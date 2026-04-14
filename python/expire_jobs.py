#!/usr/bin/env python3
"""
Marks tier-1 jobs as expired if not seen in the last 3 nightly runs (~3 days).
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
            # Mark expired — not seen in 3 days
            cur.execute("""
                UPDATE job_postings
                SET status = 'expired'
                WHERE data_tier = 1
                AND status != 'expired'
                AND last_seen_at < now() - interval '3 days'
            """)
            expired = cur.rowcount
            print(f"Marked expired: {expired}")

            # Mark active — seen recently
            cur.execute("""
                UPDATE job_postings
                SET status = 'active'
                WHERE data_tier = 1
                AND status = 'expired'
                AND last_seen_at >= now() - interval '3 days'
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
