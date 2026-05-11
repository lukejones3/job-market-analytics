#!/usr/bin/env python3
"""
backfill_location_country.py

Re-run location_normalizer against all raw tier-1 jobs whose loc_country='unknown'
and flip any that are now detected as foreign.

Usage:
    python python/backfill_location_country.py           # dry-run
    python python/backfill_location_country.py --apply   # write to DB
    python python/backfill_location_country.py --limit 500  # dry-run on 500 random
"""

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from location_normalizer import normalize_location

BATCH_SIZE = 500


def get_conn():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        dbname="job_analytics",
    )


def run(apply: bool, limit: Optional[int]):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    limit_clause = f"ORDER BY random() LIMIT {limit}" if limit else ""
    cur.execute(
        f"""
        SELECT jp.job_id, l.location
        FROM job_postings jp
        JOIN locations l ON jp.location_id = l.location_id
        WHERE jp.loc_country = 'unknown'
          AND jp.status = 'raw'
          AND jp.data_tier = 1
        {limit_clause}
        """
    )
    jobs = cur.fetchall()
    total = len(jobs)
    print(f"Evaluating {total:,} unknown-country jobs...")

    flip_ids: list[str] = []
    flip_samples: list[tuple[str, str]] = []
    stay_samples: list[str] = []

    for job in jobs:
        loc = job["location"] or ""
        result = normalize_location(loc)
        if result.country == "foreign":
            flip_ids.append(job["job_id"])
            if len(flip_samples) < 30:
                flip_samples.append((loc, job["job_id"]))
        else:
            if len(stay_samples) < 20:
                stay_samples.append(loc)

    pct_flip = 100 * len(flip_ids) / total if total else 0

    print(f"\n{'='*60}")
    print(f"LOCATION BACKFILL RESULTS  ({total:,} unknown jobs)")
    print(f"{'='*60}")
    print(f"  Flip → 'foreign':  {len(flip_ids):>7,}  ({pct_flip:.1f}%)")
    print(f"  Stay 'unknown':    {total - len(flip_ids):>7,}  ({100-pct_flip:.1f}%)")

    print(f"\nSample FLIP (location → job_id):")
    for loc, job_id in flip_samples:
        print(f"  {loc[:60]:<60} {job_id}")

    print(f"\nSample STAY unknown (first {len(stay_samples)}):")
    for loc in stay_samples:
        print(f"  {loc}")

    if not apply:
        print(f"\nDry-run — no DB changes.")
        print(f"Re-run with --apply to flip {len(flip_ids):,} jobs to loc_country='foreign'.")
        return

    print(f"\nApplying {len(flip_ids):,} → loc_country='foreign' updates...")
    start = time.time()
    updated = 0

    for i in range(0, len(flip_ids), BATCH_SIZE):
        batch = flip_ids[i : i + BATCH_SIZE]
        cur.execute(
            "UPDATE job_postings SET loc_country = 'foreign' WHERE job_id = ANY(%s)",
            (batch,),
        )
        conn.commit()
        updated += len(batch)
        if updated % 5000 == 0 or updated == len(flip_ids):
            elapsed = time.time() - start
            print(f"  {updated:,} / {len(flip_ids):,}  ({elapsed:.0f}s)")

    print(f"\nDone. {len(flip_ids):,} jobs → loc_country='foreign'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Backfill loc_country: flip unknown→foreign for newly-detected foreign cities"
    )
    parser.add_argument("--apply", action="store_true", help="Write to DB (default: dry-run)")
    parser.add_argument("--limit", type=int, metavar="N", help="Process only N random jobs")
    args = parser.parse_args()
    run(apply=args.apply, limit=args.limit)
