#!/usr/bin/env python3
"""
ats_discovery_health.py

Weekly health check for the ATS tenant discovery pipeline.

Runs:
  1. Stats report: candidates by ATS + status, integration rate
  2. Re-validates a sample of 'active' tenants to catch dead/changed boards
  3. Re-queues any 'unreachable' tenants older than 14 days for re-validation
  4. Alerts on anomalies (zero new candidates, high unreachable rate)

Usage:
    python python/ats_discovery_health.py --report           # stats only
    python python/ats_discovery_health.py --recheck-active   # re-validate 50 active tenants
    python python/ats_discovery_health.py --requeue-dead     # reset stale unreachables to pending
    python python/ats_discovery_health.py --full             # all of the above

Cron (weekly):
    0 6 * * 1 cd ~/github/job-market-analytics && python python/ats_discovery_health.py --full >> logs/ats_health.log 2>&1
"""

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Dict, List

import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

RECHECK_SAMPLE  = 50   # how many active tenants to spot-check per run
DEAD_THRESHOLD  = 14   # days before an unreachable is re-queued


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


# ============================================================
# REPORT
# ============================================================

def print_health_report() -> None:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    # Pipeline counts
    cur.execute("""
        SELECT ats, status, COUNT(*) AS cnt
        FROM ats_tenants_candidates
        GROUP BY ats, status
        ORDER BY ats, status
    """)
    rows = cur.fetchall()

    # discovered_companies breakdown
    cur.execute("""
        SELECT ats_source, COUNT(*) AS cnt, SUM(active_roles) AS total_roles
        FROM discovered_companies
        WHERE discovery_source = 'ats_aggressive'
        GROUP BY ats_source
        ORDER BY cnt DESC
    """)
    integrated_rows = cur.fetchall()

    # Total job postings contributed
    cur.execute("""
        SELECT dc.ats_source, COUNT(jp.job_id) AS job_count
        FROM job_postings jp
        JOIN discovered_companies dc
          ON jp.company_id = dc.company_id
        WHERE dc.discovery_source = 'ats_aggressive'
          AND jp.scraped_at >= now() - interval '7 days'
        GROUP BY dc.ats_source
        ORDER BY job_count DESC
    """)
    job_rows = cur.fetchall()

    cur.close()
    conn.close()

    log.info("")
    log.info("=" * 70)
    log.info("ATS DISCOVERY PIPELINE HEALTH REPORT")
    log.info("=" * 70)

    # Candidate table breakdown
    by_ats: Dict[str, Dict[str, int]] = {}
    for r in rows:
        by_ats.setdefault(r["ats"], {})[r["status"]] = r["cnt"]

    log.info("")
    log.info("ats_tenants_candidates:")
    log.info(f"  {'ATS':<20} {'pending':>8} {'active':>8} {'no_data':>8} {'dead':>8} {'integrated':>10}  total")
    log.info("  " + "-" * 72)
    grand_total = 0
    for ats in sorted(by_ats.keys()):
        s = by_ats[ats]
        total = sum(s.values())
        grand_total += total
        pct_active = (s.get("active", 0) / max(total, 1)) * 100
        log.info(
            f"  {ats:<20} {s.get('pending',0):>8} {s.get('active',0):>8} "
            f"{s.get('no_data_jobs',0):>8} {s.get('unreachable',0):>8} "
            f"{s.get('integrated',0):>10}  {total}  ({pct_active:.0f}% active)"
        )
    log.info(f"  {'TOTAL':<20} {'':>8} {'':>8} {'':>8} {'':>8} {'':>10}  {grand_total}")

    # Integration stats
    if integrated_rows:
        log.info("")
        log.info("Integrated into discovered_companies (via ats_aggressive):")
        for r in integrated_rows:
            log.info(f"  {r['ats_source']:<20} {r['cnt']:>6} companies, {int(r['total_roles'] or 0):>8} active roles")

    # Recent job yield
    if job_rows:
        log.info("")
        log.info("Jobs harvested last 7 days (from ats_aggressive companies):")
        for r in job_rows:
            log.info(f"  {r['ats_source']:<20} {r['job_count']:>8} jobs")

    # Anomaly check
    log.info("")
    total_pending = sum(s.get("pending", 0) for s in by_ats.values())
    total_active  = sum(s.get("active", 0)  for s in by_ats.values())
    total_dead    = sum(s.get("unreachable", 0) for s in by_ats.values())

    if total_pending > 5000:
        log.warning(f"⚠️  High pending count ({total_pending}) — run validate_ats_candidates.py --apply")
    if total_dead > total_active * 0.5 and total_active > 0:
        log.warning(f"⚠️  High unreachable rate ({total_dead} dead vs {total_active} active)")
    if grand_total == 0:
        log.warning("⚠️  No candidates found — run discover_ats_aggressive.py")

    log.info("")
    log.info("=" * 70)


# ============================================================
# RECHECK ACTIVE TENANTS
# ============================================================

def recheck_active(sample: int = RECHECK_SAMPLE) -> None:
    """
    Re-validate a random sample of 'active' tenants.
    Marks any that have gone dead as 'unreachable'.
    """
    from validate_ats_candidates import validate_candidate, update_candidate  # local import

    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute(f"""
        SELECT ats, tenant, server, company_name, source
        FROM ats_tenants_candidates
        WHERE status = 'active'
        ORDER BY RANDOM()
        LIMIT {sample}
    """)
    candidates = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()

    if not candidates:
        log.info("No active tenants to recheck.")
        return

    log.info(f"Rechecking {len(candidates)} active tenants (random sample)...")
    flipped = 0

    conn = get_conn()
    cur = conn.cursor()

    for row in candidates:
        ats = row["ats"]
        tenant = row["tenant"]
        try:
            server, us_jobs, data_ml_jobs, status = validate_candidate(row)
            if status != "active":
                flipped += 1
                log.info(f"  ⚠️  {ats}/{tenant} → now {status} (was active)")
                update_candidate(cur, ats, tenant, server, us_jobs, data_ml_jobs, status)
                conn.commit()
            else:
                log.debug(f"  ✅ {ats}/{tenant} still active ({us_jobs} US, {data_ml_jobs} d/ml)")
        except Exception as e:
            log.warning(f"  Error rechecking {ats}/{tenant}: {e}")
        time.sleep(0.3)

    cur.close()
    conn.close()
    log.info(f"Recheck complete: {flipped}/{len(candidates)} tenants changed status")


# ============================================================
# REQUEUE STALE UNREACHABLES
# ============================================================

def requeue_dead(threshold_days: int = DEAD_THRESHOLD) -> None:
    """
    Reset 'unreachable' candidates older than threshold_days back to 'pending'
    so they get re-validated on the next validation run.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        UPDATE ats_tenants_candidates
        SET status = 'pending', last_validated_at = NULL
        WHERE status = 'unreachable'
          AND (last_validated_at IS NULL
               OR last_validated_at < now() - interval '%s days')
        """,
        (threshold_days,),
    )
    requeued = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()

    log.info(f"Requeued {requeued} stale unreachable candidates (older than {threshold_days} days)")


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        description="Weekly health check for the ATS tenant discovery pipeline."
    )
    ap.add_argument("--report",         action="store_true", help="Print health report")
    ap.add_argument("--recheck-active", action="store_true", dest="recheck",
                    help=f"Re-validate {RECHECK_SAMPLE} random active tenants")
    ap.add_argument("--requeue-dead",   action="store_true", dest="requeue",
                    help=f"Reset unreachables older than {DEAD_THRESHOLD} days to pending")
    ap.add_argument("--full",           action="store_true", help="Run all: report + recheck + requeue")
    ap.add_argument("--sample",         type=int, default=RECHECK_SAMPLE,
                    help=f"Number of active tenants to spot-check (default: {RECHECK_SAMPLE})")
    args = ap.parse_args()

    if args.full or args.report:
        print_health_report()

    if args.full or args.recheck:
        log.info("")
        recheck_active(sample=args.sample)

    if args.full or args.requeue:
        log.info("")
        requeue_dead()

    if not any([args.report, args.recheck, args.requeue, args.full]):
        ap.print_help()


if __name__ == "__main__":
    main()
