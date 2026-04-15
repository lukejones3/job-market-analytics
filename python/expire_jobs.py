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

            # Mark expired — not seen in today's run (missed last_seen_at update)
            cur.execute("""
                UPDATE job_postings
                SET status = 'expired'
                WHERE data_tier = 1
                AND status != 'expired'
                AND last_seen_at < now() - interval '1 day'
            """)
            expired = cur.rowcount
            print(f"Marked expired: {expired}")

            # Reactivate — seen again after being marked expired
            cur.execute("""
                UPDATE job_postings
                SET status = 'raw'
                WHERE data_tier = 1
                AND status = 'expired'
                AND last_seen_at >= now() - interval '1 day'
            """)
            reactivated = cur.rowcount
            print(f"Reactivated: {reactivated}")

        conn.commit()
        print("✅ expire_jobs complete")
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

if __name__ == "__main__":
    main()
