#!/usr/bin/env python3
"""Fail-closed database gates for Airflow orchestration."""
import argparse
import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

INGEST_SOURCES = ("greenhouse", "lever", "ashby", "workday", "eightfold",
    "amazon", "smartrecruiters", "workable", "icims", "taleo")

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
    parser.add_argument("--since", help="ISO timestamp marking the current orchestration run")
    args = parser.parse_args()
    gate = args.gate
    with connection() as conn, conn.cursor() as cursor:
        if gate == "shadow":
            total = scalar(cursor, "SELECT COUNT(*) FROM job_postings")
            if total < 1000:
                raise RuntimeError(f"implausibly small job table: {total}")
            print(f"shadow gate passed: jobs={total}")
        elif gate == "ingest":
            cursor.execute("""SELECT source, status, jobs_fetched FROM ingestion_crawl_runs
                WHERE orchestration_run_id=%s AND finished_at IS NOT NULL""", (args.since,))
            crawl_rows = {row[0]: row[1:] for row in cursor.fetchall()}
            bad = [source for source in INGEST_SOURCES
                   if crawl_rows.get(source, (None,))[0] not in ('complete_nonzero', 'complete_zero')]
            if bad:
                raise RuntimeError(f"ingest gate failed; incomplete crawl outcome for: {', '.join(bad)}")
            cursor.execute("""SELECT ingestion_source, COUNT(*) FROM job_postings
                WHERE data_tier=1 AND last_seen_at >= COALESCE(%s::timestamptz, now() - interval '12 hours')
                AND ingestion_source = ANY(%s) GROUP BY ingestion_source""",
                (args.since, list(INGEST_SOURCES)))
            counts = dict(cursor.fetchall())
            missing = [source for source in INGEST_SOURCES
                       if crawl_rows[source][0] == 'complete_nonzero' and counts.get(source, 0) == 0]
            if missing:
                raise RuntimeError(f"ingest gate failed; no current-run rows for: {', '.join(missing)}")
            print(f"ingest gate passed: " + ", ".join(f"{s}={counts.get(s, 0)}" for s in INGEST_SOURCES))
        else:
            active = scalar(cursor, "SELECT COUNT(*) FROM job_postings WHERE status='raw' AND data_tier=1")
            missing = scalar(cursor, """SELECT COUNT(*) FROM job_postings
                WHERE status='raw' AND data_tier=1 AND company_id IS NULL""")
            if active < 1000 or missing > max(25, active // 100):
                raise RuntimeError(f"publish gate failed: active={active}, missing_company={missing}")
            print(f"publish gate passed: active={active}, missing_company={missing}")

if __name__ == "__main__":
    main()
