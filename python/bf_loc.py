#!/usr/bin/env python3
"""
backfill_locations.py — Run the location normalizer over every active tier-1 job
and populate the new loc_city / loc_state / loc_country columns.

Drop-as-status: jobs that normalize to country='foreign' get marked status='ignored'
so they stop showing up everywhere downstream. Logs to location_backfill_changes
for full audit + rollback.

Usage:
    python python/bf_loc.py --dry-run       # estimate, no writes
    python python/bf_loc.py                 # execute
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path("/opt/job-market-analytics")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python"))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from python.location_normalizer import normalize_location  # noqa: E402

BATCH_TAG = f"loc_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def get_conn():
    return psycopg2.connect(
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"],
        port=int(os.environ["PGPORT"]),
    )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS location_backfill_changes (
    id SERIAL PRIMARY KEY,
    job_id TEXT NOT NULL,
    raw_location TEXT,
    workplace_type TEXT,
    new_city TEXT,
    new_state TEXT,
    new_country TEXT,
    new_is_remote BOOLEAN,
    old_status TEXT,
    new_status TEXT,
    action TEXT,             -- 'updated', 'dropped', 'unchanged'
    batch_tag TEXT,
    changed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lbc_job_id ON location_backfill_changes(job_id);
CREATE INDEX IF NOT EXISTS idx_lbc_batch ON location_backfill_changes(batch_tag);
"""


def ensure_schema():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        conn.commit()


def fetch_candidates():
    """All active tier-1 jobs with their raw location string."""
    sql = """
        SELECT jp.job_id,
               l.location AS raw_location,
               jp.workplace_type,
               jp.status
        FROM job_postings jp
        LEFT JOIN locations l ON l.location_id = jp.location_id
        WHERE jp.status = 'raw'
          AND COALESCE(jp.data_tier, 1) = 1
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        return cur.fetchall()


def dry_run(jobs):
    print(f"\n{'=' * 70}")
    print(f"DRY RUN — {len(jobs)} jobs would be processed")
    print(f"{'=' * 70}\n")

    counts = {"US": 0, "foreign": 0, "unknown": 0}
    drop_count = 0
    remote_count = 0

    for j in jobs:
        result = normalize_location(j["raw_location"], j["workplace_type"])
        counts[result.country] += 1
        if result.should_drop:
            drop_count += 1
        if result.is_remote:
            remote_count += 1

    print(f"  US:      {counts['US']}")
    print(f"  Foreign: {counts['foreign']}")
    print(f"  Unknown: {counts['unknown']}")
    print(f"  Remote:  {remote_count}")
    print(f"\n  Will mark as status='ignored' (foreign drops): {drop_count}")
    print(f"  Will write loc_* fields for the remaining: {len(jobs) - drop_count}")
    print(f"\n  Estimated runtime: ~{len(jobs) / 1500:.0f}-{len(jobs) / 800:.0f} seconds")
    print(f"  Cost: $0 (no API calls)")
    print("\nRun without --dry-run to execute.\n")


def real_run(jobs):
    print(f"\n{'=' * 70}")
    print(f"BACKFILL — batch tag: {BATCH_TAG}")
    print(f"{'=' * 70}\n")

    updated = 0
    dropped = 0
    unchanged = 0
    errors = 0
    start = time.time()

    with get_conn() as conn:
        for i, j in enumerate(jobs, 1):
            try:
                result = normalize_location(j["raw_location"], j["workplace_type"])
            except Exception as e:
                errors += 1
                print(f"  [{i}/{len(jobs)}] ERR {j['job_id']}: {e}")
                continue

            with conn.cursor() as cur:
                if result.should_drop:
                    # Foreign — mark status='ignored' and zero out loc_* fields
                    cur.execute("""
                        UPDATE job_postings
                        SET status = 'ignored',
                            loc_city = NULL,
                            loc_state = NULL,
                            loc_country = 'foreign'
                        WHERE job_id = %s
                    """, (j["job_id"],))
                    cur.execute("""
                        INSERT INTO location_backfill_changes
                          (job_id, raw_location, workplace_type, new_city, new_state, new_country, new_is_remote,
                           old_status, new_status, action, batch_tag)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        j["job_id"], j["raw_location"], j["workplace_type"],
                        None, None, "foreign", result.is_remote,
                        j["status"], "ignored", "dropped", BATCH_TAG,
                    ))
                    dropped += 1
                else:
                    # Update normalized fields, leave status alone
                    cur.execute("""
                        UPDATE job_postings
                        SET loc_city = %s,
                            loc_state = %s,
                            loc_country = %s
                        WHERE job_id = %s
                    """, (result.city, result.state, result.country, j["job_id"]))
                    cur.execute("""
                        INSERT INTO location_backfill_changes
                          (job_id, raw_location, workplace_type, new_city, new_state, new_country, new_is_remote,
                           old_status, new_status, action, batch_tag)
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """, (
                        j["job_id"], j["raw_location"], j["workplace_type"],
                        result.city, result.state, result.country, result.is_remote,
                        j["status"], j["status"], "updated", BATCH_TAG,
                    ))
                    updated += 1

            if i % 500 == 0 or i == len(jobs):
                elapsed = time.time() - start
                rate = i / elapsed if elapsed > 0 else 0
                print(f"  [{i}/{len(jobs)}] updated={updated} dropped={dropped} errors={errors} | {rate:.0f}/sec")

        conn.commit()

    elapsed = time.time() - start
    print(f"\n{'=' * 70}")
    print(f"DONE in {elapsed:.1f} seconds")
    print(f"{'=' * 70}")
    print(f"  Updated (loc_* populated): {updated}")
    print(f"  Dropped (foreign → ignored): {dropped}")
    print(f"  Errors: {errors}")
    print(f"\n  Batch tag: {BATCH_TAG}")
    print(f"\nAudit:")
    print(f"  SELECT new_country, action, COUNT(*) FROM location_backfill_changes")
    print(f"  WHERE batch_tag = '{BATCH_TAG}' GROUP BY 1,2 ORDER BY 3 DESC;")
    print(f"\nRollback (ALL jobs in this batch):")
    print(f"  UPDATE job_postings jp SET status = lbc.old_status,")
    print(f"     loc_city = NULL, loc_state = NULL, loc_country = NULL")
    print(f"  FROM location_backfill_changes lbc")
    print(f"  WHERE jp.job_id = lbc.job_id AND lbc.batch_tag = '{BATCH_TAG}';")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Estimate without writes")
    args = ap.parse_args()

    ensure_schema()
    jobs = fetch_candidates()

    if not jobs:
        print("No active tier-1 jobs found. Nothing to do.")
        return

    if args.dry_run:
        dry_run(jobs)
    else:
        real_run(jobs)


if __name__ == "__main__":
    main()
