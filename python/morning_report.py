#!/usr/bin/env python3
"""
morning_report.py

Sends a daily pipeline health email to jones31luke@gmail.com.
Run after dbt completes — add to cron at 11:30am UTC.
"""

import os, smtplib, psycopg2
from datetime import datetime, timezone, timedelta
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env", override=True)

GMAIL_USER     = "jones31luke@gmail.com"
GMAIL_APP_PASS = os.getenv("GMAIL_APP_PASSWORD")
TO_EMAIL       = "jones31luke@gmail.com"

DB_CONFIG = dict(
    host=os.getenv("PGHOST"),
    port=int(os.getenv("PGPORT", 5432)),
    dbname=os.getenv("PGDATABASE"),
    user=os.getenv("PGUSER"),
    password=os.getenv("PGPASSWORD"),
)

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def build_report() -> str:
    conn = get_conn()
    cur  = conn.cursor()
    now  = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # ── Active jobs by source ─────────────────────────────────────────────────
    cur.execute("""
        SELECT source, COUNT(*) as active,
            COUNT(*) FILTER (WHERE salary_max_annual IS NOT NULL) as with_salary,
            MAX(last_seen_at) as last_seen
        FROM job_postings
        WHERE data_tier=1 AND status='raw'
        GROUP BY source ORDER BY active DESC
    """)
    sources = cur.fetchall()
    total_active = sum(r[1] for r in sources)

    # ── Yesterday's activity ──────────────────────────────────────────────────
    cur.execute("""
        SELECT source,
            COUNT(*) FILTER (WHERE ingested_at >= now() - interval '24 hours') as new_today,
            COUNT(*) FILTER (WHERE last_seen_at >= now() - interval '24 hours'
                AND ingested_at < now() - interval '24 hours') as reactivated
        FROM job_postings
        WHERE data_tier=1
        GROUP BY source ORDER BY new_today DESC
    """)
    activity = {r[0]: (r[1], r[2]) for r in cur.fetchall()}

    # ── Expiry health ─────────────────────────────────────────────────────────
    cur.execute("""
        SELECT COUNT(*) FROM job_postings
        WHERE data_tier=1 AND status='expired'
          AND last_seen_at >= now() - interval '24 hours'
    """)
    expired_today = cur.fetchone()[0]

    # ── Honesty index from mart ───────────────────────────────────────────────
    cur.execute("""
        SELECT ROUND(AVG(avg_honesty_score),1),
               COUNT(*) FILTER (WHERE avg_honesty_score >= 80),
               COUNT(*) FILTER (WHERE avg_honesty_score < 60)
        FROM analytics_analytics.mart_company_scorecard
    """)
    honesty_row = cur.fetchone()
    ghost = {'avg_honesty': honesty_row[0], 'high_honesty_companies': honesty_row[1], 'low_honesty_companies': honesty_row[2]}

    # ── Total tier 1 ──────────────────────────────────────────────────────────
    cur.execute("SELECT COUNT(*) FROM job_postings WHERE data_tier=1")
    total_tier1 = cur.fetchone()[0]

    cur.execute("SELECT COUNT(*) FROM job_postings WHERE data_tier=1 AND status='expired'")
    total_expired = cur.fetchone()[0]

    # ── Salary transparency ───────────────────────────────────────────────────
    cur.execute("""
        SELECT ROUND(AVG(CASE WHEN salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END)*100, 1)
        FROM job_postings WHERE data_tier=1 AND status='raw'
    """)
    salary_pct = cur.fetchone()[0]

    # ── Top skills ────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT s.skill_name, COUNT(DISTINCT js.job_id) as cnt
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        JOIN job_postings jp ON jp.job_id = js.job_id
        WHERE jp.data_tier=1 AND jp.status='raw'
        GROUP BY s.skill_name ORDER BY cnt DESC LIMIT 8
    """)
    top_skills = cur.fetchall()

    # ── Pipeline runs ─────────────────────────────────────────────────────────
    cur.execute("""
        SELECT run_id, status, jobs_inserted, started_at
        FROM pipeline_runs
        WHERE started_at >= now() - interval '24 hours'
        ORDER BY started_at DESC
    """)
    runs = cur.fetchall()

    # ── Enrichment coverage ───────────────────────────────────────────────────
    cur.execute("""
        SELECT
            COUNT(*) FILTER (WHERE experience_level IS NULL) as missing_level,
            COUNT(*) FILTER (WHERE workplace_type IS NULL) as missing_workplace,
            COUNT(*) FILTER (WHERE salary_max_annual IS NULL
                AND description_text ~* '\\$[0-9]') as has_salary_lang
        FROM job_postings
        WHERE data_tier=1 AND status='raw'
    """)
    enrich = cur.fetchone()

    conn.close()

    # ── Build HTML email ──────────────────────────────────────────────────────
    date_str = now.strftime("%A, %B %d %Y")

    source_rows = ""
    for source, active, with_sal, last_seen in sources:
        new, react = activity.get(source, (0, 0))
        sal_pct = round(with_sal / active * 100) if active else 0
        hours_ago = round((now - last_seen).total_seconds() / 3600, 1) if last_seen else "?"
        status_icon = "✅" if hours_ago != "?" and hours_ago < 30 else "⚠️"
        source_rows += f"""
        <tr>
            <td>{status_icon} {source}</td>
            <td><b>{active:,}</b></td>
            <td>+{new}</td>
            <td>{react}</td>
            <td>{sal_pct}%</td>
            <td>{hours_ago}h ago</td>
        </tr>"""

    run_rows = ""
    for run_id, status, inserted, started in runs[:10]:
        icon = "✅" if status == "success" else "❌"
        run_rows += f"<tr><td>{icon} {run_id}</td><td>{inserted:,} inserted</td><td>{started.strftime('%H:%M UTC')}</td></tr>"

    ghost_str = " | ".join([f"honesty {k}: {v}" for k, v in ghost.items()])
    skill_str = " &bull; ".join([f"{s[0]} ({s[1]:,})" for s in top_skills])

    alerts = []
    if expired_today > 500:
        alerts.append(f"⚠️ <b>{expired_today:,} jobs expired</b> in last 24h — possible ingestion issue")
    if enrich[2] > 100:
        alerts.append(f"💰 <b>{enrich[2]:,} jobs</b> have salary language but null salary — re-enrich recommended")
    if enrich[0] > 200:
        alerts.append(f"🎓 <b>{enrich[0]:,} jobs</b> missing experience level")
    if not alerts:
        alerts.append("✅ No anomalies detected")

    alert_html = "<br>".join(alerts)

    html = f"""
<html><body style="font-family: -apple-system, sans-serif; max-width: 700px; margin: 0 auto; padding: 20px; color: #1a1a1a;">

<h2 style="color: #6C3CE1; border-bottom: 2px solid #6C3CE1; padding-bottom: 8px;">
    📊 DataHiringIQ Morning Report
</h2>
<p style="color: #666; margin-top: -10px;">{date_str}</p>

<div style="background: #f8f4ff; border-left: 4px solid #6C3CE1; padding: 12px 16px; margin-bottom: 20px;">
    <b style="font-size: 24px;">{total_active:,}</b> active Tier 1 jobs &nbsp;|&nbsp;
    <b>{total_tier1:,}</b> total in DB &nbsp;|&nbsp;
    <b>{total_expired:,}</b> expired &nbsp;|&nbsp;
    <b>{salary_pct}%</b> salary transparent
</div>

<h3>Pipeline Status</h3>
<table style="width:100%; border-collapse: collapse; font-size: 14px;">
    <tr style="background: #6C3CE1; color: white;">
        <th style="padding: 8px; text-align:left;">Source</th>
        <th style="padding: 8px;">Active</th>
        <th style="padding: 8px;">New</th>
        <th style="padding: 8px;">Reactivated</th>
        <th style="padding: 8px;">Salary %</th>
        <th style="padding: 8px;">Last Seen</th>
    </tr>
    {source_rows}
</table>

<h3 style="margin-top: 20px;">Ghost Index</h3>
<p style="font-size: 14px;">{ghost_str}</p>

<h3>Top Skills (active jobs)</h3>
<p style="font-size: 13px; color: #444;">{skill_str}</p>

<h3>Alerts</h3>
<p style="font-size: 14px;">{alert_html}</p>

<h3>Enrichment Gaps</h3>
<p style="font-size: 14px;">
    Missing experience level: <b>{enrich[0]:,}</b> &nbsp;|&nbsp;
    Missing workplace type: <b>{enrich[1]:,}</b> &nbsp;|&nbsp;
    Salary language but null: <b>{enrich[2]:,}</b>
</p>

<h3>Pipeline Runs (last 24h)</h3>
<table style="width:100%; border-collapse: collapse; font-size: 13px;">
    {run_rows if run_rows else '<tr><td colspan=3>No runs logged</td></tr>'}
</table>

<hr style="margin-top: 30px; border: none; border-top: 1px solid #eee;">
<p style="font-size: 12px; color: #999;">
    datahiringiq.com &nbsp;|&nbsp; api.datahiringiq.com &nbsp;|&nbsp;
    Generated {now.strftime('%H:%M UTC')}
</p>

</body></html>
"""
    return html


def send_email(html: str):
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📊 DataHiringIQ Daily Report — {datetime.now().strftime('%b %d')}"
    msg["From"]    = GMAIL_USER
    msg["To"]      = TO_EMAIL
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.ehlo()
        server.starttls()
        server.login(GMAIL_USER, GMAIL_APP_PASS)
        server.sendmail(GMAIL_USER, TO_EMAIL, msg.as_string())
    print("✅ Morning report sent")


if __name__ == "__main__":
    html = build_report()
    send_email(html)
    print("Done")
