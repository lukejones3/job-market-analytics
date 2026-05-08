#!/usr/bin/env python3
"""
validate_and_clean_ashby_rows.py

For every row in discovered_companies where:
  - ats_source = 'ashby'
  - last_had_roles IS NULL
  - first_seen_at < NOW() - INTERVAL '14 days'

1. Probe the Ashby posting API for each board_token.
2. If the board returns 1+ US jobs: KEEP (stamp last_had_roles).
3. If the board returns 0 US jobs OR 404: DELETE.
4. 5xx / timeout: HTML fallback via jobs.ashbyhq.com (__NEXT_DATA__ parse).
5. 429 exhausted or all fallbacks fail: SKIP (leave row unmodified, retry next run).
6. Flush to DB every 25 rows.

Rate limiting: Ashby rate-limits aggressively.
  - 10 concurrent max (global)
  - 1 req/s to api.ashbyhq.com (enforced via lock)
  - 429 backoff: 5s → 10s → 20s → skip (no delete on exhaustion)

Primary probe:  GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
HTML fallback:  GET https://jobs.ashbyhq.com/{slug}  (parse __NEXT_DATA__ JSON)
US detection:   locationName field on each job posting

Usage:
    python python/validate_and_clean_ashby_rows.py            # dry run
    python python/validate_and_clean_ashby_rows.py --apply    # write to DB
"""

import argparse
import asyncio
import json
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

CONCURRENCY    = 10     # Ashby rate-limits aggressively
PER_HOST_LIMIT = 1      # max 1 concurrent connection to api.ashbyhq.com
MIN_REQ_GAP    = 1.0    # minimum seconds between requests to Ashby's API
FLUSH_EVERY    = 25
HTTP_TIMEOUT   = aiohttp.ClientTimeout(total=30)
MAX_RETRIES    = 3
BASE_BACKOFF   = 5.0    # 5s → 10s → 20s → skip

_API_HEADERS = {
    "Accept":     "application/json",
    "User-Agent": "job-market-analytics/1.0 (research; contact: jones31luke@gmail.com)",
}
_HTML_HEADERS = {
    "Accept":     "text/html,application/xhtml+xml",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
}

# ── Per-host throttle ─────────────────────────────────────────────────────────

class _HostThrottle:
    """Enforces a minimum gap between requests to a single host."""

    def __init__(self, gap: float):
        self._gap  = gap
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now  = asyncio.get_event_loop().time()
            wait = self._gap - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_event_loop().time()


# ── US location detection ─────────────────────────────────────────────────────

_US_STATE_RE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|"
    r"MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|"
    r"VT|VA|WA|WV|WI|WY|DC)\b"
)
_US_EXPLICIT_RE = re.compile(r"United States|USA\b|U\.S\.A\.|U\.S\.", re.I)
_NEXT_DATA_RE   = re.compile(
    r'<script[^>]*id=["\']__NEXT_DATA__["\'][^>]*>(.+?)</script>', re.S
)


def _is_us_location(location: str) -> bool:
    return bool(_US_STATE_RE.search(location) or _US_EXPLICIT_RE.search(location))


def _is_us_ashby_job(job: dict) -> bool:
    """Check primary location, structured address, and secondaryLocations."""
    if _is_us_location(job.get("location", "") or ""):
        return True
    postal = (job.get("address") or {}).get("postalAddress") or {}
    if (postal.get("addressCountry") or "").upper() in ("USA", "UNITED STATES", "US"):
        return True
    for sec in (job.get("secondaryLocations") or []):
        if _is_us_location(sec.get("location", "") or ""):
            return True
        sec_postal = (sec.get("address") or {}).get("postalAddress") or {}
        if (sec_postal.get("addressCountry") or "").upper() in ("USA", "UNITED STATES", "US"):
            return True
    return False


def _count_us_jobs_api(jobs: List[dict]) -> int:
    return sum(1 for j in jobs if _is_us_ashby_job(j))


def _count_us_jobs_html(html: str) -> int:
    """
    Parse __NEXT_DATA__ from Ashby job board HTML and count US jobs.
    Conservative: only counts if we can parse and find location fields.
    """
    m = _NEXT_DATA_RE.search(html)
    if not m:
        return 0
    try:
        data = json.loads(m.group(1))
        raw  = json.dumps(data)
        locs = re.findall(r'"location"\s*:\s*"([^"]+)"', raw)
        return sum(1 for loc in locs if _is_us_location(loc))
    except Exception:
        return 0


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
    cur  = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        SELECT company_id, company_name, board_token, discovery_source, first_seen_at
        FROM discovered_companies
        WHERE ats_source = 'ashby'
          AND last_had_roles IS NULL
          AND first_seen_at < NOW() - INTERVAL '14 days'
        ORDER BY first_seen_at
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ── HTTP fetch ────────────────────────────────────────────────────────────────

async def _fetch_api(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    url: str,
    throttle: _HostThrottle,
) -> Tuple[Optional[int], Optional[object]]:
    """
    GET url with Ashby throttling + 429 backoff.
    Returns (status, body) or (None, None) on terminal failure.
    """
    backoff = BASE_BACKOFF
    for attempt in range(MAX_RETRIES + 1):
        wait_secs: Optional[float] = None
        try:
            async with semaphore:
                await throttle.acquire()
                async with session.get(
                    url, headers=_API_HEADERS, timeout=HTTP_TIMEOUT
                ) as resp:
                    status = resp.status
                    if status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_secs   = min(float(retry_after) if retry_after else backoff, 60.0)
                    elif status == 200:
                        body = await resp.json(content_type=None)
                        return status, body
                    else:
                        return status, None
            if wait_secs is not None and attempt < MAX_RETRIES:
                log.debug(f"  429 on {url} — sleeping {wait_secs:.0f}s")
                await asyncio.sleep(wait_secs)
                backoff = min(backoff * 2, 30.0)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
    return None, None


async def _fetch_html(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    url: str,
) -> Tuple[Optional[int], Optional[str]]:
    """Fetch HTML page (jobs.ashbyhq.com fallback). No throttle — different host."""
    try:
        async with semaphore:
            async with session.get(
                url, headers=_HTML_HEADERS, timeout=HTTP_TIMEOUT
            ) as resp:
                if resp.status == 200:
                    return 200, await resp.text()
                return resp.status, None
    except (aiohttp.ClientError, asyncio.TimeoutError):
        return None, None


def _normalize_token(board_token: str) -> str:
    return board_token.strip().strip("/").split("/")[0].split("?")[0].strip()


# ── Ashby probe ───────────────────────────────────────────────────────────────

async def probe_ashby(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    board_token: str,
    throttle: _HostThrottle,
) -> Tuple[int, str]:
    """
    Returns (us_job_count, method).
    method ∈ {'api', 'html', 'none', 'skip'}.
    'skip' means rate-limited/unreachable — row left unmodified.
    """
    slug = _normalize_token(board_token)
    if not slug:
        return 0, "none"

    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    status, data = await _fetch_api(session, semaphore, api_url, throttle)

    if status == 200 and isinstance(data, dict):
        jobs     = data.get("jobs") or []
        us_count = _count_us_jobs_api(jobs)
        return us_count, "api"

    if status == 404:
        return 0, "none"

    if status is None:
        # API unreachable (429 exhausted or timeout) → signal skip
        return 0, "skip"

    # 5xx → HTML fallback via jobs.ashbyhq.com
    html_url = f"https://jobs.ashbyhq.com/{slug}"
    h_status, h_body = await _fetch_html(session, semaphore, html_url)
    if h_status == 200 and h_body:
        us_count = _count_us_jobs_html(h_body)
        if us_count > 0:
            return us_count, "html"
    if h_status == 404:
        return 0, "none"

    return 0, "none"


# ── Row validation ────────────────────────────────────────────────────────────

async def validate_row(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    row: Dict,
    throttle: _HostThrottle,
) -> Dict:
    us_jobs, method = await probe_ashby(session, semaphore, row["board_token"], throttle)
    if method == "skip":
        action = "skip"
    else:
        action = "keep" if us_jobs > 0 else "delete"
    return {
        "action":       action,
        "company_id":   row["company_id"],
        "company_name": row["company_name"],
        "board_token":  row["board_token"],
        "us_jobs":      us_jobs,
        "method":       method,
    }


# ── DB flush ──────────────────────────────────────────────────────────────────

def flush_batch(batch: List[Dict], apply: bool) -> Tuple[int, int, int]:
    """Write keeps and deletes. Returns (kept, deleted, skipped)."""
    kept = deleted = skipped = 0

    if not apply:
        for r in batch:
            if r["action"] == "keep":
                log.info(
                    f"  [DRY] KEEP  {r['company_name']:35} "
                    f"{r['board_token']:30}  ({r['us_jobs']} US jobs via {r['method']})"
                )
                kept += 1
            elif r["action"] == "delete":
                log.info(
                    f"  [DRY] DEL   {r['company_name']:35} "
                    f"{r['board_token']:30}  (0 US jobs / {r['method']})"
                )
                deleted += 1
            else:
                log.info(
                    f"  [DRY] SKIP  {r['company_name']:35} "
                    f"{r['board_token']:30}  (rate-limited / unreachable)"
                )
                skipped += 1
        return kept, deleted, skipped

    conn = get_conn()
    cur  = conn.cursor()
    try:
        for r in batch:
            if r["action"] == "skip":
                log.info(f"  ⏭  SKIP   {r['company_name']:35} {r['board_token']} (rate-limited)")
                skipped += 1
                continue
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

    return kept, deleted, skipped


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(apply: bool) -> None:
    rows = load_rows()
    log.info(f"Loaded {len(rows)} rows to validate")
    if not rows:
        log.info("Nothing to do.")
        return

    throttle  = _HostThrottle(MIN_REQ_GAP)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY,
        limit_per_host=PER_HOST_LIMIT,
        enable_cleanup_closed=True,
    )

    total_kept    = 0
    total_deleted = 0
    total_skipped = 0
    keep_sample:  List[Dict] = []
    del_sample:   List[Dict] = []
    pending:      List[Dict] = []
    done = 0
    t_start = time.monotonic()

    async with aiohttp.ClientSession(connector=connector) as session:
        tasks = [validate_row(session, semaphore, row, throttle) for row in rows]

        for coro in asyncio.as_completed(tasks):
            result = await coro
            pending.append(result)
            done += 1

            if done % 10 == 0 or done == len(rows):
                elapsed = time.monotonic() - t_start
                log.info(
                    f"  Progress: {done}/{len(rows)} rows validated "
                    f"({elapsed:.0f}s elapsed)"
                )

            if len(pending) >= FLUSH_EVERY:
                k, d, s = flush_batch(pending, apply)
                total_kept    += k
                total_deleted += d
                total_skipped += s
                for r in pending:
                    if r["action"] == "keep"   and len(keep_sample) < 10:
                        keep_sample.append(r)
                    if r["action"] == "delete" and len(del_sample)  < 10:
                        del_sample.append(r)
                pending.clear()

    if pending:
        k, d, s = flush_batch(pending, apply)
        total_kept    += k
        total_deleted += d
        total_skipped += s
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
    log.info(f"  Skipped        : {total_skipped}  (rate-limited, left unmodified)")
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
        description="Validate and clean garbage Ashby rows in discovered_companies."
    )
    ap.add_argument("--apply", action="store_true", help="Write changes to DB")
    args = ap.parse_args()

    if not args.apply:
        log.info("DRY RUN — add --apply to commit updates and deletes")

    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
