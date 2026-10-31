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


def bad_ingest_sources(crawl_rows):
    """Return sources whose orchestration never completed successfully.

    Resumable sources can create many crawl rows in one Airflow task, so the
    caller aggregates the entire orchestration instead of inspecting only its
    last checkpoint. Individual tenant failures are intentionally not fatal:
    they remain observable and expire_jobs only mutates tenants with a clean
    current-run outcome.
    """
    return [
        source for source in INGEST_SOURCES
        if not crawl_rows.get(source, (False, 1, 0))[0]
        or crawl_rows[source][1] > 0
    ]


def apply_checkpoint_sweeps(crawl_rows, sweeps):
    """Use the latest whole-inventory boundary without hiding unfinished sweeps."""
    for source, status, finished, errors, *_ in sweeps:
        prior = crawl_rows.get(source, (False, 1, 0))
        crawl_rows[source] = (
            status in ('complete_nonzero', 'complete_zero') and not errors,
            int(finished is None or status == 'running'), *prior[2:])


def latest_crawl_boundaries(cursor, orchestration_run_id):
    """A checkpoint manifest owns its source; otherwise the latest full attempt does."""
    cursor.execute("""SELECT DISTINCT ON(source) source,status,finished_at,errors,
            COALESCE((detail->>'degraded')::boolean,false)
        FROM ingestion_crawl_runs WHERE orchestration_run_id=%s
        ORDER BY source,COALESCE(detail->>'checkpoint_sweep'='true',false) DESC,
                 started_at DESC,run_id DESC""", (orchestration_run_id,))
    return cursor.fetchall()


def tenant_failure_counts(cursor, orchestration_run_id):
    cursor.execute("""WITH latest AS (
        SELECT DISTINCT ON(tr.source,tr.crawl_tenant) tr.source,tr.crawl_tenant,tr.status
        FROM ingestion_tenant_runs tr JOIN ingestion_crawl_runs cr USING(run_id)
        WHERE cr.orchestration_run_id=%s
        ORDER BY tr.source,tr.crawl_tenant,cr.started_at DESC,tr.run_id DESC)
        SELECT source,count(*) FILTER(WHERE status NOT IN ('complete_nonzero','complete_zero'))::integer
        FROM latest GROUP BY source""", (orchestration_run_id,))
    return dict(cursor.fetchall())

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
            # A resumable source (currently Workday) records one crawl row per
            # checkpoint. Aggregate the complete Airflow orchestration so a
            # small final checkpoint cannot hide the successful full crawl.
            cursor.execute("""SELECT source,
                       bool_and(status IN ('complete_nonzero', 'complete_zero')) AS succeeded,
                       count(*) FILTER (WHERE finished_at IS NULL OR status='running')::integer AS running,
                       COALESCE(sum(jobs_written), 0)::integer AS jobs_written
                FROM ingestion_crawl_runs
                WHERE orchestration_run_id=%s
                GROUP BY source""", (args.since,))
            crawl_rows = {row[0]: row[1:] for row in cursor.fetchall()}
            # A completed checkpoint sweep is the source-level boundary. Its
            # retry skips only clean committed tenants; a fatal/unfinished
            # sweep never permits publication. Isolated tenant outages remain
            # explicit in child rows and cannot authorize expiry for that tenant.
            apply_checkpoint_sweeps(crawl_rows, latest_crawl_boundaries(cursor, args.since))
            tenant_failures = tenant_failure_counts(cursor, args.since)
            bad = bad_ingest_sources(crawl_rows)
            if bad:
                raise RuntimeError(f"ingest gate failed; incomplete source orchestration for: {', '.join(bad)}")
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
            warning = {source: count for source, count in tenant_failures.items() if count}
            print(f"ingest gate passed: " + ", ".join(f"{s}={counts.get(s, 0)}" for s in INGEST_SOURCES)
                  + f"; isolated_tenant_failures={warning}")
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
