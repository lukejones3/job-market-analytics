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

    # 2. New vs reactivated jobs last 24h
    print("\n📥 JOBS ACTIVITY (last 24h):")
    cur.execute("""
        SELECT
            COALESCE(ingestion_source, 'manual') as source,
            COUNT(*) FILTER (WHERE ingested_at > now() - interval '24 hours') as truly_new,
            COUNT(*) FILTER (
                WHERE last_seen_at > now() - interval '24 hours'
                AND ingested_at < now() - interval '24 hours'
            ) as reactivated,
            COUNT(*) FILTER (WHERE status = 'raw') as currently_active
        FROM job_postings
        WHERE data_tier = 1
        AND (ingested_at > now() - interval '24 hours'
             OR last_seen_at > now() - interval '24 hours')
        GROUP BY ingestion_source
        ORDER BY truly_new DESC
    """)
    rows = cur.fetchall()
    if not rows:
        print("  ⚠️  No activity in last 24 hours")
    total_new = total_reactivated = 0
    for r in rows:
        print(f"  [{r['source']}] +{r['truly_new']} new | ↩️  {r['reactivated']} reactivated | {r['currently_active']} active")
        total_new += r['truly_new'] or 0
        total_reactivated += r['reactivated'] or 0
    print(f"  TOTAL: +{total_new} genuinely new | ↩️  {total_reactivated} reactivated")

    # 3. Total DB state (Tier 1 only)
    print("\n📦 TIER 1 DATABASE STATE:")
    cur.execute("""
        SELECT
            COALESCE(ingestion_source, 'manual') as source,
            COUNT(*) as total,
            COUNT(*) FILTER (WHERE status = 'raw') as active,
            COUNT(*) FILTER (WHERE status = 'expired') as expired,
            ROUND(AVG(CASE WHEN salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100, 1) as salary_pct,
            MAX(last_seen_at) as latest
        FROM job_postings
        WHERE data_tier = 1
        GROUP BY ingestion_source
        ORDER BY active DESC
    """)
    total_active = total_all = 0
    for r in cur.fetchall():
        print(f"  [{r['source']}] {r['active']} active / {r['total']} total | salary={r['salary_pct']}% | seen={r['latest'].strftime('%Y-%m-%d %H:%M')}")
        total_active += r['active'] or 0
        total_all += r['total'] or 0
    print(f"  TOTAL: {total_active} active / {total_all} total Tier 1 jobs")

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
        ORDER BY open_roles DESC NULLS LAST
    """)
    for r in cur.fetchall():
        print(f"  [{r['ats_source']}] {r['hiring']}/{r['total']} companies | {r['open_roles'] or 0} open roles")

    # 5. Expiry health
    print("\n💀 EXPIRY HEALTH (last 24h):")
    cur.execute("""
        SELECT
            COALESCE(ingestion_source, 'manual') as source,
            COUNT(*) as expired_today
        FROM job_postings
        WHERE data_tier = 1
        AND status = 'expired'
        AND last_seen_at > now() - interval '48 hours'
        AND last_seen_at < now() - interval '24 hours'
        GROUP BY ingestion_source
        ORDER BY expired_today DESC
    """)
    rows = cur.fetchall()
    if not rows:
        print("  ✅ No unusual expiry activity")
    else:
        for r in rows:
            print(f"  [{r['source']}] {r['expired_today']} jobs expired")

    # 6. Ghost job snapshot
    print("\n👻 GHOST JOB INDEX:")
    try:
        cur.execute("""
            SELECT ghost_tier, COUNT(*) as jobs
            FROM vw_ghost_job_index
            GROUP BY ghost_tier
            ORDER BY
                CASE ghost_tier
                    WHEN 'high' THEN 1
                    WHEN 'medium' THEN 2
                    WHEN 'low' THEN 3
                    ELSE 4
                END
        """)
        for r in cur.fetchall():
            icon = "🔴" if r['ghost_tier'] == 'high' else "🟡" if r['ghost_tier'] == 'medium' else "🟢" if r['ghost_tier'] == 'low' else "✨"
            print(f"  {icon} {r['ghost_tier']}: {r['jobs']} jobs")
    except Exception:
        print("  ⚠️  Ghost index not available")

    # 7. Top skills
    print("\n🔥 TOP SKILLS (active Tier 1):")
    cur.execute("""
        SELECT s.skill_name, COUNT(DISTINCT js.job_id) as jobs
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        JOIN job_postings jp ON jp.job_id = js.job_id
        WHERE jp.data_tier = 1 AND jp.status = 'raw'
        GROUP BY s.skill_name
        ORDER BY jobs DESC
        LIMIT 10
    """)
    for r in cur.fetchall():
        print(f"  {r['skill_name']:<22} {r['jobs']} jobs")

    # 8. Sector snapshot
    print("\n🏭 SECTOR SNAPSHOT (active Tier 1, top 10):")
    cur.execute("""
        SELECT
            COALESCE(c.sector, 'Unclassified') as sector,
            COUNT(*) as active_roles,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100) as transparency_pct
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        WHERE jp.data_tier = 1 AND jp.status = 'raw'
        GROUP BY c.sector
        ORDER BY active_roles DESC
        LIMIT 10
    """)
    for r in cur.fetchall():
        print(f"  {r['sector']:<25} {r['active_roles']} roles | {r['transparency_pct']}% transparent")

    # 9. 7-day trend
    print("\n📈 7-DAY INGESTION TREND (Tier 1):")
    cur.execute("""
        SELECT
            DATE(ingested_at) as date,
            COUNT(*) as new_jobs,
            COUNT(*) FILTER (WHERE ingestion_source = 'greenhouse') as gh,
            COUNT(*) FILTER (WHERE ingestion_source = 'lever') as lv,
            COUNT(*) FILTER (WHERE ingestion_source = 'workday') as wd
        FROM job_postings
        WHERE data_tier = 1
        AND ingested_at > now() - interval '7 days'
        GROUP BY DATE(ingested_at)
        ORDER BY date DESC
    """)
    for r in cur.fetchall():
        print(f"  {r['date']} | {r['new_jobs']} new | GH={r['gh']} LV={r['lv']} WD={r['wd']}")

    print("\n" + "=" * 60)
    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
