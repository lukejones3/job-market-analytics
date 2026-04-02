#!/usr/bin/env python3
"""
morning_check.py
Run every morning to confirm overnight pipeline ran correctly.

Usage: python python/morning_check.py
"""
import os
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST","localhost"),
        port=int(os.getenv("PGPORT",5432)),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD")
    )

def main():
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    print("=" * 60)
    print("📊 MORNING PIPELINE HEALTH CHECK")
    print("=" * 60)

    # 1. Pipeline runs from last 24 hours
    print("\n🔄 PIPELINE RUNS (last 24h):")
    cur.execute("""
        SELECT run_id, status, started_at,
               jobs_inserted, jobs_skipped, jobs_errored
        FROM pipeline_runs
        WHERE started_at > now() - interval '24 hours'
        ORDER BY started_at DESC
    """)
    runs = cur.fetchall()
    if not runs:
        print("  ⚠️  NO RUNS FOUND — cron may not have fired!")
    for r in runs:
        status_icon = "✅" if r["status"] == "success" else "❌"
        print(f"  {status_icon} {r['run_id']}")
        print(f"     Started: {r['started_at']}")
        print(f"     Inserted: {r['jobs_inserted'] or 0} | Skipped: {r['jobs_skipped'] or 0} | Errors: {r['jobs_errored'] or 0}")

    # 2. Jobs ingested last 24h by source
    print("\n📥 NEW JOBS (last 24h):")
    cur.execute("""
        SELECT 
            COALESCE(ingestion_source, 'manual') as source,
            COUNT(*) as new_jobs,
            MIN(ingested_at) as first_seen,
            MAX(ingested_at) as last_seen
        FROM job_postings
        WHERE ingested_at > now() - interval '24 hours'
        GROUP BY ingestion_source
        ORDER BY new_jobs DESC
    """)
    rows = cur.fetchall()
    if not rows:
        print("  ⚠️  No new jobs in last 24 hours")
    for r in rows:
        print(f"  [{r['source']}] {r['new_jobs']} jobs | {r['first_seen'].strftime('%H:%M')} → {r['last_seen'].strftime('%H:%M')}")

    # 3. Total DB state
    print("\n📦 TOTAL DATABASE STATE:")
    cur.execute("""
        SELECT 
            COALESCE(ingestion_source, 'manual') as source,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE experience_level IS NOT NULL) as has_exp,
            COUNT(*) FILTER (WHERE salary_min IS NOT NULL) as has_salary,
            MAX(ingested_at) as latest
        FROM job_postings
        GROUP BY ingestion_source
        ORDER BY total DESC
    """)
    total = 0
    for r in cur.fetchall():
        print(f"  [{r['source']}] {r['total']} jobs | exp={r['has_exp']} salary={r['has_salary']} | latest={r['latest'].strftime('%Y-%m-%d %H:%M')}")
        total += r['total']
    print(f"  TOTAL: {total} jobs")

    # 4. Active companies
    print("\n🏢 ACTIVE COMPANIES:")
    cur.execute("""
        SELECT ats_source,
               COUNT(*) FILTER (WHERE active_roles > 0) as hiring,
               COUNT(*) as total,
               SUM(active_roles) as open_roles
        FROM discovered_companies
        WHERE enabled = true
        GROUP BY ats_source
    """)
    for r in cur.fetchall():
        print(f"  [{r['ats_source']}] {r['hiring']}/{r['total']} companies hiring | {r['open_roles']} open roles")

    # 5. Skill demand snapshot (top 10)
    print("\n🔥 TOP SKILLS (Tier 1):")
    cur.execute("""
        SELECT skill_name, SUM(jobs_with_skill) as jobs
        FROM analytics_analytics.mart_skill_demand
        GROUP BY skill_name
        ORDER BY jobs DESC
        LIMIT 10
    """)
    for r in cur.fetchall():
        print(f"  {r['skill_name']:<20} {r['jobs']} jobs")

    # 6. 7-day trend
    print("\n📈 7-DAY INGESTION TREND:")
    cur.execute("""
        SELECT 
            DATE(ingested_at) as date,
            COUNT(*) as jobs,
            COUNT(*) FILTER (WHERE ingestion_source = 'greenhouse') as gh,
            COUNT(*) FILTER (WHERE ingestion_source = 'adzuna') as az
        FROM job_postings
        WHERE ingested_at > now() - interval '7 days'
        GROUP BY DATE(ingested_at)
        ORDER BY date DESC
    """)
    for r in cur.fetchall():
        print(f"  {r['date']} | {r['jobs']} total | GH={r['gh']} AZ={r['az']}")

    print("\n" + "=" * 60)
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
