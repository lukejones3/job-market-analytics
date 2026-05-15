#!/usr/bin/env python3
"""
enforce_blocklist.py

Belt-and-suspenders: marks any raw/active job_postings rows for blocked companies
as status='ignored'. Runs at 06:20 UTC, after ingest (06:00) and before enrich (06:30).

This catches anything that slipped through the ingest-time is_company_blocked() check
(e.g., via the cross-source dedup reactivation path).
"""
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
import psycopg2

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

_LOG_PATH = Path(__file__).resolve().parents[1] / "logs" / "blocklist_enforce.log"
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.FileHandler(_LOG_PATH),
        logging.StreamHandler(sys.stdout),
    ],
)
log = logging.getLogger(__name__)


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def main():
    from company_blocklist import ABSOLUTE_BLOCK

    blocked_lower = [name.lower() for name in ABSOLUTE_BLOCK]

    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE job_postings jp
                SET status = 'ignored'
                FROM companies c
                WHERE c.company_id = jp.company_id
                  AND LOWER(c.company_name) = ANY(%s)
                  AND jp.status NOT IN ('ignored', 'expired')
                RETURNING jp.job_id, c.company_name
                """,
                (blocked_lower,),
            )
            rows = cur.fetchall()
            if rows:
                by_company: dict = {}
                for job_id, company_name in rows:
                    by_company.setdefault(company_name, 0)
                    by_company[company_name] += 1
                for company, count in sorted(by_company.items()):
                    log.warning(
                        f"Blocked company leaked: {company!r} — "
                        f"set {count} row(s) to ignored"
                    )
                log.warning(f"Total rows enforced: {len(rows)}")
            else:
                log.info("No blocked-company rows found — blocklist clean.")

        conn.commit()
        log.info("enforce_blocklist complete")
    except Exception:
        conn.rollback()
        log.exception("enforce_blocklist failed")
        raise
    finally:
        conn.close()


if __name__ == "__main__":
    main()
