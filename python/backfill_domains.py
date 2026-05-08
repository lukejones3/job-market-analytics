#!/usr/bin/env python3
"""
Backfill job_postings.domain / domain_secondary / domain_classified_at
for all rows where domain IS NULL.

Usage:
    python python/backfill_domains.py              # dry-run (default)
    python python/backfill_domains.py --apply      # write to DB
    python python/backfill_domains.py --apply --llm   # enable LLM fallback
    python python/backfill_domains.py --apply --limit 100   # test on 100 rows
"""

import os
import sys
import argparse
import logging
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor, execute_values

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_domain import build_alias_map, classify_domain

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")
log = logging.getLogger(__name__)

BATCH_SIZE = 500


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", 5432),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def fetch_unclassified(conn, limit):
    # limit: int or None
    q = """
        SELECT jp.job_id, r.role_name, jp.description_text
        FROM job_postings jp
        LEFT JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.domain IS NULL
        ORDER BY jp.ingested_at DESC
    """
    if limit:
        q += f" LIMIT {int(limit)}"
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute(q)
        return [dict(r) for r in cur.fetchall()]


def apply_batch(conn, rows, now: datetime):
    """Batch UPDATE domain fields for a list of (domain, secondary, job_id)."""
    with conn.cursor() as cur:
        for domain, secondary, job_id in rows:
            cur.execute(
                """
                UPDATE job_postings
                SET domain               = %s,
                    domain_secondary     = %s,
                    domain_classified_at = %s
                WHERE job_id = %s
                """,
                (domain, secondary if secondary else None, now, job_id),
            )


def main():
    parser = argparse.ArgumentParser(description="Backfill domain on job_postings")
    parser.add_argument("--apply",  action="store_true", help="Write to DB (default: dry-run)")
    parser.add_argument("--llm",    action="store_true", help="Enable LLM fallback for ambiguous titles")
    parser.add_argument("--limit",  type=int, default=None, help="Cap rows processed (for testing)")
    args = parser.parse_args()

    conn = get_conn()

    print(f"Loading alias map from DB...")
    alias_map = build_alias_map(conn)
    print(f"  {len(alias_map)} skill aliases loaded")

    print(f"Fetching unclassified jobs...")
    jobs = fetch_unclassified(conn, args.limit)
    total = len(jobs)
    print(f"  {total} jobs to classify\n")

    if not jobs:
        print("Nothing to do.")
        conn.close()
        return

    # ── Classify ──────────────────────────────────────────────────────────────
    method_counts   = defaultdict(int)
    vertical_counts = defaultdict(int)
    llm_cache       = {}
    now = datetime.now(timezone.utc)

    pending:  list[tuple] = []
    dot_every = max(1, total // 50)

    for i, job in enumerate(jobs, 1):
        title       = job.get("role_name") or ""
        description = job.get("description_text") or ""
        job_id      = job["job_id"]

        domain, secondary, method = classify_domain(
            title, description, alias_map,
            use_llm=args.llm, llm_cache=llm_cache,
        )

        method_counts[method]     += 1
        vertical_counts[domain]   += 1
        pending.append((domain, secondary, job_id))

        if i % dot_every == 0:
            pct = i * 100 // total
            print(f"  {i}/{total} ({pct}%) — methods so far: "
                  + ", ".join(f"{m}={c}" for m, c in sorted(method_counts.items())))

        if args.apply and len(pending) >= BATCH_SIZE:
            apply_batch(conn, pending, now)
            conn.commit()
            pending.clear()

    # Flush remainder
    if args.apply and pending:
        apply_batch(conn, pending, now)
        conn.commit()

    # ── Report ─────────────────────────────────────────────────────────────────
    print(f"\n{'DRY RUN' if not args.apply else 'APPLIED'} — {total} jobs processed\n")

    print("By method:")
    for method, count in sorted(method_counts.items(), key=lambda x: -x[1]):
        pct = count * 100 // total
        print(f"  {method:<12s}  {count:>6}  ({pct}%)")

    print("\nBy domain:")
    for domain, count in sorted(vertical_counts.items(), key=lambda x: -x[1]):
        pct = count * 100 // total
        print(f"  {domain:<14s}  {count:>6}  ({pct}%)")

    if not args.apply:
        print(f"\n(Dry run — pass --apply to write {total} updates)")

    conn.close()


if __name__ == "__main__":
    main()
