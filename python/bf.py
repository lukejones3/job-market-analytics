#!/usr/bin/env python3
"""
backfill_role_category.py — Re-classify NULL + non_data jobs through the
patched classify_role(). Logs every change to role_category_changes for audit
and rollback.

Usage:
    # See what WOULD happen (no API calls, no writes):
    python python/bf.py --dry-run

    # Run for real:
    python python/bf.py

    # Resume from a partial run (skips jobs already in the audit log from this batch):
    python python/bf.py --resume
"""

import argparse
import os
import sys
import time
from pathlib import Path
from datetime import datetime

ROOT = Path("/opt/job-market-analytics")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python"))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from python.llm_client import classify_role  # noqa: E402

BATCH_TAG = f"backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"


def get_conn():
    return psycopg2.connect(
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"],
        port=int(os.environ["PGPORT"]),
    )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS role_category_changes (
    id SERIAL PRIMARY KEY,
    job_id TEXT NOT NULL,
    company_name TEXT,
    role_name TEXT,
    old_category TEXT,
    new_category TEXT,
    is_data_ml BOOLEAN,
    confidence TEXT,
    reason TEXT,
    via TEXT,                  -- 'precheck' or 'llm'
    batch_tag TEXT,
    changed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_rcc_job_id ON role_category_changes(job_id);
CREATE INDEX IF NOT EXISTS idx_rcc_batch ON role_category_changes(batch_tag);
"""


def ensure_schema():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        conn.commit()


def fetch_candidates(resume_batch=None):
    """All jobs that need re-classification: NULL OR non_data, status=raw, tier=1, has description."""
    sql = """
        SELECT jp.job_id,
               jp.role_category AS old_category,
               r.role_name,
               c.company_name,
               jp.description_text
        FROM job_postings jp
        LEFT JOIN roles r     ON r.role_id    = jp.role_id
        LEFT JOIN companies c ON c.company_id = jp.company_id
        WHERE jp.status = 'raw'
          AND COALESCE(jp.data_tier, 1) = 1
          AND jp.description_text IS NOT NULL
          AND length(jp.description_text) > 0
          AND (jp.role_category IS NULL OR jp.role_category = 'non_data')
    """
    params = []
    if resume_batch:
        sql += """ AND jp.job_id NOT IN (
            SELECT job_id FROM role_category_changes WHERE batch_tag = %s
        )"""
        params.append(resume_batch)
    sql += " ORDER BY jp.ingested_at DESC"

    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def detect_precheck(company_name):
    """Mirror llm_client logic without an API call — for dry-run estimation only."""
    try:
        from python.llm_client import _is_federal_staffing
        return _is_federal_staffing(company_name)
    except ImportError:
        return False


def update_job(cur, job_id, new_category):
    cur.execute(
        "UPDATE job_postings SET role_category = %s, role_classified_at = NOW() WHERE job_id = %s",
        (new_category, job_id),
    )


def log_change(cur, job, verdict, via):
    cur.execute(
        """INSERT INTO role_category_changes
           (job_id, company_name, role_name, old_category, new_category,
            is_data_ml, confidence, reason, via, batch_tag)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            job["job_id"],
            job["company_name"],
            job["role_name"],
            job["old_category"],
            verdict["category"],
            verdict["is_data_ml"],
            verdict["confidence"],
            verdict["reason"],
            via,
            BATCH_TAG,
        ),
    )


def dry_run(jobs):
    print(f"\n{'=' * 70}")
    print(f"DRY RUN — {len(jobs)} jobs would be processed")
    print(f"{'=' * 70}\n")

    null_count = sum(1 for j in jobs if j["old_category"] is None)
    nondata_count = sum(1 for j in jobs if j["old_category"] == "non_data")
    print(f"  NULL → re-classify:     {null_count}")
    print(f"  non_data → re-classify: {nondata_count}")

    precheck_hits = [j for j in jobs if detect_precheck(j["company_name"])]
    print(f"\n  Will skip LLM (precheck): {len(precheck_hits)} jobs")
    print(f"  Will call LLM:            {len(jobs) - len(precheck_hits)} jobs")

    print(f"\n  Estimated cost: ~${(len(jobs) - len(precheck_hits)) * 0.0013:.2f}")
    print(f"  Estimated time: ~{(len(jobs) - len(precheck_hits)) * 1.3 / 60:.0f} minutes")

    if precheck_hits:
        print("\n  Sample precheck hits (will become non_data immediately):")
        for j in precheck_hits[:10]:
            print(f"    {j['company_name'][:30]:<30} | {j['role_name'][:40]} | currently: {j['old_category']}")

    print(f"\n  Sample LLM calls needed:")
    llm_jobs = [j for j in jobs if not detect_precheck(j["company_name"])]
    for j in llm_jobs[:5]:
        print(f"    {j['company_name'][:30] if j['company_name'] else '(no company)':<30} | "
              f"{j['role_name'][:40] if j['role_name'] else '?':<40} | currently: {j['old_category']}")

    print(f"\nBatch tag would be: {BATCH_TAG}")
    print("Run without --dry-run to execute.\n")


def real_run(jobs):
    print(f"\n{'=' * 70}")
    print(f"BACKFILL — batch tag: {BATCH_TAG}")
    print(f"{'=' * 70}")
    print(f"Processing {len(jobs)} jobs...\n")

    changed = 0
    unchanged = 0
    failed = 0
    precheck_hits = 0
    start = time.time()

    with get_conn() as conn:
        for i, job in enumerate(jobs, 1):
            try:
                verdict = classify_role(
                    job["role_name"] or "",
                    job["description_text"] or "",
                    company_name=job["company_name"],
                )
            except Exception as e:
                print(f"  [{i}/{len(jobs)}] EXCEPTION on {job['job_id']}: {e}")
                failed += 1
                continue

            if verdict is None:
                failed += 1
                if failed % 10 == 0:
                    print(f"  [{i}/{len(jobs)}] {failed} failures so far (likely API/credit issue)")
                continue

            via = "precheck" if "pre-check" in (verdict.get("reason") or "") else "llm"
            if via == "precheck":
                precheck_hits += 1

            new_cat = verdict["category"]
            if new_cat != job["old_category"]:
                with conn.cursor() as cur:
                    update_job(cur, job["job_id"], new_cat)
                    log_change(cur, job, verdict, via)
                conn.commit()
                changed += 1
                marker = "★"
            else:
                unchanged += 1
                marker = " "

            if i % 25 == 0 or i <= 10:
                elapsed = time.time() - start
                rate = i / elapsed
                eta_min = (len(jobs) - i) / rate / 60 if rate > 0 else 0
                print(f"  [{i}/{len(jobs)}] {marker} changed={changed} unchanged={unchanged} "
                      f"failed={failed} precheck={precheck_hits} | "
                      f"{rate:.1f}/sec | ETA {eta_min:.0f}min")

    elapsed = time.time() - start
    print(f"\n{'=' * 70}")
    print(f"DONE in {elapsed / 60:.1f} minutes")
    print(f"{'=' * 70}")
    print(f"  Changed:   {changed}")
    print(f"  Unchanged: {unchanged}")
    print(f"  Failed:    {failed}")
    print(f"  Precheck:  {precheck_hits} (no API call)")
    print(f"\nBatch tag for rollback / audit: {BATCH_TAG}")
    print(f"\nAudit query:")
    print(f"  SELECT old_category, new_category, COUNT(*) FROM role_category_changes")
    print(f"  WHERE batch_tag = '{BATCH_TAG}' GROUP BY 1,2 ORDER BY 3 DESC;")
    print(f"\nRollback (if needed):")
    print(f"  UPDATE job_postings jp SET role_category = rcc.old_category")
    print(f"  FROM role_category_changes rcc")
    print(f"  WHERE jp.job_id = rcc.job_id AND rcc.batch_tag = '{BATCH_TAG}';")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Estimate without API calls or writes")
    ap.add_argument("--resume", help="Resume a previous batch by tag (skips already-processed)")
    args = ap.parse_args()

    ensure_schema()
    jobs = fetch_candidates(resume_batch=args.resume)

    if not jobs:
        print("No candidate jobs found. Nothing to do.")
        return

    if args.dry_run:
        dry_run(jobs)
    else:
        real_run(jobs)


if __name__ == "__main__":
    main()
