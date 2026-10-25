"""Read-only operational readiness checks. Exit nonzero when checks fail."""
from __future__ import annotations
from contextlib import closing
import json
import os
import shutil
from datetime import datetime, timezone

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor


def inspect(conn) -> dict:
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SET TRANSACTION READ ONLY")
        cur.execute("SET LOCAL statement_timeout='30s'")
        queries = {
            'inventory': """SELECT count(*)::int AS published_jobs,count(DISTINCT company_id)::int AS companies,
                count(*) FILTER(WHERE workplace_type IS NULL OR workplace_type='unknown')::int AS unknown_workplace,
                count(*) FILTER(WHERE experience_level IS NULL OR experience_level='unknown')::int AS unknown_experience
                FROM job_postings WHERE is_public=true""",
            'seo': """SELECT (SELECT count(*) FROM seo_company_index WHERE job_count>=5) AS companies,
                (SELECT count(*) FROM seo_skill_index WHERE job_count>=5) AS skills,
                (SELECT count(*) FROM seo_role_location_index WHERE job_count>=5) AS collections""",
            'publication': "SELECT max(published_at)::text AS latest,EXTRACT(EPOCH FROM now()-max(published_at))/3600 AS age_hours FROM publication_runs",
            'hosts': """SELECT count(*)::int AS attempts,count(*) FILTER(WHERE status='failed')::int AS failures
                FROM career_host_runs WHERE started_at>now()-interval '14 days'""",
            'hosts_24h': """SELECT count(*)::int AS attempts,count(*) FILTER(WHERE status='failed')::int AS failures
                FROM career_host_runs WHERE started_at>now()-interval '24 hours'""",
            'indexing': """SELECT notification_type,count(*) FILTER(WHERE sent_at IS NULL)::int AS pending,min(queued_at) FILTER(WHERE sent_at IS NULL)::text AS oldest,
                max(sent_at)::text AS last_success FROM seo_indexing_queue
                GROUP BY notification_type""",
            'retention': "SELECT count(*)::int AS expired_resumes FROM resumes WHERE uploaded_at<now()-interval '30 days'",
            'connections': "SELECT count(*)::int AS used,current_setting('max_connections')::int AS maximum FROM pg_stat_activity",
            'funnel': "SELECT event_name,count(DISTINCT user_id)::int AS accounts FROM product_lifecycle_events WHERE occurred_at>now()-interval '30 days' GROUP BY event_name",
        }
        result = {}
        for name, sql in queries.items():
            cur.execute(sql)
            result[name] = [dict(row) for row in cur.fetchall()]
    alerts = []
    if float(result['publication'][0]['age_hours'] or 9999) > 36:
        alerts.append('Published inventory is over 36 hours old')
    for queue in result['indexing']:
        if queue['pending'] and queue['oldest'] and (datetime.now(timezone.utc)-datetime.fromisoformat(queue['oldest'])).total_seconds()>7*86400:
            alerts.append(queue['notification_type']+' notification backlog exceeds seven days')
    hosts = result['hosts'][0]
    if hosts['attempts'] and hosts['failures'] == hosts['attempts']:
        alerts.append('All direct-host attempts failed in the last 14 days')
    recent = result['hosts_24h'][0]
    if recent['attempts'] >= 5 and recent['failures'] / recent['attempts'] >= .5:
        alerts.append('Direct-host failure rate is at least 50% in the last 24 hours')
    if result['retention'][0]['expired_resumes']:
        alerts.append('Resume retention overdue')
    connections = result['connections'][0]
    if connections['used'] >= .8 * connections['maximum']:
        alerts.append('PostgreSQL connection usage above 80%')
    disk = shutil.disk_usage('/')
    result['disk_used_percent'] = round(100 * disk.used / disk.total, 1)
    if result['disk_used_percent'] >= 85:
        alerts.append('Filesystem usage above 85%')
    result['alerts'] = alerts
    return result


if __name__ == '__main__':
    load_dotenv()
    with closing(psycopg2.connect(host=os.getenv('PGHOST'), port=os.getenv('PGPORT', '5432'),
        dbname=os.getenv('PGDATABASE'), user=os.getenv('PGUSER'), password=os.getenv('PGPASSWORD'))) as conn:
        report = inspect(conn)
    print(json.dumps(report, sort_keys=True, default=str))
    raise SystemExit(bool(report['alerts']))
