#!/usr/bin/env python3
"""
One-time backfill: fetch descriptions for empty-description Workday rows.

Target: ~13,736 raw Workday jobs whose description was never fetched (timed out
or rate-limited during original ingest). Excluded from scope:
  - PayPal:                   permanent S22/403 on detail endpoint (63 rows)
  - bbinsurance, raymondjames: wrong board config, listing 404s (178 rows)

Concurrency model: 5 tenants processed in parallel, sequential within each
tenant, 0.5–1.5s random jitter between requests. 403 → up to 3 retries with
5/15/30s backoff (transient rate-limiting, not a hard block).

Timeout: sock_connect=10, sock_read=20 — matches Phase 1 fix in ingest_jobs.py.

Usage:
    # Dry-run — confirm target row count, do NOT fetch or write
    python python/backfill_wd_descriptions.py

    # Test run — first 150 rows across a sample of tenants, write to DB
    python python/backfill_wd_descriptions.py --apply --limit 150

    # Full run
    python python/backfill_wd_descriptions.py --apply
"""

import argparse
import asyncio
import itertools
import logging
import os
import random
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import aiohttp
import psycopg2
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
log = logging.getLogger(__name__)

# ── Concurrency / retry config ────────────────────────────────────────────────
CONCURRENT_TENANTS = 5       # tenants in-flight simultaneously
JITTER_MIN         = 0.5     # seconds between requests within a tenant
JITTER_MAX         = 1.5
RETRY_DELAYS       = [5, 15, 30]  # backoff seconds after each 403 (3 retries)
PROGRESS_EVERY     = 100     # log progress every N rows attempted
SAVE_EVERY         = 500     # commit to DB every N successes

WD_TIMEOUT = aiohttp.ClientTimeout(sock_connect=10, sock_read=20)

# Rotate UAs matching the harvester pool
_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
]
_UA_CYCLE = itertools.cycle(_USER_AGENTS)

# Excluded from scope — see docstring
EXCLUDED_URL_PATTERNS = ("%paypal%", "%bbinsurance%", "%raymondjames%")


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.environ['PGHOST'],
        port=os.environ.get('PGPORT', '5432'),
        dbname=os.environ['PGDATABASE'],
        user=os.environ['PGUSER'],
        password=os.environ['PGPASSWORD'],
    )


def load_target_rows(conn, limit: Optional[int]) -> List[Tuple[str, str]]:
    """Return [(job_id, job_url)] for all empty-desc Workday rows in scope."""
    sql = """
        SELECT job_id, job_url
        FROM job_postings
        WHERE source = 'workday'
          AND status = 'raw'
          AND data_tier = 1
          AND (description_text = '' OR description_text IS NULL)
          AND job_url NOT LIKE %s
          AND job_url NOT LIKE %s
          AND job_url NOT LIKE %s
        ORDER BY ingested_at ASC
    """
    params: list = list(EXCLUDED_URL_PATTERNS)
    if limit:
        sql += " LIMIT %s"
        params.append(limit)
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def flush_updates(conn, updates: List[Tuple]) -> None:
    """Write a batch of (desc, posted_date, workplace_type, job_id) to DB."""
    with conn.cursor() as cur:
        execute_batch(
            cur,
            """
            UPDATE job_postings
            SET   description_text = %s,
                  posted_date      = COALESCE(%s, posted_date),
                  workplace_type   = COALESCE(%s, workplace_type)
            WHERE job_id = %s
              AND (description_text = '' OR description_text IS NULL)
            """,
            updates,
            page_size=200,
        )
    conn.commit()


# ── URL helpers ───────────────────────────────────────────────────────────────

def parse_tenant(job_url: str) -> Optional[str]:
    m = re.match(r'https://([a-z0-9_\-]+)\.wd\d+\.myworkdayjobs\.com', job_url)
    return m.group(1) if m else None


def job_url_to_cxs(job_url: str) -> Optional[str]:
    """
    Convert stored en-US job URL to CXS detail API URL.

    Stored:  https://{tenant}.wdN.myworkdayjobs.com/en-US/{board}/job/...
    Returns: https://{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{board}/job/...
    """
    m = re.match(
        r'(https://([a-z0-9_\-]+)\.wd\d+\.myworkdayjobs\.com)/en-US/([^/]+)/(.+)',
        job_url,
    )
    if not m:
        return None
    base, tenant, board, ext_path = m.groups()
    return f"{base}/wday/cxs/{tenant}/{board}/{ext_path}"


def parse_remote_type(s: str) -> Optional[str]:
    if not s:
        return None
    sl = s.lower()
    if "remote" in sl:
        return "remote"
    if "hybrid" in sl:
        return "hybrid"
    return "onsite"


def parse_workday_date(s: str) -> Optional[str]:
    if not s:
        return None
    try:
        return str(datetime.strptime(s.strip(), "%Y-%m-%d").date())
    except (ValueError, TypeError):
        return None


# ── Fetch ─────────────────────────────────────────────────────────────────────

async def fetch_detail(
    session: aiohttp.ClientSession,
    job_url: str,
    cxs_url: str,
) -> Optional[Tuple[str, Optional[str], Optional[str]]]:
    """
    GET the CXS detail endpoint. Returns (description, posted_date, workplace_type)
    on success, or None on failure.

    403 → retry with 5/15/30s backoff (transient rate-limiting).
    Other non-200 → None immediately.
    """
    ua = next(_UA_CYCLE)
    headers = {
        "User-Agent": ua,
        "Content-Type": "application/json",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": job_url,
    }

    max_attempts = 1 + len(RETRY_DELAYS)
    for attempt in range(max_attempts):
        if attempt > 0:
            wait = RETRY_DELAYS[attempt - 1]
            log.debug(f"  retry {attempt}/{len(RETRY_DELAYS)} after {wait}s — {cxs_url}")
            await asyncio.sleep(wait)
        try:
            async with session.get(cxs_url, headers=headers) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    info = data.get("jobPostingInfo", {})
                    desc = (
                        info.get("jobDescription", "")
                        or data.get("jobDescription", "")
                        or data.get("description", "")
                        or ""
                    )
                    desc = re.sub(r"<[^>]+>", " ", desc)
                    desc = re.sub(r"\s+", " ", desc).strip()
                    posted = parse_workday_date(info.get("startDate", ""))
                    remote = parse_remote_type(info.get("remoteType", ""))
                    return desc, posted, remote

                elif r.status == 403:
                    if attempt < len(RETRY_DELAYS):
                        continue  # backoff handled at top of loop
                    log.warning(f"  403 after {max_attempts} attempts: {cxs_url}")
                    return None

                else:
                    body = (await r.text())[:200]
                    log.warning(f"  HTTP {r.status}: {cxs_url} — {body}")
                    return None

        except asyncio.TimeoutError:
            if attempt < len(RETRY_DELAYS):
                continue
            log.warning(f"  timeout after {max_attempts} attempts: {cxs_url}")
            return None
        except Exception as exc:
            log.warning(f"  {type(exc).__name__}: {cxs_url} — {exc}")
            return None

    return None


# ── Tenant worker ─────────────────────────────────────────────────────────────

async def process_tenant(
    session: aiohttp.ClientSession,
    tenant: str,
    rows: List[Tuple[str, str]],
    stats: Dict,
    pending_updates: List[Tuple],
    pending_lock: asyncio.Lock,
) -> None:
    """Process one tenant sequentially with jitter between requests."""
    for job_id, job_url in rows:
        cxs_url = job_url_to_cxs(job_url)
        if not cxs_url:
            log.warning(f"Cannot derive CXS URL: {job_url}")
            async with pending_lock:
                stats['failed'] += 1
                stats['done'] += 1
            continue

        result = await fetch_detail(session, job_url, cxs_url)

        async with pending_lock:
            stats['done'] += 1
            if result is not None:
                desc, posted, remote = result
                if desc:
                    pending_updates.append((desc, posted, remote, job_id))
                    stats['succeeded'] += 1
                else:
                    # 200 but no description text in response
                    stats['empty_200'] += 1
            else:
                stats['failed'] += 1
                log.warning("FAILED | tenant=%-30s | %s", tenant, job_url)
                stats['failures'].append((tenant, job_url))

            if stats['done'] % PROGRESS_EVERY == 0:
                total = stats['total']
                done = stats['done']
                log.info(
                    f"Progress {done}/{total} "
                    f"({100*done//total}%) — "
                    f"succeeded={stats['succeeded']} "
                    f"failed={stats['failed']} "
                    f"empty_200={stats['empty_200']} "
                    f"pending_write={len(pending_updates)}"
                )

        # Jitter between requests within this tenant
        await asyncio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))


# ── Main async driver ─────────────────────────────────────────────────────────

async def run_backfill(
    rows: List[Tuple[str, str]],
    apply: bool,
    conn,
) -> Dict:
    by_tenant: Dict[str, List] = defaultdict(list)
    for job_id, job_url in rows:
        t = parse_tenant(job_url) or "unknown"
        by_tenant[t].append((job_id, job_url))

    log.info(f"Tenants to process: {len(by_tenant)}")
    if len(by_tenant) <= 20:
        for t, trows in sorted(by_tenant.items()):
            log.info(f"  {t}: {len(trows)} rows")

    stats: Dict = {
        'total': len(rows),
        'done': 0,
        'succeeded': 0,
        'failed': 0,
        'empty_200': 0,
        'failures': [],   # list of (tenant, job_url) for all failed rows
    }
    pending_updates: List[Tuple] = []
    pending_lock = asyncio.Lock()

    connector = aiohttp.TCPConnector(limit=CONCURRENT_TENANTS * 4, limit_per_host=2)
    async with aiohttp.ClientSession(connector=connector, timeout=WD_TIMEOUT) as session:
        tenant_batches = [
            list(by_tenant.items())[i:i + CONCURRENT_TENANTS]
            for i in range(0, len(by_tenant), CONCURRENT_TENANTS)
        ]

        for batch_idx, batch in enumerate(tenant_batches):
            tasks = [
                process_tenant(session, t, t_rows, stats, pending_updates, pending_lock)
                for t, t_rows in batch
            ]
            await asyncio.gather(*tasks)

            # Flush completed updates to DB every SAVE_EVERY successes
            async with pending_lock:
                if apply and len(pending_updates) >= SAVE_EVERY:
                    to_write = pending_updates[:]
                    pending_updates.clear()
                    flush_updates(conn, to_write)
                    log.info(
                        f"  Saved {len(to_write)} rows to DB "
                        f"(batch {batch_idx+1}/{len(tenant_batches)})"
                    )

    # Final flush
    if apply and pending_updates:
        flush_updates(conn, pending_updates)
        log.info(f"  Final flush: saved {len(pending_updates)} rows to DB")

    return stats


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--apply', action='store_true',
                    help='Write descriptions to DB (default: dry-run)')
    ap.add_argument('--limit', type=int, default=None,
                    help='Process at most N rows — use 150 for the Gate 2 test run')
    args = ap.parse_args()

    conn = get_conn()
    rows = load_target_rows(conn, limit=args.limit)

    log.info(
        f"Target rows: {len(rows):,} "
        f"(excluded: paypal, bbinsurance, raymondjames)"
        f"{f' [limited to {args.limit}]' if args.limit else ''}"
    )

    if not args.apply:
        log.info("Dry-run — pass --apply to fetch and write. Exiting.")
        conn.close()
        return

    log.info(
        f"Starting backfill | apply=True | "
        f"concurrent_tenants={CONCURRENT_TENANTS} | "
        f"jitter={JITTER_MIN}-{JITTER_MAX}s | "
        f"retry_delays={RETRY_DELAYS}s"
    )

    stats = asyncio.run(run_backfill(rows, apply=True, conn=conn))

    log.info("=" * 60)
    log.info("BACKFILL COMPLETE")
    log.info(f"  Total attempted : {stats['total']:,}")
    log.info(f"  Succeeded       : {stats['succeeded']:,}  "
             f"({100*stats['succeeded']//max(stats['total'],1)}%)")
    log.info(f"  Failed          : {stats['failed']:,}")
    log.info(f"  Empty on 200    : {stats['empty_200']:,}")
    log.info("=" * 60)

    if stats['failures']:
        from collections import Counter
        by_tenant = Counter(tenant for tenant, _ in stats['failures'])
        log.info("FAILURES BY TENANT (%d tenants, %d rows):", len(by_tenant), stats['failed'])
        for tenant, count in sorted(by_tenant.items(), key=lambda x: -x[1]):
            log.info("  %-40s %d rows", tenant, count)
            for t, url in stats['failures']:
                if t == tenant:
                    log.info("    %s", url)

    conn.close()


if __name__ == '__main__':
    main()
