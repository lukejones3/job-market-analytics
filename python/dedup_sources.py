#!/usr/bin/env python3
"""
dedup_sources.py

Enforces single-source priority for companies tracked across multiple ATS sources.
Priority: greenhouse > lever > ashby

Run after adding new companies or periodically as a maintenance task.

Usage:
    python python/dedup_sources.py          # dry run
    python python/dedup_sources.py --apply  # apply changes
"""

import os, argparse, psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
from pathlib import Path

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

PRIORITY = {"greenhouse": 1, "lever": 2, "ashby": 3}

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    # Find companies on multiple Tier 1 sources
    cur.execute("""
        SELECT company_name,
               array_agg(DISTINCT ats_source) as sources,
               COUNT(DISTINCT ats_source) as source_count
        FROM discovered_companies
        WHERE enabled = true
        AND ats_source IN ('greenhouse', 'lever', 'ashby')
        GROUP BY company_name
        HAVING COUNT(DISTINCT ats_source) > 1
        ORDER BY company_name
    """)
    overlaps = cur.fetchall()

    if not overlaps:
        print("✅ No multi-source overlaps found")
        conn.close()
        return

    print(f"Found {len(overlaps)} companies on multiple Tier 1 sources:\n")
    to_disable = []

    for row in overlaps:
        name = row["company_name"]
        sources = row["sources"]

        # Find the best source that actually has active roles
        # Priority: greenhouse > lever > ashby
        # Only use a source if it has active_roles > 0
        cur.execute("""
            SELECT ats_source, active_roles
            FROM discovered_companies
            WHERE lower(company_name) = lower(%s)
            AND ats_source IN ('greenhouse', 'lever', 'ashby')
            AND enabled = true
            ORDER BY active_roles DESC
        """, (name,))
        source_roles = {r["ats_source"]: r["active_roles"] for r in cur.fetchall()}

        # Find highest priority source WITH active roles
        best = None
        for src in ["greenhouse", "lever", "ashby"]:
            if src in source_roles and source_roles[src] > 0:
                best = src
                break

        # If no source has active roles, keep highest priority anyway
        if best is None:
            best = min(sources, key=lambda s: PRIORITY.get(s, 99))

        disable = [s for s in sources if s != best]
        if disable:
            roles_info = {s: source_roles.get(s, 0) for s in sources}
            print(f"  {name:<25} roles={roles_info} → keep={best}, disable={disable}")
            for s in disable:
                to_disable.append((name, s))
        else:
            print(f"  {name:<25} → no change needed")

    if not to_disable:
        print("\n✅ Nothing to disable")
        conn.close()
        return

    print(f"\n{len(to_disable)} source entries to disable")

    if args.apply:
        for name, source in to_disable:
            cur.execute("""
                UPDATE discovered_companies
                SET enabled = false
                WHERE company_name = %s
                AND ats_source = %s
            """, (name, source))
        conn.commit()
        print("✅ Applied — lower priority sources disabled")
    else:
        print("Dry run — pass --apply to disable")

    conn.close()

if __name__ == "__main__":
    main()
