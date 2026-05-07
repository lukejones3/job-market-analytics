#!/usr/bin/env python3
"""
migrate_workday_candidates.py

Creates the workday_tenants_candidates table used by the Workday tenant
discovery pipeline.

Run once on the droplet before running discover/validate scripts:
    python python/migrate_workday_candidates.py

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
CREATE TABLE IF NOT EXISTS workday_tenants_candidates (
    tenant               TEXT PRIMARY KEY,
    server               TEXT,
    board                TEXT,
    company_name         TEXT,
    us_jobs_count        INTEGER DEFAULT 0,
    data_ml_jobs_count   INTEGER DEFAULT 0,
    discovery_source     TEXT NOT NULL,
    discovered_at        TIMESTAMPTZ DEFAULT now() NOT NULL,
    last_validated_at    TIMESTAMPTZ,
    status               TEXT DEFAULT 'pending' NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_wtc_status
    ON workday_tenants_candidates(status);

CREATE INDEX IF NOT EXISTS idx_wtc_data_ml_jobs
    ON workday_tenants_candidates(data_ml_jobs_count DESC);

CREATE INDEX IF NOT EXISTS idx_wtc_discovery_source
    ON workday_tenants_candidates(discovery_source);
"""

COMMENT = """
COMMENT ON TABLE workday_tenants_candidates IS
    'Staging table for Workday tenant discovery. Populated by discover_workday_tenants.py, '
    'validated by validate_workday_tenants.py. Tenants with status=active and '
    'data_ml_jobs_count > 0 get written to discovered_companies after manual review.';

COMMENT ON COLUMN workday_tenants_candidates.status IS
    'pending=not validated | active=has US+data/ML jobs | no_data_jobs=working but no target roles | unreachable=probe failed | integrated=in discovered_companies';
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
    log.info("Running migration: workday_tenants_candidates ...")
    cur.execute(DDL)
    try:
        cur.execute(COMMENT)
    except Exception:
        pass  # COMMENT is non-critical
    conn.commit()
    cur.close()
    conn.close()
    log.info("Migration complete.")
    log.info("")
    log.info("Next steps:")
    log.info("  1. python python/discover_workday_tenants.py --apply")
    log.info("  2. python python/validate_workday_tenants.py --apply")
    log.info("  3. python python/validate_workday_tenants.py --report")
    log.info("  4. Review list, then: python python/validate_workday_tenants.py --integrate")


if __name__ == "__main__":
    main()
