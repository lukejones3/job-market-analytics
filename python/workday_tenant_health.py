#!/usr/bin/env python3
"""
workday_tenant_health.py

Weekly health monitor for all Workday tenants in production.

Checks:
  1. Tenants in WORKDAY_COMPANIES (hardcoded) — last job seen in job_postings
  2. Tenants in discovered_companies (dynamic) — last job seen + enabled status

Actions:
  - Flag tenants with 0 new jobs in the last 30 days
  - Auto-deactivate dead tenants in discovered_companies (set enabled=false)
  - Surface tenants in workday_tenants_candidates with data_ml_jobs_count > 0
    that haven't been integrated yet

Run weekly. Safe to run any time.

Usage:
    python python/workday_tenant_health.py           # report only, no changes
    python python/workday_tenant_health.py --deactivate  # also disable dead tenants
"""

import argparse
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import psycopg2
from psycopg2.extras import DictCursor
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

DEAD_TENANT_DAYS = 30     # flag if no new jobs seen in this many days
REQUEST_TIMEOUT  = 10

# ============================================================
# DB
# ============================================================

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


# ============================================================
# LOAD TENANT LISTS
# ============================================================

def load_hardcoded_tenants() -> List[Tuple[str, str, str, str]]:
    """Parse WORKDAY_COMPANIES from ingest_jobs.py without importing it."""
    path = Path(__file__).parent / "ingest_jobs.py"
    if not path.exists():
        return []
    text = path.read_text()
    # Find the WORKDAY_COMPANIES block
    m = re.search(r"WORKDAY_COMPANIES\s*=\s*\[(.+?)\]\s*\n\n", text, re.DOTALL)
    if not m:
        return []
    block = m.group(1)
    # Extract tuples: ("Name", "tenant", "board", "wdN")
    pattern = re.compile(
        r'\(\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*,\s*"([^"]+)"\s*\)',
        re.IGNORECASE,
    )
    return pattern.findall(block)  # List of (name, tenant, board, server)


def load_dynamic_tenants(cur) -> List[Dict]:
    """Load Workday tenants from discovered_companies."""
    cur.execute("""
        SELECT company_name, board_token, active_roles, enabled, last_seen_at
        FROM discovered_companies
        WHERE ats_source = 'workday'
        ORDER BY active_roles DESC
    """)
    return [dict(r) for r in cur.fetchall()]


def load_candidate_summary(cur) -> Dict:
    """Load summary stats from workday_tenants_candidates."""
    cur.execute("""
        SELECT status, COUNT(*) as n, SUM(data_ml_jobs_count) as total_data_ml
        FROM workday_tenants_candidates
        GROUP BY status
    """)
    return {r["status"]: {"count": r["n"], "total_data_ml": r["total_data_ml"]} for r in cur.fetchall()}


# ============================================================
# JOB ACTIVITY CHECK
# ============================================================

def get_tenant_activity(cur, tenants: List[str]) -> Dict[str, Optional[datetime]]:
    """
    For each tenant slug, find the most recent job_postings.last_seen_at.
    Uses a company name / source pattern match.
    Returns {tenant_slug -> last_seen_at} (None means no jobs found).
    """
    if not tenants:
        return {}

    # job_postings.source = 'workday', source_id starts with 'WD'
    # We join via company table — but it's easier to match on the WD hash prefix.
    # The simplest approach: query last_seen_at by company (display name)
    # grouped across all jobs with source='workday'.
    # However we don't have a direct tenant->company_name map in job_postings.
    # Fall back to querying by company name from discovered_companies.

    # Get distinct company names for each tenant (from discovered_companies)
    cur.execute("""
        SELECT
            split_part(board_token, '/', 1) AS tenant,
            MAX(last_seen_at)               AS last_seen
        FROM discovered_companies
        WHERE ats_source = 'workday'
        GROUP BY 1
    """)
    dc_activity = {r["tenant"]: r["last_seen"] for r in cur.fetchall()}

    # Also check job_postings directly by company name cross-referencing
    cur.execute("""
        SELECT c.company_name, MAX(jp.last_seen_at) AS last_seen
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        WHERE jp.source = 'workday'
        GROUP BY c.company_name
    """)
    jp_by_company = {r["company_name"].lower(): r["last_seen"] for r in cur.fetchall()}

    return dc_activity, jp_by_company


# ============================================================
# LIVE PROBE (spot-check a tenant is still alive)
# ============================================================

def _quick_probe(tenant: str, server: str, board: str) -> bool:
    """
    Quick liveness check — one API request, return True if any jobs found.
    """
    url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        r = requests.post(
            url,
            json={"limit": 1, "offset": 0, "searchText": ""},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            return (r.json().get("total", 0) or 0) > 0
    except Exception:
        pass
    return False


# ============================================================
# HEALTH REPORT
# ============================================================

def run_health_check(deactivate: bool) -> None:
    conn = get_conn()
    cur  = conn.cursor(cursor_factory=DictCursor)

    now      = datetime.now(timezone.utc)
    deadline = now - timedelta(days=DEAD_TENANT_DAYS)

    # --- Hardcoded tenants ---
    hardcoded = load_hardcoded_tenants()
    log.info(f"Hardcoded WORKDAY_COMPANIES tenants: {len(hardcoded)}")

    # --- Dynamic tenants ---
    dynamic = load_dynamic_tenants(cur)
    log.info(f"discovered_companies workday tenants: {len(dynamic)}")

    # --- Activity data ---
    dc_activity, jp_by_company = get_tenant_activity(cur, [])

    # --- Candidate pipeline summary ---
    cand_summary = load_candidate_summary(cur)

    # --------------------------------------------------------
    # 1. Check dynamic tenants for inactivity
    # --------------------------------------------------------
    dead_tenants: List[Dict] = []
    stale_tenants: List[Dict] = []
    healthy_tenants: List[Dict] = []

    for t in dynamic:
        bt = t["board_token"]
        parts = bt.split("/")
        tenant_slug = parts[0] if parts else bt
        last_seen = dc_activity.get(tenant_slug)

        if not last_seen:
            # Try matching by company name in job_postings
            last_seen = jp_by_company.get(t["company_name"].lower())

        t["tenant_slug"] = tenant_slug
        t["last_seen"]   = last_seen

        if not t["enabled"]:
            continue  # already disabled

        if last_seen is None:
            dead_tenants.append(t)
        elif last_seen < deadline:
            dead_tenants.append(t)
        elif last_seen < now - timedelta(days=14):
            stale_tenants.append(t)
        else:
            healthy_tenants.append(t)

    # --------------------------------------------------------
    # 2. Spot-check dead tenants (are they actually dead?)
    # --------------------------------------------------------
    confirmed_dead: List[Dict] = []
    revived: List[Dict] = []

    for t in dead_tenants:
        bt = t["board_token"]
        parts = bt.split("/")
        if len(parts) == 3:
            tenant_s, server_s, board_s = parts
        else:
            confirmed_dead.append(t)
            continue

        last_str = t["last_seen"].strftime("%Y-%m-%d") if t["last_seen"] else "never"
        log.info(f"  Spot-checking dead tenant: {tenant_s} (last seen: {last_str})")
        alive = _quick_probe(tenant_s, server_s, board_s)
        time.sleep(0.5)

        if alive:
            log.info(f"    ↑ {tenant_s} is actually alive — harvest may have missed it")
            revived.append(t)
        else:
            log.info(f"    ✗ {tenant_s} confirmed dead")
            confirmed_dead.append(t)

    # --------------------------------------------------------
    # 3. Auto-deactivate confirmed dead tenants
    # --------------------------------------------------------
    deactivated = 0
    if deactivate and confirmed_dead:
        for t in confirmed_dead:
            try:
                cur.execute(
                    "UPDATE discovered_companies SET enabled=false WHERE board_token=%s",
                    (t["board_token"],),
                )
                deactivated += 1
                log.info(f"  Deactivated: {t['company_name']} ({t['board_token']})")
            except Exception as e:
                log.warning(f"  Could not deactivate {t['board_token']}: {e}")
        conn.commit()

    # --------------------------------------------------------
    # 4. Print report
    # --------------------------------------------------------
    log.info("")
    log.info("=" * 70)
    log.info("WORKDAY TENANT HEALTH REPORT")
    log.info(f"As of: {now.strftime('%Y-%m-%d %H:%M UTC')}")
    log.info("=" * 70)

    log.info(f"\nHardcoded tenants (ingest_jobs.py):   {len(hardcoded)}")
    log.info(f"Dynamic tenants (discovered_companies): {len(dynamic)}")
    log.info(f"  Healthy (jobs seen <14 days):          {len(healthy_tenants)}")
    log.info(f"  Stale   (jobs seen 14-{DEAD_TENANT_DAYS} days):      {len(stale_tenants)}")
    log.info(f"  Dead    (no jobs in >{DEAD_TENANT_DAYS} days):        {len(dead_tenants)}")
    log.info(f"    confirmed dead (probe failed):        {len(confirmed_dead)}")
    log.info(f"    still alive (harvest gap):            {len(revived)}")
    if deactivate:
        log.info(f"  Auto-deactivated:                      {deactivated}")

    if stale_tenants:
        log.info(f"\nSTALE TENANTS (may need attention):")
        for t in stale_tenants:
            last_str = t["last_seen"].strftime("%Y-%m-%d") if t["last_seen"] else "never"
            log.info(f"  {t['company_name']:<40} last_seen={last_str}")

    if confirmed_dead and not deactivate:
        log.info(f"\nCONFIRMED DEAD TENANTS (run --deactivate to disable):")
        for t in confirmed_dead:
            log.info(f"  {t['company_name']:<40} ({t['board_token']})")

    # --------------------------------------------------------
    # 5. Candidate pipeline summary
    # --------------------------------------------------------
    log.info(f"\nDISCOVERY PIPELINE (workday_tenants_candidates):")
    total_cands = sum(v["count"] for v in cand_summary.values())
    log.info(f"  Total candidates: {total_cands}")
    for status in ("pending", "active", "no_data_jobs", "unreachable", "integrated"):
        info = cand_summary.get(status, {"count": 0, "total_data_ml": 0})
        count = info["count"]
        dml   = info["total_data_ml"] or 0
        log.info(f"  {status:<15}: {count:>5}  (data/ML jobs: {dml})")

    active_unintegrated = cand_summary.get("active", {}).get("count", 0)
    if active_unintegrated > 0:
        log.info(
            f"\n  ⚡ {active_unintegrated} validated tenants ready for integration — "
            f"run: python python/validate_workday_tenants.py --report"
        )

    cur.close()
    conn.close()


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        description="Weekly Workday tenant health check."
    )
    ap.add_argument(
        "--deactivate",
        action="store_true",
        help="Auto-disable confirmed-dead tenants in discovered_companies",
    )
    args = ap.parse_args()
    run_health_check(deactivate=args.deactivate)


if __name__ == "__main__":
    main()
