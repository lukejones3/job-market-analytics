#!/usr/bin/env python3
"""
validate_and_clean_workday_rows.py

For every row in discovered_companies where:
  - ats_source = 'workday'
  - last_had_roles IS NULL
  - first_seen_at < NOW() - INTERVAL '14 days'

1. Generate (tenant, server, board) probe candidates from the existing board_token,
   trying multiple token interpretations + common board names across all known servers.
2. Hit the Workday CXS API for each candidate concurrently (100-way async).
3. If any candidate returns >= 5 jobs: UPDATE board_token to the correct format, keep row.
4. If no candidate succeeds: DELETE the row.
5. Flush every 25 rows so killing mid-run loses at most one flush window.

Usage:
    python python/validate_and_clean_workday_rows.py            # dry run
    python python/validate_and_clean_workday_rows.py --apply    # write to DB
"""

import argparse
import asyncio
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

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

# ── Config ───────────────────────────────────────────────────────────────────

WD_SERVERS = ["wd1", "wd5", "wd3", "wd12", "wd103", "wd501", "wd108", "wd503", "wd1480"]
MIN_JOBS   = 5          # minimum total jobs for a tenant to be considered valid
CONCURRENCY      = 100  # global HTTP semaphore
PER_HOST_LIMIT   = 4    # aiohttp connector limit per host
FLUSH_EVERY      = 25   # DB commit cadence (rows)
HTTP_TIMEOUT     = aiohttp.ClientTimeout(total=15)

_HEADERS = {
    "Content-Type": "application/json",
    "Accept":       "application/json",
    "User-Agent":   "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}
_PAYLOAD = {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""}
_WD_SERVER_RE = re.compile(r"^wd\d", re.I)

# ── DB ───────────────────────────────────────────────────────────────────────

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
        WHERE ats_source = 'workday'
          AND last_had_roles IS NULL
          AND first_seen_at < NOW() - INTERVAL '14 days'
        ORDER BY first_seen_at
    """)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ── Candidate generation ──────────────────────────────────────────────────────

def get_probe_candidates(board_token: str) -> List[Tuple[str, str, str]]:
    """
    Given an (possibly malformed) board_token, return an ordered list of
    (tenant, server, board) tuples to probe.

    Token shapes we encounter:
      0-slash  "3m"                  → bare slug
      1-slash  "paypal/jobs"         → tenant / board (server missing)
      2-slash  "geico/external/wd1"  → possibly swapped server & board
               "thermofisher/wd1/ThermoFisher_External"  → correct format
    """
    parts = board_token.strip("/").split("/")
    seen: Set[Tuple[str, str, str]] = set()
    candidates: List[Tuple[str, str, str]] = []

    def _add(t: str, s: str, b: str) -> None:
        key = (t.lower(), s.lower(), b.lower())
        if key not in seen:
            seen.add(key)
            candidates.append((t, s, b))

    slug = parts[0]

    # Common board names (tried on every server for every token shape)
    common_boards = [
        "External",
        "Careers_External",
        "Careers",
        f"{slug}_External",
        f"{slug}Careers",
    ]

    if len(parts) == 1:
        # Bare slug — no server, no board info
        for server in WD_SERVERS:
            for board in common_boards:
                _add(slug, server, board)

    elif len(parts) == 2:
        other = parts[1]
        # Try the existing second segment as the board name
        for server in WD_SERVERS:
            _add(slug, server, other)
        # Also try common boards on all servers
        for server in WD_SERVERS:
            for board in common_boards:
                _add(slug, server, board)

    else:
        # 3+ parts
        a, b, c = parts[0], parts[1], parts[2]
        b_server = bool(_WD_SERVER_RE.match(b))
        c_server = bool(_WD_SERVER_RE.match(c))

        # Best guesses first
        if b_server and not c_server:
            _add(a, b, c)          # correct order already
        if c_server and not b_server:
            _add(a, c, b)          # server/board were swapped — fix it
        if not b_server and not c_server:
            # Neither position looks like a WD server — try both orderings
            _add(a, b, c)
            _add(a, c, b)

        # Exhaustively try b and c as board names across all servers
        for server in WD_SERVERS:
            _add(a, server, b)
            _add(a, server, c)

        # Common boards
        for server in WD_SERVERS:
            for board in common_boards:
                _add(a, server, board)

    return candidates


# ── HTTP probing ─────────────────────────────────────────────────────────────

async def probe_one(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    tenant: str,
    server: str,
    board: str,
) -> int:
    """Return total job count for this (tenant, server, board), or 0 on any failure."""
    url = (
        f"https://{tenant}.{server}.myworkdayjobs.com"
        f"/wday/cxs/{tenant}/{board}/jobs"
    )
    async with semaphore:
        try:
            async with session.post(
                url, json=_PAYLOAD, headers=_HEADERS, timeout=HTTP_TIMEOUT
            ) as r:
                if r.status == 200:
                    data = await r.json(content_type=None)
                    total = data.get("total", 0) or len(data.get("jobPostings", []))
                    return int(total)
                return 0
        except Exception:
            return 0


async def validate_row(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    row: Dict,
) -> Dict:
    """
    Probe all candidates for this row concurrently.
    Returns a result dict indicating "update" (keep with corrected token) or "delete".
    """
    candidates = get_probe_candidates(row["board_token"])

    counts = await asyncio.gather(
        *[probe_one(session, semaphore, t, s, b) for t, s, b in candidates],
        return_exceptions=True,
    )

    for i, count in enumerate(counts):
        if isinstance(count, int) and count >= MIN_JOBS:
            tenant, server, board = candidates[i]
            return {
                "action":       "update",
                "company_id":   row["company_id"],
                "company_name": row["company_name"],
                "old_token":    row["board_token"],
                "new_token":    f"{tenant}/{server}/{board}",
                "total_jobs":   count,
            }

    return {
        "action":       "delete",
        "company_id":   row["company_id"],
        "company_name": row["company_name"],
        "old_token":    row["board_token"],
        "new_token":    None,
        "total_jobs":   0,
    }


# ── DB flush ─────────────────────────────────────────────────────────────────

def flush_batch(batch: List[Dict], apply: bool) -> Tuple[int, int]:
    """Write a batch of updates + deletes. Returns (updated, deleted)."""
    updated = deleted = 0

    if not apply:
        for r in batch:
            if r["action"] == "update":
                log.info(
                    f"  [DRY] KEEP  {r['company_name']:35} "
                    f"{r['old_token']} → {r['new_token']} ({r['total_jobs']} jobs)"
                )
                updated += 1
            else:
                log.info(f"  [DRY] DEL   {r['company_name']:35} {r['old_token']}")
                deleted += 1
        return updated, deleted

    conn = get_conn()
    cur = conn.cursor()
    try:
        for r in batch:
            try:
                cur.execute("SAVEPOINT sp")
                if r["action"] == "update":
                    cur.execute(
                        """
                        UPDATE discovered_companies
                        SET board_token    = %s,
                            last_seen_at   = now(),
                            last_had_roles = now()
                        WHERE company_id = %s
                        """,
                        (r["new_token"], r["company_id"]),
                    )
                    cur.execute("RELEASE SAVEPOINT sp")
                    log.info(
                        f"  ✅ KEPT   {r['company_name']:35} "
                        f"{r['old_token']} → {r['new_token']} ({r['total_jobs']} jobs)"
                    )
                    updated += 1
                else:
                    cur.execute(
                        "DELETE FROM discovered_companies WHERE company_id = %s",
                        (r["company_id"],),
                    )
                    cur.execute("RELEASE SAVEPOINT sp")
                    log.info(f"  🗑  DELETED {r['company_name']:35} {r['old_token']}")
                    deleted += 1
            except Exception as row_err:
                cur.execute("ROLLBACK TO SAVEPOINT sp")
                if r["action"] == "update" and "unique" in str(row_err).lower():
                    # Correct token already exists — just remove the garbage row
                    try:
                        cur.execute("SAVEPOINT sp2")
                        cur.execute(
                            "DELETE FROM discovered_companies WHERE company_id = %s",
                            (r["company_id"],),
                        )
                        cur.execute("RELEASE SAVEPOINT sp2")
                        log.info(
                            f"  🗑  DELETED {r['company_name']:35} {r['old_token']} "
                            f"(valid token already exists)"
                        )
                        deleted += 1
                    except Exception as del_err:
                        cur.execute("ROLLBACK TO SAVEPOINT sp2")
                        log.warning(f"  !! Could not clean {r['company_name']}: {del_err}")
                else:
                    log.warning(f"  !! Skip {r['company_name']}: {row_err}")
        conn.commit()
    except Exception as e:
        log.error(f"  DB flush error (outer): {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()

    return updated, deleted


# ── Main ─────────────────────────────────────────────────────────────────────

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

    total_updated = 0
    total_deleted = 0
    recovered_sample: List[Dict] = []
    pending: List[Dict] = []
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
                u, d = flush_batch(pending, apply)
                total_updated += u
                total_deleted += d
                for r in pending:
                    if r["action"] == "update" and len(recovered_sample) < 15:
                        recovered_sample.append(r)
                pending.clear()

    # Final flush for any remainder
    if pending:
        u, d = flush_batch(pending, apply)
        total_updated += u
        total_deleted += d
        for r in pending:
            if r["action"] == "update" and len(recovered_sample) < 15:
                recovered_sample.append(r)

    elapsed = time.monotonic() - t_start

    log.info("")
    log.info("=" * 65)
    log.info("RESULTS")
    log.info("=" * 65)
    log.info(f"  Total examined : {len(rows)}")
    log.info(f"  Recovered (kept, board_token corrected): {total_updated}")
    log.info(f"  Deleted   (no valid API endpoint found): {total_deleted}")
    log.info(f"  Elapsed   : {elapsed:.0f}s")
    log.info("")
    if recovered_sample:
        log.info("  Sample recovered tenants:")
        for r in recovered_sample:
            log.info(
                f"    {r['company_name']:35} "
                f"{r['old_token']:30} → {r['new_token']}  ({r['total_jobs']} jobs)"
            )
    if not apply:
        log.info("")
        log.info("  DRY RUN — re-run with --apply to write changes")


def main():
    ap = argparse.ArgumentParser(
        description="Validate and clean garbage Workday rows in discovered_companies."
    )
    ap.add_argument("--apply", action="store_true", help="Write changes to DB")
    args = ap.parse_args()

    if not args.apply:
        log.info("DRY RUN — add --apply to commit updates and deletes")

    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
