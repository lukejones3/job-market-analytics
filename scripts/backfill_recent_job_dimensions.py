#!/usr/bin/env python3
import os, sys
from typing import Optional, Tuple
import psycopg2
from psycopg2.extras import DictCursor

# ensure repo root is on path so `python/enrich_job_postings.py` is importable
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, REPO_ROOT)

from python.enrich_job_postings import (
    extract_title_company_location_from_description,
    infer_experience_level,
    parse_workplace_type,
    parse_salary_range,
    upsert_company,
    upsert_role,
    upsert_location,
)

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def main(limit: int = 120, apply: bool = False):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("""
      SELECT job_id, company_id, role_id, location_id, workplace_type,
             experience_level, salary_min, salary_max, salary_period, description_text
      FROM job_postings
      ORDER BY ingested_at DESC
      LIMIT %s
    """, (limit,))
    rows = cur.fetchall()

    planned = 0
    updated = 0

    for job in rows:
        job_id = job["job_id"]
        desc = job["description_text"] or ""
        if not desc.strip():
            continue

        ex = extract_title_company_location_from_description(desc)
        workplace = parse_workplace_type(desc)
        smin, smax, period = parse_salary_range(desc)
        exp_level = infer_experience_level(desc, title_hint=ex.get("title"))

        # compute new dimension IDs if missing
        company_id = job["company_id"]
        role_id = job["role_id"]
        location_id = job["location_id"]

        if (not company_id) and ex.get("company"):
            company_id = upsert_company(cur, ex["company"])
        if (not role_id) and ex.get("title"):
            role_id = upsert_role(cur, ex["title"])
        if (not location_id) and ex.get("location"):
            location_id = upsert_location(cur, ex["location"], ex.get("state"))

        fields = []
        params = []

        def add(col, val, old):
            nonlocal planned
            if val is not None and val != old:
                fields.append(f"{col}=%s")
                params.append(val)
                planned += 1

        add("company_id", company_id, job["company_id"])
        add("role_id", role_id, job["role_id"])
        add("location_id", location_id, job["location_id"])
        add("workplace_type", workplace, job["workplace_type"])
        add("salary_min", smin, job["salary_min"])
        add("salary_max", smax, job["salary_max"])
        add("salary_period", period, job["salary_period"])
        add("experience_level", exp_level, job["experience_level"])

        if fields:
            params.append(job_id)
            if apply:
                cur.execute(f"UPDATE job_postings SET {', '.join(fields)} WHERE job_id=%s", tuple(params))
                updated += 1
            else:
                print(f"DRYRUN would update {job_id}: {', '.join([f.split('=')[0] for f in fields])}")

    if apply:
        conn.commit()
        print(f"✅ Updated jobs: {updated}")
    else:
        conn.rollback()
        print("ℹ️ Dry-run only. Re-run with --apply to write changes.")

    cur.close()
    conn.close()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=120)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    main(limit=args.limit, apply=args.apply)
