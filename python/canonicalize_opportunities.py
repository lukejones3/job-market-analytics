#!/usr/bin/env python3
"""Group source postings without deleting or suppressing any requisition."""
import argparse
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def connection():
    return psycopg2.connect(host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with connection() as conn, conn.cursor() as cur:
        # Schema changes belong to migrations (sql/ingestion_observability.sql).
        # Even an IF NOT EXISTS ALTER takes ACCESS EXCLUSIVE and, when kept in
        # this transaction, blocks every API read for the duration of the large
        # canonicalization update below.
        cur.execute("SET LOCAL lock_timeout = '5s'")
        # Prefer a requisition identifier embedded in the source id. Otherwise use
        # stable employer/title/description/location evidence. This groups likely
        # mirrors while retaining every posting and every source-specific URL.
        cur.execute("""
            WITH candidates AS (
                SELECT jp.job_id,
                    'CO' || substr(md5(concat_ws('|',
                        COALESCE(jp.company_id, 'unknown'),
                        regexp_replace(lower(COALESCE(r.role_name, '')), '[^a-z0-9]+', '', 'g'),
                        COALESCE(NULLIF(jp.desc_hash, ''), jp.source_id),
                        COALESCE(jp.loc_country, ''), COALESCE(jp.loc_state, ''),
                        COALESCE(jp.loc_city, ''))), 1, 20) AS canonical_id
                FROM job_postings jp
                LEFT JOIN roles r ON r.role_id = jp.role_id
                WHERE jp.data_tier = 1
            )
            UPDATE job_postings jp
               SET canonical_opportunity_id = candidates.canonical_id
              FROM candidates
             WHERE jp.job_id = candidates.job_id
               AND jp.canonical_opportunity_id IS DISTINCT FROM candidates.canonical_id
        """)
        changed = cur.rowcount
        if not args.apply:
            conn.rollback()
        print(f"{'Would update' if not args.apply else 'Updated'} {changed} postings")


if __name__ == "__main__":
    main()
