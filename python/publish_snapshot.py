#!/usr/bin/env python3
"""Validate and atomically replace Lander's public job snapshot."""
from __future__ import annotations

import argparse
import os

import psycopg2
from psycopg2.extras import RealDictCursor


CANDIDATE = """
  jp.status = 'raw'
  AND jp.data_tier = 1
  AND COALESCE(jp.source, '') <> 'adzuna'
  AND jp.company_id IS NOT NULL
  AND jp.role_id IS NOT NULL
  AND jp.domain IS NOT NULL
  AND jp.role_category IS NOT NULL
  AND jp.experience_level IS NOT NULL
  AND jp.embedding IS NOT NULL
  AND length(COALESCE(jp.description_text, '')) >= 100
  AND COALESCE(jp.loc_country, 'us') <> 'foreign'
"""


def publish(*, apply: bool, minimum: int, floor_ratio: float) -> dict:
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )
    try:
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('lander-publication'))")
            cur.execute("SELECT COUNT(*)::int AS count FROM job_postings WHERE is_public=true")
            prior = cur.fetchone()["count"]
            cur.execute(f"SELECT COUNT(*)::int AS count FROM job_postings jp WHERE {CANDIDATE}")
            candidate = cur.fetchone()["count"]
            required = max(minimum, int(prior * floor_ratio)) if prior else minimum
            if candidate < required:
                raise RuntimeError(
                    f"publication refused: {candidate:,} candidates; required at least {required:,} "
                    f"(prior snapshot {prior:,})"
                )
            result = {"prior": prior, "candidate": candidate, "required": required, "applied": apply}
            if not apply:
                return result

            cur.execute(f"""
                UPDATE job_postings jp
                SET is_public=false
                WHERE jp.is_public=true AND NOT ({CANDIDATE})
            """)
            deactivated = cur.rowcount
            cur.execute(f"""
                UPDATE job_postings jp
                SET is_public=true, published_at=now()
                WHERE ({CANDIDATE}) AND jp.is_public=false
            """)
            activated = cur.rowcount
            cur.execute(
                """INSERT INTO publication_runs(prior_count,candidate_count,activated_count,deactivated_count)
                   VALUES(%s,%s,%s,%s)""",
                (prior, candidate, activated, deactivated),
            )
            result.update(activated=activated, deactivated=deactivated)
            return result
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--minimum", type=int, default=1000)
    parser.add_argument("--floor-ratio", type=float, default=0.70)
    args = parser.parse_args()
    print(publish(apply=args.apply, minimum=args.minimum, floor_ratio=args.floor_ratio))


if __name__ == "__main__":
    main()
