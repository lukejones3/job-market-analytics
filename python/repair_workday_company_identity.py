#!/usr/bin/env python3
"""Repair high-confidence Workday tenant/company identity errors.

Dry-run is the default. --apply preserves every posting and lifecycle event; it
only corrects tenant metadata and repoints postings to the proper company row.
"""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

from company_identity import workday_company_overrides

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def company_id(name: str) -> str:
    return "C" + hashlib.md5(name.encode("utf-8")).hexdigest()[:10]


def connection():
    return psycopg2.connect(host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    changed = []
    with connection() as conn, conn.cursor() as cur:
        for tenant, correct_name in workday_company_overrides().items():
            correct_id = company_id(correct_name)
            cur.execute("""SELECT count(*), array_agg(DISTINCT c.company_name)
                FROM job_postings jp LEFT JOIN companies c USING(company_id)
                WHERE jp.ingestion_source='workday' AND lower(jp.crawl_tenant)=%s""", (tenant,))
            postings, old_names = cur.fetchone()
            if not postings:
                continue
            changed.append((tenant, old_names or [], correct_name, postings))
            if args.apply:
                cur.execute("""INSERT INTO companies(company_id, company_name) VALUES(%s,%s)
                    ON CONFLICT(company_id) DO UPDATE SET company_name=EXCLUDED.company_name""",
                    (correct_id, correct_name))
                cur.execute("""UPDATE discovered_companies SET company_name=%s
                    WHERE ats_source='workday' AND split_part(board_token,'/',1)=%s""",
                    (correct_name, tenant))
                cur.execute("""UPDATE job_postings SET company_id=%s
                    WHERE ingestion_source='workday' AND lower(crawl_tenant)=%s
                      AND company_id IS DISTINCT FROM %s""", (correct_id, tenant, correct_id))
        if not args.apply:
            conn.rollback()
    for tenant, old_names, correct_name, postings in changed:
        print(f"{tenant:12} {postings:5} postings  {old_names} -> {correct_name}")
    print(("Applied" if args.apply else "Would apply") + f" {len(changed)} tenant repairs")


if __name__ == "__main__":
    main()
