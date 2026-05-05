#!/usr/bin/env python3
"""bf_classifier.py — Re-run the data-title pre-check on every job currently classified non_data.

Conservative: only flips rows where the new DATA_TITLE_PRECHECK_v1 returns a definitive
data subcategory. Doesn't touch the LLM. Doesn't run on rows already classified data_*.

Audit logged to classifier_backfill_changes.

Usage:
    python python/bf_classifier.py --dry-run
    python python/bf_classifier.py
"""

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/opt/job-market-analytics")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python"))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

# Force fresh import
for mod in list(sys.modules.keys()):
    if "llm_client" in mod:
        del sys.modules[mod]

from llm_client import _data_title_subcategory  # noqa: E402

BATCH_TAG = f"classifier_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def get_conn():
    return psycopg2.connect(
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"],
        port=int(os.environ["PGPORT"]),
    )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS classifier_backfill_changes (
    id SERIAL PRIMARY KEY,
    job_id TEXT NOT NULL,
    role_name TEXT,
    company_name TEXT,
    old_category TEXT,
    new_category TEXT,
    batch_tag TEXT,
    changed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_cbc_job_id ON classifier_backfill_changes(job_id);
CREATE INDEX IF NOT EXISTS idx_cbc_batch ON classifier_backfill_changes(batch_tag);
"""


def ensure_schema():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        conn.commit()


def fetch_candidates():
    """All current non_data jobs in active tier-1 inventory."""
    sql = """
        SELECT jp.job_id, jp.role_category, r.role_name, c.company_name
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN companies c ON c.company_id = jp.company_id
        WHERE jp.status = 'raw'
          AND COALESCE(jp.data_tier, 1) = 1
          AND jp.role_category = 'non_data'
          AND r.role_name IS NOT NULL
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        return cur.fetchall()


def dry_run(jobs):
    flips = {}
    samples = []
    for j in jobs:
        new_cat = _data_title_subcategory(j["role_name"])
        if not new_cat:
            continue
        flips[new_cat] = flips.get(new_cat, 0) + 1
        if len(samples) < 15:
            samples.append((j["role_name"], j["company_name"], new_cat))

    print()
    print("=" * 70)
    print(f"DRY RUN — {len(jobs)} candidates")
    print("=" * 70)
    print()
    total = sum(flips.values())
    print(f"Will reclassify: {total} jobs")
    for cat, n in sorted(flips.items(), key=lambda kv: -kv[1]):
        print(f"  non_data -> {cat}: {n}")
    print()
    print("Sample flips:")
    for title, company, new_cat in samples:
        print(f"  {(title or '')[:50].ljust(50)} @ {(company or '')[:25].ljust(25)} -> {new_cat}")
    print()
    print("Run without --dry-run to execute.")


def real_run(jobs):
    print()
    print("=" * 70)
    print(f"BACKFILL — batch tag: {BATCH_TAG}")
    print("=" * 70)
    print()

    flipped = 0
    errors = 0

    with get_conn() as conn:
        for i, j in enumerate(jobs, 1):
            new_cat = _data_title_subcategory(j["role_name"])
            if not new_cat:
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE job_postings SET role_category = %s WHERE job_id = %s",
                        (new_cat, j["job_id"]),
                    )
                    cur.execute(
                        """
                        INSERT INTO classifier_backfill_changes
                          (job_id, role_name, company_name, old_category, new_category, batch_tag)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        """,
                        (j["job_id"], j["role_name"], j["company_name"],
                         j["role_category"], new_cat, BATCH_TAG),
                    )
                flipped += 1
            except Exception as e:
                errors += 1
                print(f"  ERR {j['job_id']}: {e}")

            if i % 200 == 0:
                print(f"  [{i}/{len(jobs)}] flipped={flipped} errors={errors}")

        conn.commit()

    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)
    print(f"  Flipped: {flipped}")
    print(f"  Errors: {errors}")
    print(f"  Batch tag: {BATCH_TAG}")
    print()
    print("Rollback if needed:")
    print(f"  UPDATE job_postings jp SET role_category = cbc.old_category")
    print(f"  FROM classifier_backfill_changes cbc")
    print(f"  WHERE jp.job_id = cbc.job_id AND cbc.batch_tag = '{BATCH_TAG}';")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    ensure_schema()
    jobs = fetch_candidates()
    if not jobs:
        print("No non_data jobs to reclassify.")
        return
    if args.dry_run:
        dry_run(jobs)
    else:
        real_run(jobs)


if __name__ == "__main__":
    main()
