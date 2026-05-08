#!/usr/bin/env python3
"""
validate_and_clean_greenhouse_rows.py

For every row in discovered_companies where:
  - ats_source = 'greenhouse'
  - last_had_roles IS NULL
  - first_seen_at < NOW() - INTERVAL '14 days'

1. Probe the Greenhouse Jobs Board API for each board_token.
2. If the board returns 1+ US jobs: KEEP (stamp last_had_roles).
3. If the board returns 0 US jobs OR 404: DELETE.
4. API 5xx/timeout: HTML fallback, then DELETE if still no US signal.
5. Flush to DB every 25 rows.

Primary probe:  GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs
HTML fallback:  GET https://boards.greenhouse.io/{slug}  (only on API 5xx/timeout)

Usage:
    python python/validate_and_clean_greenhouse_rows.py            # dry run
    python python/validate_and_clean_greenhouse_rows.py --apply    # write to DB
"""

import argparse
import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

CONCURRENCY    = 100
PER_HOST_LIMIT = 10
FLUSH_EVERY    = 25
HTTP_TIMEOUT   = aiohttp.ClientTimeout(total=20)
MAX_RETRIES    = 4
BASE_BACKOFF   = 1.0   # seconds; doubles on each 429/error retry

_HEADERS = {
    "Accept":     "application/json",
    "User-Agent": "job-market-analytics/1.0 (research; contact: jones31luke@gmail.com)",
}

# ── US location detection ─────────────────────────────────────────────────────

_US_STATE_RE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|"
    r"MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|"
    r"VT|VA|WA|WV|WI|WY|DC)\b"
)
_US_EXPLICIT_RE = re.compile(r"United States|USA\b|U\.S\.A\.|U\.S\.", re.I)

# For HTML: `, TX` pattern is specific to location strings
_US_LOCATION_IN_HTML_RE = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|"
    r"MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|"
    r"VT|VA|WA|WV|WI|WY|DC)\b"
)


def _is_us_location(location: str) -> bool:
    return bool(_US_STATE_RE.search(location) or _US_EXPLICIT_RE.search(location))


def _has_us_jobs_in_html(html: str) -> bool:
    """
    Conservative signal (only used on API 5xx/timeout fallback).
    Requires ≥3 DISTINCT ', ST' location patterns OR ≥1 explicit 'United States'.
    Prefers false-negative (delete) over false-positive (keep garbage).
    """
    state_matches   = _US_LOCATION_IN_HTML_RE.findall(html)
    distinct_states = len({m.strip(", ").strip() for m in state_matches})
    explicit_hits   = len(_US_EXPLICIT_RE.findall(html))
    return distinct_states >= 3 or explicit_hits >= 1


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def load_rows() -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        SELECT company_id, company_name, board_token, discovery_source, first_seen_at
        FROM discovered_companies
        WHERE ats_source = 'greenhouse'
          AND last_had_roles IS NULL
          AND first_seen_at < NOW() - INTERVAL '14 days'
        ORDER BY first_seen_at
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ── HTTP fetch with 429 backoff ───────────────────────────────────────────────

async def _fetch(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    url: str,
    want_text: bool = False,
) -> Tuple[Optional[int], Optional[object]]:
    """
    GET url. Returns (status, body) or (None, None) on terminal failure.
    Semaphore is released before sleeping on 429 so other tasks aren't starved.
    """
    backoff = BASE_BACKOFF
    for attempt in range(MAX_RETRIES + 1):
        wait_secs: Optional[float] = None
        try:
            async with semaphore:
                async with session.get(
                    url, headers=_HEADERS, timeout=HTTP_TIMEOUT
                ) as resp:
                    status = resp.status
                    if status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_secs = min(float(retry_after) if retry_after else backoff, 60.0)
                    elif status == 200:
                        body = await resp.text() if want_text else await resp.json(content_type=None)
                        return status, body
                    else:
                        return status, None
            if wait_secs is not None and attempt < MAX_RETRIES:
                await asyncio.sleep(wait_secs)
                backoff *= 2
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= 2
    return None, None


def _normalize_token(board_token: str) -> str:
    return board_token.strip().strip("/").split("/")[0].split("?")[0].strip()


# ── Greenhouse probe ──────────────────────────────────────────────────────────

async def probe_greenhouse(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    board_token: str,
) -> Tuple[int, str]:
    """
    Returns (us_job_count, method).
    method ∈ {'api', 'html', 'none'}.
    """
    slug = _normalize_token(board_token)
    if not slug:
        return 0, "none"

    api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    status, data = await _fetch(session, semaphore, api_url)

    if status == 200 and isinstance(data, dict):
        jobs = data.get("jobs", [])
        us_count = sum(
            1 for j in jobs
            if _is_us_location(j.get("location", {}).get("name", ""))
        )
        return us_count, "api"

    if status == 404:
        return 0, "none"   # definitively gone

    # 5xx / timeout → conservative HTML fallback
    html_url = f"https://boards.greenhouse.io/{slug}"
    h_status, h_body = await _fetch(session, semaphore, html_url, want_text=True)
    if h_status == 200 and isinstance(h_body, str) and _has_us_jobs_in_html(h_body):
        return 1, "html"

    return 0, "none"


# ── Row validation ────────────────────────────────────────────────────────────

async def validate_row(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    row: Dict,
) -> Dict:
    us_jobs, method = await probe_greenhouse(session, semaphore, row["board_token"])
    return {
        "action":       "keep" if us_jobs > 0 else "delete",
        "company_id":   row["company_id"],
        "company_name": row["company_name"],
        "board_token":  row["board_token"],
        "us_jobs":      us_jobs,
        "method":       method,
    }


# ── DB flush ──────────────────────────────────────────────────────────────────

def flush_batch(batch: List[Dict], apply: bool) -> Tuple[int, int]:
    """Write keeps and deletes. Returns (kept, deleted)."""
    kept = deleted = 0

    if not apply:
        for r in batch:
            if r["action"] == "keep":
                log.info(
                    f"  [DRY] KEEP  {r['company_name']:35} "
                    f"{r['board_token']:30}  ({r['us_jobs']} US jobs via {r['method']})"
                )
                kept += 1
            else:
                log.info(
                    f"  [DRY] DEL   {r['company_name']:35} "
                    f"{r['board_token']:30}  (0 US jobs / {r['method']})"
                )
                deleted += 1
        return kept, deleted

    conn = get_conn()
    cur = conn.cursor()
    try:
        for r in batch:
            try:
                cur.execute("SAVEPOINT sp")
                if r["action"] == "keep":
                    cur.execute(
                        """
                        UPDATE discovered_companies
                        SET last_seen_at   = now(),
                            last_had_roles = now()
                        WHERE company_id = %s
                        """,
                        (r["company_id"],),
                    )
                    cur.execute("RELEASE SAVEPOINT sp")
                    log.info(
                        f"  ✅ KEPT   {r['company_name']:35} "
                        f"{r['board_token']}  ({r['us_jobs']} US jobs via {r['method']})"
                    )
                    kept += 1
                else:
                    cur.execute(
                        "DELETE FROM discovered_companies WHERE company_id = %s",
                        (r["company_id"],),
                    )
                    cur.execute("RELEASE SAVEPOINT sp")
                    log.info(f"  🗑  DELETED {r['company_name']:35} {r['board_token']}")
                    deleted += 1
            except Exception as row_err:
                cur.execute("ROLLBACK TO SAVEPOINT sp")
                log.warning(f"  !! Skip {r['company_name']}: {row_err}")
        conn.commit()
    except Exception as e:
        log.error(f"  DB flush error (outer): {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

    return kept, deleted


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(apply: bool) -> None:
    rows = load_rows()
    log.info(f"Loaded {len(rows)} rows to validate")
    if not rows:
        log.info("Nothing to do.")
        return

    semaphore = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY,
        limit_per_host=PER_HOST_LIMIT,
        enable_cleanup_closed=True,
    )

    total_kept    = 0
    total_deleted = 0
    keep_sample:  List[Dict] = []
    del_sample:   List[Dict] = []
    pending:      List[Dict] = []
    done = 0
    t_start = time.monotonic()

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [validate_row(session, semaphore, row) for row in rows]

        for coro in asyncio.as_completed(tasks):
            result = await coro
            pending.append(result)
            done += 1

            if done % 25 == 0 or done == len(rows):
                elapsed = time.monotonic() - t_start
                log.info(
                    f"  Progress: {done}/{len(rows)} rows validated "
                    f"({elapsed:.0f}s elapsed)"
                )

            if len(pending) >= FLUSH_EVERY:
                k, d = flush_batch(pending, apply)
                total_kept    += k
                total_deleted += d
                for r in pending:
                    if r["action"] == "keep"   and len(keep_sample) < 10:
                        keep_sample.append(r)
                    if r["action"] == "delete" and len(del_sample)  < 10:
                        del_sample.append(r)
                pending.clear()

    if pending:
        k, d = flush_batch(pending, apply)
        total_kept    += k
        total_deleted += d
        for r in pending:
            if r["action"] == "keep"   and len(keep_sample) < 10:
                keep_sample.append(r)
            if r["action"] == "delete" and len(del_sample)  < 10:
                del_sample.append(r)

    elapsed = time.monotonic() - t_start

    log.info("")
    log.info("=" * 65)
    log.info("RESULTS")
    log.info("=" * 65)
    log.info(f"  Total examined : {len(rows)}")
    log.info(f"  Valid / kept   : {total_kept}")
    log.info(f"  Invalid / del  : {total_deleted}")
    log.info(f"  Elapsed        : {elapsed:.0f}s")
    log.info("")
    if keep_sample:
        log.info("  Sample valid boards (keep):")
        for r in keep_sample:
            log.info(
                f"    {r['company_name']:35} "
                f"{r['board_token']:30}  ({r['us_jobs']} US jobs via {r['method']})"
            )
    if del_sample:
        log.info("")
        log.info("  Sample deleted boards:")
        for r in del_sample:
            log.info(
                f"    {r['company_name']:35} "
                f"{r['board_token']:30}  (0 US jobs / {r['method']})"
            )
    if not apply:
        log.info("")
        log.info("  DRY RUN — re-run with --apply to write changes")


def main():
    ap = argparse.ArgumentParser(
        description="Validate and clean garbage Greenhouse rows in discovered_companies."
    )
    ap.add_argument("--apply", action="store_true", help="Write changes to DB")
    args = ap.parse_args()

    if not args.apply:
        log.info("DRY RUN — add --apply to commit updates and deletes")

    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
