#!/usr/bin/env python3
"""Fail-closed database gates for Airflow orchestration."""
import argparse
import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

def connection():
    return psycopg2.connect(host=os.environ["PGHOST"], port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"], connect_timeout=10)

def scalar(cursor, sql):
    cursor.execute(sql)
    return cursor.fetchone()[0]

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("gate", choices=("shadow", "ingest", "publish"))
    gate = parser.parse_args().gate
    with connection() as conn, conn.cursor() as cursor:
        if gate == "shadow":
            total = scalar(cursor, "SELECT COUNT(*) FROM job_postings")
            if total < 1000:
                raise RuntimeError(f"implausibly small job table: {total}")
            print(f"shadow gate passed: jobs={total}")
        elif gate == "ingest":
            seen = scalar(cursor, """SELECT COUNT(*) FROM job_postings
                WHERE data_tier=1 AND last_seen_at >= now() - interval '12 hours'""")
            sources = scalar(cursor, """SELECT COUNT(DISTINCT ingestion_source) FROM job_postings
                WHERE data_tier=1 AND last_seen_at >= now() - interval '12 hours'""")
            if seen < 50 or sources < 5:
                raise RuntimeError(f"ingest gate failed: seen={seen}, sources={sources}")
            print(f"ingest gate passed: seen={seen}, sources={sources}")
        else:
            active = scalar(cursor, "SELECT COUNT(*) FROM job_postings WHERE status != 'expired'")
            missing = scalar(cursor, """SELECT COUNT(*) FROM job_postings
                WHERE status != 'expired' AND company_id IS NULL""")
            if active < 1000 or missing > max(25, active // 100):
                raise RuntimeError(f"publish gate failed: active={active}, missing_company={missing}")
            print(f"publish gate passed: active={active}, missing_company={missing}")

if __name__ == "__main__":
    main()
