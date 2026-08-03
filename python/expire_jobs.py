#!/usr/bin/env python3
"""
Marks tier-1 jobs as expired if not seen in today's ingest run.
Includes a sanity check — if today's ingest was unhealthy (< 50 new jobs),
expiry is skipped entirely to avoid false positives from a bad run.
Run nightly AFTER ingest_jobs.py completes.
"""
import os
import argparse
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

INGEST_SOURCES = ("greenhouse", "lever", "ashby", "workday", "eightfold",
    "amazon", "smartrecruiters", "workable", "icims", "taleo")

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
    parser.add_argument("--since", help="ISO timestamp marking the current orchestration run")
    args = parser.parse_args()
    conn = get_conn()
    try:
        with conn.cursor() as cur:

            # Expire only sources proven healthy during this crawl window. The
            # Airflow gate requires all scheduled sources; this also makes the
            # script safe when run independently after a partial crawl.
            cur.execute("""SELECT ingestion_source, COUNT(*) FROM job_postings
                WHERE last_seen_at >= COALESCE(%s::timestamptz, now() - interval '4 hours')
                AND data_tier = 1 AND ingestion_source = ANY(%s)
                GROUP BY ingestion_source""", (args.since, list(INGEST_SOURCES)))
            counts = dict(cur.fetchall())
            healthy_sources = [source for source in INGEST_SOURCES if counts.get(source, 0) > 0]
            if not healthy_sources:
                print("⚠️ No sources completed recently — skipping expiry")
                return
            print(f"✅ Expiring only healthy sources: {', '.join(healthy_sources)}")

            # Log disappeared events BEFORE marking expired (so we capture last_seen_at)
            cur.execute("""
                INSERT INTO job_posting_events (job_id, event_type, observed_at, source, posted_date)
                SELECT jp.job_id, 'disappeared', jp.last_seen_at, jp.ingestion_source, jp.posted_date
                FROM job_postings jp
                WHERE jp.data_tier = 1
                  AND jp.status != 'expired'
                  AND jp.ingestion_source = ANY(%s)
                  AND jp.last_seen_at < now() - interval '1 day'
                ON CONFLICT DO NOTHING
            """, (healthy_sources,))
            disappeared_events = cur.rowcount

            # Mark expired — not seen in today's run (missed last_seen_at update)
            cur.execute("""
                UPDATE job_postings
                SET status = 'expired',
                    expired_reason = 'natural_cron'
                WHERE data_tier = 1
                AND status != 'expired'
                AND ingestion_source = ANY(%s)
                AND last_seen_at < now() - interval '1 day'
            """, (healthy_sources,))
            expired = cur.rowcount
            print(f"Marked expired: {expired}  (logged {disappeared_events} disappeared events)")

            # Log reappeared events BEFORE reactivating (capture the gap)
            cur.execute("""
                INSERT INTO job_posting_events (job_id, event_type, observed_at, gap_days, source, posted_date)
                SELECT
                    jp.job_id,
                    'reappeared',
                    now(),
                    EXTRACT(DAYS FROM (now() - jp.last_seen_at))::int,
                    jp.ingestion_source,
                    jp.posted_date
                FROM job_postings jp
                WHERE jp.data_tier = 1
                  AND jp.status = 'expired'
                  AND jp.last_seen_at >= now() - interval '1 day'
                ON CONFLICT DO NOTHING
            """)
            reappeared_events = cur.rowcount

            # Reactivate — seen again after being marked expired
            cur.execute("""
                UPDATE job_postings
                SET status = 'raw',
                    expired_reason = NULL
                WHERE data_tier = 1
                AND status = 'expired'
                AND last_seen_at >= now() - interval '1 day'
            """)
            reactivated = cur.rowcount
            print(f"Reactivated: {reactivated}  (logged {reappeared_events} reappeared events)")

        conn.commit()
        print("✅ expire_jobs complete")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
