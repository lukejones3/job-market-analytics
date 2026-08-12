#!/usr/bin/env python3
"""Fail-closed database gates for Airflow orchestration."""
import argparse
import os
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

INGEST_SOURCES = ("greenhouse", "lever", "ashby", "workday", "eightfold",
    "amazon", "smartrecruiters", "workable", "icims", "taleo", "jobvite", "bamboohr")

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

def connection():
    return psycopg2.connect(host=os.environ["PGHOST"], port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"], connect_timeout=10)

def scalar(cursor, sql):
    cursor.execute(sql)
    return cursor.fetchone()[0]


def bad_ingest_sources(crawl_rows, tenant_failures):
    """Return sources whose latest source or tenant outcome is incomplete."""
    return [
        source for source in INGEST_SOURCES
        if not crawl_rows.get(source, (False, 1, 0))[0]
        or crawl_rows[source][1] > 0
        or tenant_failures.get(source, 0) > 0
    ]

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
            # Evaluate the latest retry for every source, then independently
            # require the latest outcome for every observed tenant to be clean.
            # This prevents one successful sibling from masking a partial batch.
            cursor.execute("""WITH ranked AS (
                    SELECT source, status, finished_at, jobs_written,
                           row_number() OVER (PARTITION BY source ORDER BY started_at DESC, run_id DESC) AS rn
                    FROM ingestion_crawl_runs WHERE orchestration_run_id=%s
                )
                SELECT source,
                       status IN ('complete_nonzero', 'complete_zero') AS succeeded,
                       CASE WHEN finished_at IS NULL OR status='running' THEN 1 ELSE 0 END AS running,
                       jobs_written
                FROM ranked WHERE rn=1""", (args.since,))
            crawl_rows = {row[0]: row[1:] for row in cursor.fetchall()}
            cursor.execute("""WITH ranked AS (
                    SELECT tr.source, tr.crawl_tenant, tr.status,
                           row_number() OVER (
                               PARTITION BY tr.source, tr.crawl_tenant
                               ORDER BY cr.started_at DESC, tr.run_id DESC
                           ) AS rn
                    FROM ingestion_tenant_runs tr
                    JOIN ingestion_crawl_runs cr ON cr.run_id=tr.run_id
                    WHERE cr.orchestration_run_id=%s
                )
                SELECT source, count(*) FILTER (
                    WHERE status NOT IN ('complete_nonzero', 'complete_zero')
                )::integer AS failed_tenants
                FROM ranked WHERE rn=1 GROUP BY source""", (args.since,))
            tenant_failures = dict(cursor.fetchall())
            bad = bad_ingest_sources(crawl_rows, tenant_failures)
            if bad:
                raise RuntimeError(f"ingest gate failed; incomplete crawl outcome for: {', '.join(bad)}")
            cursor.execute("""SELECT ingestion_source, COUNT(*) FROM job_postings
                WHERE data_tier=1 AND last_seen_at >= COALESCE(
                    (SELECT MIN(started_at) FROM ingestion_crawl_runs
                     WHERE orchestration_run_id=%s),
                    now() - interval '12 hours')
                AND ingestion_source = ANY(%s) GROUP BY ingestion_source""",
                (args.since, list(INGEST_SOURCES)))
            counts = dict(cursor.fetchall())
            missing = [source for source in INGEST_SOURCES
                       if (crawl_rows[source][2] or 0) > 0 and counts.get(source, 0) == 0]
            if missing:
                raise RuntimeError(f"ingest gate failed; no current-run rows for: {', '.join(missing)}")
            print(f"ingest gate passed: " + ", ".join(f"{s}={counts.get(s, 0)}" for s in INGEST_SOURCES))
        else:
            candidate = scalar(cursor, "SELECT COUNT(*) FROM public.vw_lander_publication_candidates")
            current = scalar(cursor, "SELECT COUNT(*) FROM job_postings WHERE is_public=true")
            missing = scalar(cursor, """SELECT COUNT(*) FROM job_postings
                WHERE status='raw' AND data_tier=1 AND company_id IS NULL""")
            foreign = scalar(cursor, """SELECT COUNT(*) FROM job_postings
                WHERE status='raw' AND data_tier=1 AND loc_country='foreign'""")
            required = max(1000, int(current * 0.70)) if current else 1000
            if candidate < required:
                raise RuntimeError(f"publish gate failed: candidate={candidate}, current={current}, "
                                   f"required={required}, missing_company_backlog={missing}, foreign_excluded={foreign}")
            print(f"publish gate passed: candidate={candidate}, current={current}, "
                  f"missing_company_backlog={missing}, foreign_excluded={foreign}")

if __name__ == "__main__":
    main()
