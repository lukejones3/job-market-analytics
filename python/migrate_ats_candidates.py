#!/usr/bin/env python3
"""
migrate_ats_candidates.py

Creates the ats_tenants_candidates table used by the aggressive ATS
discovery pipeline (discover_ats_aggressive.py).

Run once on the droplet before running discover/validate scripts:
    python python/migrate_ats_candidates.py

Safe to re-run (uses IF NOT EXISTS).
"""

import logging
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger(__name__)

DDL = """
CREATE TABLE IF NOT EXISTS ats_tenants_candidates (
    ats                  TEXT NOT NULL,
    tenant               TEXT NOT NULL,
    server               TEXT,
    source               TEXT NOT NULL,
    company_name         TEXT,
    us_jobs_count        INTEGER DEFAULT 0,
    data_ml_jobs_count   INTEGER DEFAULT 0,
    discovered_at        TIMESTAMPTZ DEFAULT now() NOT NULL,
    last_validated_at    TIMESTAMPTZ,
    status               TEXT DEFAULT 'pending' NOT NULL,
    PRIMARY KEY (ats, tenant)
);

CREATE INDEX IF NOT EXISTS idx_atc_status
    ON ats_tenants_candidates(status);

CREATE INDEX IF NOT EXISTS idx_atc_ats
    ON ats_tenants_candidates(ats);

CREATE INDEX IF NOT EXISTS idx_atc_data_ml_jobs
    ON ats_tenants_candidates(data_ml_jobs_count DESC);

CREATE INDEX IF NOT EXISTS idx_atc_source
    ON ats_tenants_candidates(source);

CREATE INDEX IF NOT EXISTS idx_atc_ats_status
    ON ats_tenants_candidates(ats, status);
"""

COMMENT = """
COMMENT ON TABLE ats_tenants_candidates IS
    'Staging table for multi-ATS tenant discovery. Populated by discover_ats_aggressive.py, '
    'validated by validate_ats_candidates.py. Tenants with status=active and '
    'data_ml_jobs_count > 0 get written to discovered_companies by integrate_ats_candidates.py.';

COMMENT ON COLUMN ats_tenants_candidates.ats IS
    'ATS platform: workday | greenhouse | lever | ashby | icims | workable | taleo | eightfold | jobvite | successfactors';

COMMENT ON COLUMN ats_tenants_candidates.tenant IS
    'Tenant slug. For Workday: vanity slug (e.g. acme). For Greenhouse: board slug (e.g. stripe). '
    'For Lever: company slug. For Ashby: company slug. For iCIMS: subdomain. For Taleo: subdomain.';

COMMENT ON COLUMN ats_tenants_candidates.server IS
    'Workday-only: server prefix (wd1, wd5, wd3, etc.). NULL for all other ATSs.';

COMMENT ON COLUMN ats_tenants_candidates.source IS
    'Discovery source: ct_logs | serper | company_probe | sitemap | linkedin | commoncrawl | edgar';

COMMENT ON COLUMN ats_tenants_candidates.status IS
    'pending=not validated | active=has US+data/ML jobs | no_data_jobs=working but no target roles '
    '| unreachable=probe failed | integrated=written to discovered_companies';
"""


def main():
    conn = psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )
    cur = conn.cursor()
    log.info("Running migration: ats_tenants_candidates ...")
    cur.execute(DDL)
    try:
        cur.execute(COMMENT)
    except Exception:
        pass
    conn.commit()
    cur.close()
    conn.close()
    log.info("Migration complete.")
    log.info("")
    log.info("Next steps:")
    log.info("  1. python python/discover_ats_aggressive.py --source ct_logs --apply")
    log.info("  2. python python/discover_ats_aggressive.py --source serper --apply")
    log.info("  3. python python/discover_ats_aggressive.py --source company_probe --apply")
    log.info("  4. python python/validate_ats_candidates.py --apply")
    log.info("  5. python python/validate_ats_candidates.py --report")
    log.info("  6. python python/integrate_ats_candidates.py --apply")


if __name__ == "__main__":
    main()
