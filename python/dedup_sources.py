#!/usr/bin/env python3
"""
dedup_sources.py

Checks for companies tracked across multiple ATS sources.
Deduplication now happens at the job level (company + title + location)
in ingest_jobs.py, so all sources remain enabled.

This script is kept for monitoring/reporting purposes only.

Usage:
    python python/dedup_sources.py
"""

import os, psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def main():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("""
        SELECT company_name,
               array_agg(DISTINCT ats_source ORDER BY ats_source) as sources,
               array_agg(DISTINCT ats_source || '=' || active_roles::text) as role_counts
        FROM discovered_companies
        WHERE enabled = true
        AND active_roles > 0
        AND ats_source IN ('greenhouse', 'lever', 'ashby')
        GROUP BY company_name
        HAVING COUNT(DISTINCT ats_source) > 1
        ORDER BY company_name
    """)
    overlaps = cur.fetchall()

    if not overlaps:
        print("✅ No companies actively hiring on multiple Tier 1 sources")
    else:
        print(f"Companies actively hiring on multiple sources ({len(overlaps)}):")
        print(f"  (dedup handled at job level — company+title+location)\n")
        for row in overlaps:
            print(f"  {row['company_name']:<25} {row['role_counts']}")

    conn.close()

if __name__ == "__main__":
    main()
