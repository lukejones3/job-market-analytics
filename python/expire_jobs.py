#!/usr/bin/env python3
"""
Marks tier-1 jobs as expired if not seen in today's ingest run.
Includes a sanity check — if today's ingest was unhealthy (< 50 new jobs),
expiry is skipped entirely to avoid false positives from a bad run.
Run nightly AFTER ingest_jobs.py completes.
"""
import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2

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
    conn = get_conn()
    try:
        with conn.cursor() as cur:

            # Sanity check — was today's ingest healthy?
            cur.execute("""
                SELECT COUNT(*) FROM job_postings
                WHERE last_seen_at >= now() - interval '4 hours'
                AND data_tier = 1
            """)
            todays_seen = cur.fetchone()[0]

            if todays_seen < 50:
                print(f"⚠️ Only {todays_seen} jobs seen in last 4 hours — skipping expiry to avoid false positives")
                return

            print(f"✅ Ingest healthy — {todays_seen} jobs seen today. Running expiry...")

            # Log disappeared events BEFORE marking expired (so we capture last_seen_at)
            cur.execute("""
                INSERT INTO job_posting_events (job_id, event_type, observed_at, source, posted_date)
                SELECT jp.job_id, 'disappeared', jp.last_seen_at, jp.ingestion_source, jp.posted_date
                FROM job_postings jp
                WHERE jp.data_tier = 1
                  AND jp.status != 'expired'
                  AND jp.last_seen_at < now() - interval '1 day'
                ON CONFLICT DO NOTHING
            """)
            disappeared_events = cur.rowcount

            # Mark expired — not seen in today's run (missed last_seen_at update)
            cur.execute("""
                UPDATE job_postings
                SET status = 'expired',
                    expired_reason = 'natural_cron'
                WHERE data_tier = 1
                AND status != 'expired'
                AND last_seen_at < now() - interval '1 day'
            """)
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
