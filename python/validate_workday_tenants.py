#!/usr/bin/env python3
"""
validate_workday_tenants.py

Validates candidates in workday_tenants_candidates by probing the Workday CXS API.
For each pending tenant:
  1. Resolves the wd{N} server (if unknown) via redirect sniffing
  2. Finds the working job board name by trying common patterns
  3. Samples listings across the board and counts every admitted Lander domain
  4. Updates the candidate's status + counts in the DB

After validation, run with --report to see which tenants are ready for integration.
After manual review, run with --integrate to write approved tenants to discovered_companies.

Safe to re-run — only processes 'pending' rows unless --revalidate is set.

Usage:
    python python/validate_workday_tenants.py --dry-run        # show what would happen
    python python/validate_workday_tenants.py --apply          # validate and update DB
    python python/validate_workday_tenants.py --apply --limit 50
    python python/validate_workday_tenants.py --report         # show validated results
    python python/validate_workday_tenants.py --integrate      # write active tenants to discovered_companies
    python python/validate_workday_tenants.py --revalidate --apply  # re-run all, not just pending
"""

import argparse
import hashlib
import json
import logging
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import DictCursor
import requests
from dotenv import load_dotenv

from role_scope import evaluate_role

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================

REQUEST_TIMEOUT = 10
REQUEST_DELAY   = 0.8   # seconds between API calls to the same tenant
BATCH_DELAY     = 0.3   # seconds between tenants

# Common board names to try during validation, in order of frequency
COMMON_BOARDS = [
    "External",
    "Careers",
    "External_Career_Site",
    "JobSearch",
    "Global",
    "Search",
    "US",
    "CareerSite",
]

# WD servers to try if server is unknown
WD_SERVERS = [
    "wd1", "wd2", "wd3", "wd5", "wd10", "wd12", "wd102", "wd103",
    "wd104", "wd105", "wd106", "wd107", "wd108", "wd501", "wd502",
    "wd503", "wd1480",
]

# US location signals (must match at least one)
US_SIGNALS = {
    "remote", "united states", "usa", ", al", ", ak", ", az", ", ar",
    ", ca", ", co", ", ct", ", de", ", fl", ", ga", ", hi", ", id",
    ", il", ", in", ", ia", ", ks", ", ky", ", la", ", me", ", md",
    ", ma", ", mi", ", mn", ", ms", ", mo", ", mt", ", ne", ", nv",
    ", nh", ", nj", ", nm", ", ny", ", nc", ", nd", ", oh", ", ok",
    ", or", ", pa", ", ri", ", sc", ", sd", ", tn", ", tx", ", ut",
    ", vt", ", va", ", wa", ", wv", ", wi", ", wy", ", dc",
}
# Non-US location signals (skip if any match)
NON_US_SIGNALS = {
    "singapore", "sgp", "india", "ind", "bangalore", "hyderabad",
    "warsaw", "poland", "london", "united kingdom", "uk", "germany",
    "france", "canada", "toronto", "amsterdam", "dublin", "australia",
    "sydney", "tokyo", "japan", "china", "chn", "brazil", "mexico",
    "netherlands", "sweden", "switzerland", "spain", "italy", "korea",
}

def _is_us_job(location: str) -> bool:
    if not location:
        return True  # assume US if no location
    loc = location.lower()
    for sig in NON_US_SIGNALS:
        if sig in loc:
            return False
    # If it has a 3-letter country code that isn't USA/CAN, skip
    if "," in loc:
        last = loc.split(",")[-1].strip().upper()
        if len(last) == 3 and last not in {"USA", "CAN"} and last.isalpha():
            return False
    for sig in US_SIGNALS:
        if sig in loc:
            return True
    # If no location signals at all and it's short, assume US
    if len(location) < 5:
        return True
    return False


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


def load_pending(revalidate: bool = False, limit: Optional[int] = None) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)
    if revalidate:
        where = "WHERE status NOT IN ('integrated')"
    else:
        where = "WHERE status = 'pending'"
    q = f"""
        SELECT tenant, server, board, company_name, discovery_source
        FROM workday_tenants_candidates
        {where}
        ORDER BY discovered_at ASC
        {"LIMIT %s" if limit else ""}
    """
    params = (limit,) if limit else ()
    cur.execute(q, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def update_candidate(
    cur,
    tenant: str,
    server: Optional[str],
    board: Optional[str],
    us_jobs: int,
    target_jobs: int,
    domain_counts: Dict[str, int],
    status: str,
) -> None:
    cur.execute(
        """
        UPDATE workday_tenants_candidates SET
            server             = COALESCE(%s, server),
            board              = COALESCE(%s, board),
            us_jobs_count      = %s,
            data_ml_jobs_count = %s,
            target_jobs_count  = %s,
            domain_counts      = %s::jsonb,
            status             = %s,
            last_validated_at  = now()
        WHERE tenant = %s
        """,
        (server, board, us_jobs, domain_counts.get("data_ml", 0), target_jobs,
         json.dumps(domain_counts, sort_keys=True), status, tenant),
    )


# ============================================================
# WORKDAY API PROBING
# ============================================================

_WD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _resolve_server(tenant: str, known_server: Optional[str]) -> Optional[str]:
    """
    Find the wd{N} server for a tenant via redirect sniffing.
    Returns the resolved server string (e.g. "wd5") or None.
    """
    if known_server:
        return known_server

    # Try vanity URL redirect
    url = f"https://{tenant}.myworkdayjobs.com/"
    try:
        r = requests.get(
            url,
            allow_redirects=True,
            timeout=REQUEST_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        m = re.search(
            r"https?://[^.]+\.(wd\d+)\.myworkdayjobs\.com",
            r.url,
            re.IGNORECASE,
        )
        if m:
            return m.group(1).lower()
    except Exception:
        pass

    # Fall back: try each known server directly
    for server in WD_SERVERS:
        url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs"
        try:
            r = requests.post(
                url,
                json={"limit": 1, "offset": 0, "searchText": ""},
                headers=_WD_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                return server
        except Exception:
            pass
        time.sleep(0.2)

    return None


def _try_board(tenant: str, server: str, board: str) -> Optional[int]:
    """
    Try a specific board on the Workday CXS API.
    Returns total job count on success, None on failure.
    """
    url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    try:
        r = requests.post(
            url,
            json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
            headers=_WD_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            total = data.get("total", 0)
            postings = data.get("jobPostings")
            if postings is not None:  # valid API response
                return total
    except Exception:
        pass
    return None


def _find_board(tenant: str, server: str, known_board: Optional[str]) -> Optional[str]:
    """
    Find a working board for (tenant, server).
    Tries the known board first, then common patterns.
    Returns the first working board name, or None.
    """
    boards_to_try = []

    if known_board:
        boards_to_try.append(known_board)

    # Common board patterns derived from the tenant slug
    t = tenant
    T = tenant.capitalize()
    boards_to_try += COMMON_BOARDS + [
        f"{t}_External",
        f"{T}_External",
        f"{t}Careers",
        f"{T}Careers",
        f"{t}_Careers",
        f"{T}_Careers",
        f"{t}Jobs",
        f"{T}Jobs",
    ]

    seen = set()
    for board in boards_to_try:
        if board in seen:
            continue
        seen.add(board)

        total = _try_board(tenant, server, board)
        if total is not None and total > 0:
            return board
        time.sleep(0.25)

    return None


def _count_jobs(tenant: str, server: str, board: str, total_jobs: int) -> Tuple[int, int, Dict[str, int]]:
    """
    Sample up to 500 listings across the complete board and count:
      - us_jobs: listings with US-compatible location
      - target_jobs: US listings admitted or awaiting description evidence
      - domains: target jobs by modern role-scope domain

    Returns (us_jobs_count, target_jobs_count, domain_counts).
    """
    url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    us_jobs = 0
    target_jobs = 0
    domains: Counter[str] = Counter()

    sample_size = min(max(total_jobs, 0), 500)
    if sample_size <= 100:
        offsets = range(0, sample_size, 20)
    else:
        # Cover the board rather than rejecting finance-heavy tenants because
        # their first 100 listings happen to contain another business unit.
        offsets = sorted({round(i * max(total_jobs - 20, 0) / 24 / 20) * 20 for i in range(25)})
    for offset in offsets:
        try:
            r = requests.post(
                url,
                json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
                headers=_WD_HEADERS,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code != 200:
                break
            postings = r.json().get("jobPostings", [])
            if not postings:
                break
            for p in postings:
                title = p.get("title", "")
                locs = p.get("locationsText", "") or p.get("locations", "")
                loc = locs[0] if isinstance(locs, list) and locs else (locs or "")
                if _is_us_job(loc):
                    us_jobs += 1
                    decision = evaluate_role(title)
                    if decision.candidate:
                        target_jobs += 1
                        domains[decision.domain or "evidence_pending"] += 1
        except Exception:
            break
        time.sleep(REQUEST_DELAY)

    return us_jobs, target_jobs, dict(domains)


# ============================================================
# REPORT & INTEGRATE
# ============================================================

def print_report() -> None:
    """Print a summary of validated tenants ready for integration."""
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("""
        SELECT tenant, server, board, company_name,
               us_jobs_count, target_jobs_count, domain_counts, discovery_source, status
        FROM workday_tenants_candidates
        ORDER BY target_jobs_count DESC, us_jobs_count DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    by_status: Dict[str, List] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)

    total = len(rows)
    active = by_status.get("active", [])
    log.info("")
    log.info("=" * 70)
    log.info("VALIDATION REPORT")
    log.info("=" * 70)
    log.info(f"Total candidates: {total}")
    for status, items in sorted(by_status.items()):
        log.info(f"  {status}: {len(items)}")

    if active:
        log.info("")
        log.info(f"ACTIVE TENANTS ({len(active)}) — sorted by admitted target-job count:")
        log.info(f"  {'Tenant':<25} {'Server':<6} {'Board':<35} {'US':>5} {'Target':>6} {'Domains'}")
        log.info(f"  {'-'*25} {'-'*6} {'-'*35} {'-'*5} {'-'*5} {'-'*30}")
        for r in active:
            log.info(
                f"  {r['tenant']:<25} {(r['server'] or '?'):<6} "
                f"{(r['board'] or '?'):<35} "
                f"{r['us_jobs_count']:>5} {r['target_jobs_count']:>6}  "
                f"{dict(r['domain_counts'] or {})}"
            )
        log.info("")
        log.info(f"To integrate: python python/validate_workday_tenants.py --integrate")

    no_target = by_status.get("no_target_jobs", []) + by_status.get("no_data_jobs", [])
    if no_target:
        log.info(f"\nSkipped ({len(no_target)} working boards with no in-scope roles)")


def integrate_active() -> None:
    """
    Write every reachable US tenant into discovered_companies so the nightly
    job-level role policy—not a stale tenant snapshot—decides admission.
    board_token format: "tenant/server/board"

    Marks integrated tenants with status='integrated'.
    """
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    cur.execute("""
        SELECT tenant, server, board, company_name, us_jobs_count, target_jobs_count, domain_counts
        FROM workday_tenants_candidates
        WHERE status IN ('active', 'no_target_jobs', 'no_data_jobs')
          AND us_jobs_count > 0
          AND board IS NOT NULL
          AND server IS NOT NULL
        ORDER BY target_jobs_count DESC
    """)
    candidates = cur.fetchall()

    if not candidates:
        log.info("No reachable US Workday candidates to integrate.")
        cur.close()
        conn.close()
        return

    log.info(f"Integrating {len(candidates)} tenants into discovered_companies...")
    integrated = 0

    for r in candidates:
        tenant = r["tenant"]
        server = r["server"]
        board  = r["board"]
        name   = r["company_name"] or tenant.title()
        target = r["target_jobs_count"]
        observed = max(target, r.get("us_jobs_count", 0) or 0)

        board_token = f"{tenant}/{server}/{board}"
        company_id  = "WD" + hashlib.md5(f"workday|{board_token}".encode()).hexdigest()[:10]

        try:
            cur.execute(
                """
                INSERT INTO discovered_companies
                    (company_id, company_name, ats_source, board_token,
                     discovery_source, active_roles, total_seen, enabled)
                VALUES (%s, %s, 'workday', %s, 'workday_probe', %s, %s, true)
                ON CONFLICT (ats_source, board_token) DO UPDATE SET
                    company_name = COALESCE(NULLIF(EXCLUDED.company_name, ''), discovered_companies.company_name),
                    active_roles = GREATEST(discovered_companies.active_roles, EXCLUDED.active_roles),
                    total_seen = GREATEST(discovered_companies.total_seen, EXCLUDED.total_seen),
                    enabled = true,
                    last_seen_at = now()
                """,
                (company_id, name, board_token, observed, observed),
            )
            cur.execute(
                "UPDATE workday_tenants_candidates SET status='integrated' WHERE tenant=%s",
                (tenant,),
            )
            integrated += 1
            log.info(f"  ✅ {name} ({tenant}.{server}/{board}) — {target} target jobs {dict(r['domain_counts'] or {})}")
        except Exception as e:
            log.warning(f"  Failed to integrate {tenant}: {e}")
            conn.rollback()
            continue

    conn.commit()
    cur.close()
    conn.close()
    log.info(f"Integration complete: {integrated} new tenants added to discovered_companies")
    log.info("The nightly workday harvest will pick them up automatically.")


# ============================================================
# MAIN VALIDATION LOOP
# ============================================================

def run_validation(apply: bool, limit: Optional[int], revalidate: bool) -> None:
    pending = load_pending(revalidate=revalidate, limit=limit)
    log.info(f"Loaded {len(pending)} candidates to validate")

    if not pending:
        log.info("Nothing to validate. Run discover_workday_tenants.py first.")
        return

    results = {"active": 0, "no_target_jobs": 0, "unreachable": 0, "errors": 0}

    conn = None
    cur  = None
    if apply:
        conn = get_conn()
        cur  = conn.cursor()

    for i, row in enumerate(pending):
        tenant = row["tenant"]
        log.info(f"[{i+1}/{len(pending)}] {tenant}")

        try:
            # Step 1: resolve server
            server = _resolve_server(tenant, row.get("server"))
            if not server:
                log.info(f"  ❌ Could not resolve server for {tenant}")
                results["unreachable"] += 1
                if apply:
                    update_candidate(cur, tenant, None, None, 0, 0, {}, "unreachable")
                    conn.commit()
                time.sleep(BATCH_DELAY)
                continue

            # Step 2: find working board
            board = _find_board(tenant, server, row.get("board"))
            if not board:
                log.info(f"  ❌ No working board found for {tenant}.{server}")
                results["unreachable"] += 1
                if apply:
                    update_candidate(cur, tenant, server, None, 0, 0, {}, "unreachable")
                    conn.commit()
                time.sleep(BATCH_DELAY)
                continue

            # Step 3: count US + admitted jobs across the full Lander domain set.
            total_jobs = _try_board(tenant, server, board) or 0
            us_jobs, target_jobs, domain_counts = _count_jobs(tenant, server, board, total_jobs)

            if target_jobs > 0:
                status = "active"
                results["active"] += 1
                log.info(
                    f"  ✅ {tenant}.{server}/{board} — "
                    f"{us_jobs} US jobs, {target_jobs} target {domain_counts}"
                )
            elif us_jobs > 0:
                status = "no_target_jobs"
                results["no_target_jobs"] += 1
                log.info(f"  ⚪ {tenant}.{server}/{board} — {us_jobs} sampled US jobs, 0 target")
            else:
                status = "unreachable"
                results["unreachable"] += 1
                log.info(f"  ❌ {tenant}.{server}/{board} — 0 US jobs")

            if apply:
                update_candidate(cur, tenant, server, board, us_jobs, target_jobs, domain_counts, status)
                conn.commit()

        except Exception as e:
            log.warning(f"  Error validating {tenant}: {e}")
            results["errors"] += 1
            if apply and conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

        time.sleep(BATCH_DELAY)

    if apply and conn:
        cur.close()
        conn.close()

    log.info("")
    log.info("=" * 60)
    log.info("VALIDATION COMPLETE")
    log.info("=" * 60)
    for k, v in results.items():
        log.info(f"  {k}: {v}")
    if apply:
        log.info("")
        log.info("Next steps:")
        log.info("  1. Review results:  python python/validate_workday_tenants.py --report")
        log.info("  2. Approve + integrate: python python/validate_workday_tenants.py --integrate")


def main():
    ap = argparse.ArgumentParser(
        description="Validate Workday tenant candidates against the live API."
    )
    ap.add_argument("--apply",      action="store_true", help="Write results to DB")
    ap.add_argument("--dry-run",    action="store_true", help="Print plan, no DB writes")
    ap.add_argument("--report",     action="store_true", help="Print validation report and exit")
    ap.add_argument("--integrate",  action="store_true", help="Write active tenants to discovered_companies")
    ap.add_argument("--revalidate", action="store_true", help="Re-run all rows, not just pending")
    ap.add_argument("--limit",      type=int, default=None, metavar="N", help="Max tenants to process")
    args = ap.parse_args()

    if args.report:
        print_report()
        return

    if args.integrate:
        integrate_active()
        return

    apply = args.apply and not args.dry_run
    if not apply:
        log.info("DRY RUN — use --apply to write to DB")

    run_validation(apply=apply, limit=args.limit, revalidate=args.revalidate)


if __name__ == "__main__":
    main()
