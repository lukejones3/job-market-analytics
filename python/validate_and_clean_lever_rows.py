#!/usr/bin/env python3
"""
validate_and_clean_lever_rows.py

For every row in discovered_companies where:
  - ats_source = 'lever'
  - last_had_roles IS NULL
  - first_seen_at < NOW() - INTERVAL '14 days'

1. Probe the Lever public postings API for each board_token.
2. If the board returns 1+ US jobs: KEEP (stamp last_had_roles).
3. If the board returns 0 US jobs OR 404/400: DELETE.
4. 5xx / timeout: DELETE (no HTML fallback for Lever).
5. Flush to DB every 25 rows.

Primary probe:  GET https://api.lever.co/v0/postings/{slug}?mode=json
US detection:   categories.location / categories.allLocations fields

Usage:
    python python/validate_and_clean_lever_rows.py            # dry run
    python python/validate_and_clean_lever_rows.py --apply    # write to DB
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
BASE_BACKOFF   = 1.0

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


def _is_us_location(location: str) -> bool:
    return bool(_US_STATE_RE.search(location) or _US_EXPLICIT_RE.search(location))


def _has_us_jobs(postings: List[dict]) -> int:
    """Return count of US postings. Checks allLocations array, falls back to location."""
    count = 0
    for job in postings:
        cats = job.get("categories", {})
        all_locs = cats.get("allLocations") or []
        if all_locs:
            if any(_is_us_location(loc) for loc in all_locs):
                count += 1
        else:
            if _is_us_location(cats.get("location", "")):
                count += 1
    return count


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
        WHERE ats_source = 'lever'
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
) -> Tuple[Optional[int], Optional[object]]:
    """GET url, return (status, json_body). Releases semaphore before sleeping on 429."""
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
                        body = await resp.json(content_type=None)
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


# ── Lever probe ───────────────────────────────────────────────────────────────

async def probe_lever(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    board_token: str,
) -> Tuple[int, str]:
    """
    Returns (us_job_count, method).
    method ∈ {'api', 'none'}.
    No HTML fallback — Lever's public board HTML lacks structured location data.
    """
    slug = _normalize_token(board_token)
    if not slug:
        return 0, "none"

    api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    status, data = await _fetch(session, semaphore, api_url)

    if status == 200 and isinstance(data, list):
        us_count = _has_us_jobs(data)
        return us_count, "api"

    # 404, 400, 5xx, or timeout → treat as dead board
    return 0, "none"


# ── Row validation ────────────────────────────────────────────────────────────

async def validate_row(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    row: Dict,
) -> Dict:
    us_jobs, method = await probe_lever(session, semaphore, row["board_token"])
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
        description="Validate and clean garbage Lever rows in discovered_companies."
    )
    ap.add_argument("--apply", action="store_true", help="Write changes to DB")
    args = ap.parse_args()

    if not args.apply:
        log.info("DRY RUN — add --apply to commit updates and deletes")

    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
