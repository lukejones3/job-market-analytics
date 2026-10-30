#!/usr/bin/env python3
"""Send a trustworthy, frontend-aligned Lander operations brief."""
from __future__ import annotations

import html
import argparse
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env", override=True)

TO_EMAIL = os.getenv("MORNING_REPORT_TO", "jones31luke@gmail.com")
DB_CONFIG = {
    "host": os.getenv("PGHOST"), "port": int(os.getenv("PGPORT", 5432)),
    "dbname": os.getenv("PGDATABASE"), "user": os.getenv("PGUSER"),
    "password": os.getenv("PGPASSWORD"),
}


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def _n(value) -> str:
    return f"{int(value or 0):,}"


def pipeline_status() -> dict:
    url = os.getenv("AIRFLOW__DATABASE__SQL_ALCHEMY_CONN", "")
    if not url:
        return {"state": "unavailable", "failed": []}
    try:
        conn = psycopg2.connect(url.replace("postgresql+psycopg2://", "postgresql://"), connect_timeout=5)
        try:
            with conn.cursor() as cur:
                cur.execute("SET statement_timeout='5s'")
                cur.execute("SELECT run_id,state FROM dag_run WHERE dag_id='lander_nightly' ORDER BY start_date DESC NULLS LAST LIMIT 1")
                row = cur.fetchone()
                if not row:
                    return {"state": "missing", "failed": []}
                cur.execute("SELECT task_id FROM task_instance WHERE dag_id='lander_nightly' AND run_id=%s AND state='failed' ORDER BY task_id", (row[0],))
                return {"state": row[1], "run_id": row[0], "failed": [r[0] for r in cur.fetchall()]}
        finally:
            conn.close()
    except psycopg2.Error as exc:
        return {"state": "unavailable", "failed": [], "error": type(exc).__name__}


def report_state(crawl_bad, publication, pipeline, now) -> str:
    if not publication or (now-publication[0]).total_seconds() > 36*3600:
        return "Needs attention"
    if crawl_bad or pipeline["state"] in ("failed", "missing", "unavailable"):
        return "Needs attention"
    return "In progress" if pipeline["state"] in ("running", "queued") else "Healthy"


def build_report() -> str:
    conn = get_conn()
    cur = conn.cursor()
    now = datetime.now(timezone.utc)

    # Match the frontend boundary exactly: is_public, not every raw Tier-1 row.
    cur.execute("""
        SELECT COUNT(*),
               COUNT(*) FILTER (WHERE salary_min_annual IS NOT NULL OR salary_max_annual IS NOT NULL),
               COUNT(*) FILTER (WHERE published_at >= now() - interval '24 hours'),
               COUNT(*) FILTER (WHERE experience_level_v3 IS NULL),
               COUNT(*) FILTER (WHERE workplace_type IS NULL OR lower(workplace_type) IN ('unknown',''))
        FROM job_postings WHERE is_public
    """)
    public_count, salary_count, new_24h, missing_level, missing_workplace = cur.fetchone()
    salary_pct = round(100 * salary_count / public_count, 1) if public_count else 0

    cur.execute("""
        SELECT COALESCE(ingestion_source, source, 'unknown'), COUNT(*),
               COUNT(*) FILTER (WHERE salary_min_annual IS NOT NULL OR salary_max_annual IS NOT NULL),
               COUNT(*) FILTER (WHERE published_at >= now() - interval '24 hours'),
               MAX(last_seen_at)
        FROM job_postings WHERE is_public
        GROUP BY 1 ORDER BY 2 DESC
    """)
    sources = cur.fetchall()

    cur.execute("""
        SELECT orchestration_run_id
        FROM ingestion_crawl_runs
        WHERE orchestration_run_id IS NOT NULL
        ORDER BY started_at DESC LIMIT 1
    """)
    orchestration_row = cur.fetchone()
    orchestration_id = orchestration_row[0] if orchestration_row else None
    crawls = []
    if orchestration_id:
        cur.execute("""
            SELECT source,
                   CASE WHEN bool_and(status IN ('complete_nonzero','complete_zero'))
                     THEN 'complete_nonzero' ELSE 'partial_failure' END,
                   sum(jobs_fetched),sum(jobs_written),sum(errors),
                   EXTRACT(epoch FROM (max(finished_at) - min(started_at)))::int
            FROM ingestion_crawl_runs
            WHERE orchestration_run_id = %s
            GROUP BY source ORDER BY source
        """, (orchestration_id,))
        crawls = cur.fetchall()

    cur.execute("""
        SELECT published_at, prior_count, candidate_count, activated_count, deactivated_count
        FROM publication_runs ORDER BY published_at DESC LIMIT 1
    """)
    publication = cur.fetchone()

    cur.execute("""
        SELECT COUNT(*)
        FROM job_postings
        WHERE is_public AND salary_min_annual IS NULL AND salary_max_annual IS NULL
          AND description_text LIKE '%$%'
          AND description_text ~* '(salary|compensation|pay range|base pay|pay rate|hourly rate|wage)'
    """)
    salary_candidates = cur.fetchone()[0]

    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE signed_up_at >= now() - interval '24 hours'), COUNT(*)
        FROM free_signups
    """)
    signups_24h, total_signups = cur.fetchone()
    cur.execute("""
        SELECT COUNT(*) FILTER (WHERE tier='pro' AND active),
               COUNT(*) FILTER (WHERE tier='free' AND active)
        FROM api_keys
    """)
    active_pro, active_free = cur.fetchone()
    conn.close()

    crawl_bad = [r for r in crawls if r[1] not in ("complete_nonzero", "complete_zero") or (r[4] or 0) > 0]
    pipeline = pipeline_status()
    for task in pipeline['failed']:
        if task.startswith('ingest_') and task != 'ingest_quality_gate':
            source = task.removeprefix('ingest_')
            crawls = [row for row in crawls if row[0] != source] + [
                (source, 'source_failure', 0, 0, 1, 0)]
    crawl_bad = [r for r in crawls if r[1] not in ('complete_nonzero','complete_zero') or (r[4] or 0) > 0]
    overall = report_state(crawl_bad, publication, pipeline, now)
    accent = "#b8f36b" if overall == "Healthy" else "#ff8c69"

    source_rows = "".join(
        f"<tr><td><b>{html.escape(str(src))}</b></td><td>{_n(count)}</td>"
        f"<td>{_n(salary)} <span>{round(100*salary/count) if count else 0}%</span></td>"
        f"<td>+{_n(new)}</td><td>{f'{(now-last_seen).total_seconds()/3600:.1f}h' if last_seen else 'unknown'}</td></tr>"
        for src, count, salary, new, last_seen in sources
    )
    crawl_rows = "".join(
        f"<tr><td><b>{html.escape(str(src))}</b></td>"
        f"<td class='status'>{'OK' if status in ('complete_nonzero','complete_zero') and not errors else 'CHECK'}</td>"
        f"<td>{_n(fetched)}</td><td>{_n(written)}</td><td>{seconds or 0}s</td></tr>"
        for src, status, fetched, written, errors, seconds in crawls
    ) or "<tr><td colspan='5'>No crawl record found</td></tr>"

    pub_text = "No publication recorded"
    if publication:
        published_at, prior, candidate, activated, deactivated = publication
        pub_text = (f"Published {_n(candidate)} jobs at {published_at:%Y-%m-%d %H:%M} UTC · "
                    f"{_n(activated)} activated · {_n(deactivated)} removed · prior {_n(prior)}")

    return f"""<!doctype html><html><head><meta name='viewport' content='width=device-width'></head>
<body style='margin:0;background:#0b0c0e;color:#f4f4f0;font-family:Arial,Helvetica,sans-serif'>
<div style='display:none'>{_n(public_count)} live jobs · pipeline {overall.lower()}</div>
<div class='wrap' style='max-width:720px;margin:auto;padding:28px 18px'>
  <div style='border:1px solid #292b30;border-radius:22px;overflow:hidden;background:#121316'>
    <div style='padding:28px 30px;border-bottom:1px solid #292b30'>
      <div style='font-size:12px;letter-spacing:2px;color:#90939a'>LANDER / DAILY SYSTEM BRIEF</div>
      <h1 style='font-size:30px;margin:12px 0 6px'>The market, after the crawl.</h1>
      <div style='color:#9699a1'>{now:%A, %B %d} · generated {now:%H:%M} UTC</div>
    </div>
    <div style='padding:26px 30px'>
      <div style='display:inline-block;padding:7px 12px;border-radius:999px;background:{accent};color:#111;font-weight:700'>{overall}</div>
      <table role='presentation' width='100%' style='margin:24px 0;border-spacing:8px'><tr>
        <td class='metric'><b>{_n(public_count)}</b><span>live on frontend</span></td>
        <td class='metric'><b>{salary_pct}%</b><span>salary coverage</span></td>
        <td class='metric'><b>+{_n(new_24h)}</b><span>new in 24h</span></td>
      </tr></table>
      <div class='callout'>{html.escape(pub_text)}<br>Nightly run: {html.escape(pipeline['state'])} · Failed tasks: {html.escape(', '.join(pipeline['failed']) or 'none recorded')}</div>
      <h2>Source inventory</h2>
      <table class='data'><thead><tr><th>Source</th><th>Live</th><th>Salary</th><th>New</th><th>Fresh</th></tr></thead><tbody>{source_rows}</tbody></table>
      <h2>Latest ingestion</h2>
      <table class='data'><thead><tr><th>Source</th><th>State</th><th>Fetched</th><th>Written</th><th>Time</th></tr></thead><tbody>{crawl_rows}</tbody></table>
      <h2>Coverage requiring work</h2>
      <div class='grid'><div><b>{_n(salary_candidates)}</b><span>salary-text candidates still unresolved</span></div>
      <div><b>{_n(missing_level)}</b><span>public jobs missing level</span></div>
      <div><b>{_n(missing_workplace)}</b><span>public jobs missing workplace</span></div></div>
      <h2>Accounts</h2><div class='callout'>+{_n(signups_24h)} signups today · {_n(total_signups)} lifetime · {_n(active_pro)} pro · {_n(active_free)} active free</div>
    </div>
  </div>
  <div style='color:#686b72;font-size:12px;text-align:center;padding:18px'>datahiringiq.com · frontend-aligned metrics</div>
</div>
<style>
  h2{{font-size:15px;margin:28px 0 10px;color:#d7d8d4}} table.data{{width:100%;border-collapse:collapse;font-size:13px}}
  .data th{{color:#777b83;text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:1px;padding:9px 7px;border-bottom:1px solid #2b2d31}}
  .data td{{padding:10px 7px;border-bottom:1px solid #202226;color:#d9dad6}} .data td span{{color:#777b83}}
  .metric{{background:#1a1c20;border:1px solid #292c31;border-radius:14px;padding:16px}} .metric b{{display:block;font-size:24px}} .metric span,.grid span{{display:block;color:#858890;font-size:11px;margin-top:5px}}
  .callout{{background:#191b1f;border:1px solid #292c31;border-radius:13px;padding:14px;color:#c9cbc6;font-size:13px}}
  .grid{{display:grid;grid-template-columns:repeat(3,1fr);gap:9px}} .grid div{{padding:14px;background:#191b1f;border:1px solid #292c31;border-radius:13px}} .grid b{{font-size:18px}}
  .status{{color:{accent}!important;font-weight:700}} @media(max-width:560px){{.wrap{{padding:10px!important}}.grid{{display:block}}.grid div{{margin:8px 0}}.metric{{padding:10px!important}}.metric b{{font-size:18px!important}}}}
</style></body></html>"""


def send_email(report_html: str, delivery_key: str | None = None) -> str:
    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        raise RuntimeError("RESEND_API_KEY not set; report was not delivered")
    response = requests.post(
        "https://api.resend.com/emails",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
                 "Idempotency-Key": delivery_key or f"lander-daily-{datetime.now(timezone.utc):%Y-%m-%d}"},
        json={
            "from": os.getenv("RESEND_FROM", "Lander <onboarding@resend.dev>"),
            "to": [TO_EMAIL],
            "subject": f"Lander brief · {datetime.now():%b %d}",
            "html": report_html,
        }, timeout=15,
    )
    response.raise_for_status()
    delivery_id = response.json()["id"]
    print(f"Morning report accepted by provider: {delivery_id}")
    return delivery_id


def save_delivery_state(path, state):
    with tempfile.NamedTemporaryFile(mode='w', dir=path.parent, delete=False) as pending:
        json.dump(state, pending)
        pending.flush()
        os.fsync(pending.fileno())
    os.replace(pending.name, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()
    folder = Path(os.getenv('MORNING_REPORT_OUTPUT_DIR', '/opt/job-market-analytics/logs'))
    folder.mkdir(parents=True, exist_ok=True)
    with (folder/'.morning-report.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            report = build_report()
        except Exception as exc:
            # A broken metrics query must not silence the operational warning.
            report = ("<h1>Lander daily brief — Needs attention</h1>"
                      f"<p>Report metrics could not be loaded ({html.escape(type(exc).__name__)}). "
                      "Check the nightly pipeline and report task logs.</p>")
        (folder/'latest_report.html').write_text(report)
        if args.dry_run:
            print('Report rendered; dry run did not send')
            return
        day = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        ledger = folder/f'morning-report-{day}.json'
        state = json.loads(ledger.read_text()) if ledger.exists() else {'html': report}
        if state.get('delivery_id'):
            print(f'Daily report already accepted: {state["delivery_id"]}')
            return
        # Retry exactly the same body with the same provider idempotency key.
        save_delivery_state(ledger, state)
        state['delivery_id'] = send_email(state['html'], f'lander-daily-{day}')
        save_delivery_state(ledger, state)


if __name__ == "__main__":
    main()
