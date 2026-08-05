#!/usr/bin/env python3
"""Print the role-admission funnel and highest-volume review candidates."""
from __future__ import annotations

import os
import psycopg2


def connection():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def main() -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute("""
            SELECT status, count(*), sum(seen_count)
            FROM role_scope_decisions
            WHERE last_seen_at >= now() - interval '24 hours'
            GROUP BY status ORDER BY status
        """)
        print("ROLE SCOPE — LAST 24 HOURS")
        for status, titles, observations in cur.fetchall():
            print(f"  {status:20} {titles:7,d} listings  {observations:7,d} observations")

        cur.execute("""
            SELECT title, company, rule_id, seen_count
            FROM role_scope_decisions
            WHERE status='quarantine'
            ORDER BY last_seen_at DESC, seen_count DESC
            LIMIT 30
        """)
        print("\nLATEST QUARANTINE CANDIDATES")
        for title, company, rule_id, seen_count in cur.fetchall():
            print(f"  [{rule_id}] {title} — {company or 'Unknown'} ({seen_count}x)")


if __name__ == "__main__":
    main()
