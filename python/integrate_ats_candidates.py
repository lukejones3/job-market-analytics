#!/usr/bin/env python3
"""
integrate_ats_candidates.py

Writes validated tenants from ats_tenants_candidates (status='active')
into discovered_companies so the nightly harvest picks them up.
All active tenants are integrated regardless of data/ML job count.

Marks integrated rows with status='integrated'.

Usage:
    python python/integrate_ats_candidates.py --dry-run
    python python/integrate_ats_candidates.py --apply
    python python/integrate_ats_candidates.py --apply --ats greenhouse
"""

import argparse
import hashlib
import logging
import os
from pathlib import Path
from typing import Optional

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


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def _board_token(ats: str, tenant: str, server: Optional[str]) -> Optional[str]:
    """
    Build the board_token value used in discovered_companies.
      Workday:    tenant/server/External  (server is needed for harvest)
      Greenhouse: tenant
      Lever:      tenant
      Ashby:      tenant
      Workable:   tenant
      iCIMS:      tenant
      Taleo:      tenant
      Eightfold:  tenant/domain (validation stores the required employer domain)
      others:     tenant

    Returns None for Workday rows where server is unknown — caller must skip
    these rather than insert a bare slug that the harvester cannot parse.
    """
    if ats == "workday":
        if not server:
            return None   # no server → cannot build a valid harvester URL
        server_name, _, board = server.partition("/")
        return f"{tenant}/{server_name}/{board or 'External'}"
    if ats == "eightfold":
        return f"{tenant}/{server}" if server else None
    return tenant


def integrate_active(
    apply: bool,
    ats_filter: Optional[str] = None,
) -> None:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    where_parts = ["status = 'active'"]
    if ats_filter:
        where_parts.append(f"ats = '{ats_filter}'")

    cur.execute(f"""
        SELECT ats, tenant, server, company_name, data_ml_jobs_count, us_jobs_count
        FROM ats_tenants_candidates
        WHERE {" AND ".join(where_parts)}
        ORDER BY ats, us_jobs_count DESC
    """)
    candidates = cur.fetchall()

    if not candidates:
        log.info("No active candidates to integrate.")
        cur.close()
        conn.close()
        return

    log.info(f"{'[DRY RUN] ' if not apply else ''}Integrating {len(candidates)} candidates into discovered_companies...")
    log.info(f"  {'ATS':<15} {'Tenant':<30} {'US':>5} {'D/ML':>5}  Company")
    log.info("  " + "-" * 75)

    integrated = 0
    skipped_existing = 0

    for r in candidates:
        ats      = r["ats"]
        tenant   = r["tenant"]
        server   = r["server"]
        name     = r["company_name"] or tenant.replace("-", " ").title()
        dml      = r["data_ml_jobs_count"]
        us_jobs  = r["us_jobs_count"]

        board_token = _board_token(ats, tenant, server)
        if board_token is None:
            log.warning(f"  SKIP {ats}/{tenant}: no server resolved — cannot build valid board_token")
            continue

        company_id  = "AT" + hashlib.md5(f"{ats}|{board_token}".encode()).hexdigest()[:10]

        log.info(f"  {ats:<15} {tenant:<30} {us_jobs:>5} {dml:>5}  {name}")

        if not apply:
            continue

        try:
            cur.execute(
                """
                INSERT INTO discovered_companies
                    (company_id, company_name, ats_source, board_token,
                     discovery_source, active_roles, total_seen, enabled)
                VALUES (%s, %s, %s, %s, 'ats_aggressive', %s, %s, true)
                ON CONFLICT (ats_source, board_token) DO UPDATE SET
                    company_name = COALESCE(NULLIF(EXCLUDED.company_name, ''), discovered_companies.company_name),
                    active_roles = GREATEST(discovered_companies.active_roles, EXCLUDED.active_roles),
                    total_seen = GREATEST(discovered_companies.total_seen, EXCLUDED.total_seen),
                    enabled = true,
                    last_seen_at = now()
                """,
                (company_id, name, ats, board_token, us_jobs, us_jobs),
            )
            cur.execute(
                "UPDATE ats_tenants_candidates SET status='integrated' WHERE ats=%s AND tenant=%s",
                (ats, tenant),
            )
            integrated += 1

            conn.commit()
        except Exception as e:
            log.warning(f"  Failed to integrate {ats}/{tenant}: {e}")
            conn.rollback()
            continue

    cur.close()
    conn.close()

    log.info("")
    log.info(f"Integration complete:")
    if apply:
        log.info(f"  New to discovered_companies: {integrated}")
        log.info(f"  Already existed (skipped):   {skipped_existing}")
        log.info("The nightly harvest will pick them up automatically.")
    else:
        log.info(f"  Would integrate: {len(candidates)} candidates (dry run)")
        log.info("  Re-run with --apply to write to DB.")


def main():
    ap = argparse.ArgumentParser(
        description="Integrate validated ATS candidates into discovered_companies."
    )
    ap.add_argument("--apply",   action="store_true", help="Write to discovered_companies")
    ap.add_argument("--dry-run", action="store_true", help="Show what would be integrated")
    ap.add_argument("--ats",     type=str, default=None,
                    help="Only integrate this ATS (e.g. greenhouse, lever)")
    args = ap.parse_args()

    apply = args.apply and not args.dry_run
    if not apply:
        log.info("DRY RUN — use --apply to write to DB")

    integrate_active(apply=apply, ats_filter=args.ats)


if __name__ == "__main__":
    main()
