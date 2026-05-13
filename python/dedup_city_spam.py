#!/usr/bin/env python3
"""
Collapse city-templated duplicate job listings.

Companies using certain ATS platforms post the same remote job 100+ times,
once per city. Each city variant has a different job_id (bypasses standard
dedup) but identical company + role + description.

Detection: (company_id, role_id) pairs with >= N distinct loc_city values.
Action:    Keep the oldest (first-ingested) row; expire the rest with
           expired_reason='city_spam_dedup'. Update surviving row's location
           to 'Multiple Locations' and workplace_type to 'remote'.

Usage:
  python python/dedup_city_spam.py              # dry-run (default)
  python python/dedup_city_spam.py --apply      # apply changes
  python python/dedup_city_spam.py --threshold 10  # require 10+ cities
"""
import argparse
import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import RealDictCursor

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run)")
    parser.add_argument("--threshold", type=int, default=5, help="Min distinct cities to trigger dedup (default: 5)")
    args = parser.parse_args()

    dry_run = not args.apply
    threshold = args.threshold

    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            # 1. Find all (company_id, role_id) combos with >= threshold distinct cities
            cur.execute("""
                SELECT
                    jp.company_id,
                    jp.role_id,
                    c.company_name,
                    r.role_name,
                    COUNT(DISTINCT jp.loc_city)  AS city_count,
                    COUNT(DISTINCT jp.job_id)    AS total_jobs,
                    MIN(jp.ingested_at)          AS first_seen,
                    STRING_AGG(DISTINCT jp.loc_city, ', ' ORDER BY jp.loc_city) AS all_cities
                FROM job_postings jp
                JOIN companies c ON c.company_id = jp.company_id
                JOIN roles r     ON r.role_id    = jp.role_id
                WHERE jp.status = 'raw' AND jp.data_tier = 1
                GROUP BY jp.company_id, jp.role_id, c.company_name, r.role_name
                HAVING COUNT(DISTINCT jp.loc_city) >= %s
                ORDER BY city_count DESC
            """, (threshold,))
            spam_combos = cur.fetchall()

        total_combos   = len(spam_combos)
        total_to_expire = sum(row["total_jobs"] - 1 for row in spam_combos)

        print(f"{'DRY-RUN' if dry_run else 'APPLYING'} — threshold: {threshold}+ cities")
        print(f"Combos matching: {total_combos}")
        print(f"Rows to expire:  {total_to_expire:,}  (keeping 1 survivor per combo)")
        print()

        print(f"{'Company':26s} {'Role':38s} {'Cities':>7} {'Jobs':>5}")
        print("-" * 80)
        for row in spam_combos[:30]:
            city_preview = (row["all_cities"] or "")[:60]
            if len(row["all_cities"] or "") > 60:
                city_preview += "..."
            print(f"{row['company_name']:26.26s} {row['role_name'][:38]:38s} {row['city_count']:>7} {row['total_jobs']:>5}")
            print(f"  {city_preview}")
        if total_combos > 30:
            print(f"  ... and {total_combos - 30} more combos")

        print()

        if dry_run:
            print("Dry-run complete. Re-run with --apply to write changes.")
            return

        # 2. Apply: expire duplicates, keep oldest per (company_id, role_id)
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE job_postings
                SET status          = 'expired',
                    expired_reason  = 'city_spam_dedup'
                WHERE job_id IN (
                    SELECT job_id FROM (
                        SELECT job_id,
                               ROW_NUMBER() OVER (
                                   PARTITION BY company_id, role_id
                                   ORDER BY ingested_at ASC
                               ) AS rn,
                               COUNT(*) OVER (PARTITION BY company_id, role_id) AS city_count
                        FROM job_postings
                        WHERE status = 'raw' AND data_tier = 1
                    ) ranked
                    WHERE city_count >= %s AND rn > 1
                )
            """, (threshold,))
            expired = cur.rowcount
            print(f"Expired {expired:,} duplicate rows.")

            # 3. Update surviving row location
            cur.execute("""
                UPDATE job_postings jp
                SET loc_city       = 'Multiple Locations',
                    loc_state      = NULL,
                    workplace_type = 'remote'
                WHERE jp.status = 'raw'
                  AND jp.data_tier = 1
                  AND (
                    SELECT COUNT(*)
                    FROM job_postings jp2
                    WHERE jp2.company_id     = jp.company_id
                      AND jp2.role_id        = jp.role_id
                      AND jp2.expired_reason = 'city_spam_dedup'
                  ) >= %s
            """, (threshold - 1,))
            updated_location = cur.rowcount
            print(f"Updated {updated_location} surviving rows to 'Multiple Locations'.")

        conn.commit()
        print("Done.")

    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
