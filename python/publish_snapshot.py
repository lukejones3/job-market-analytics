#!/usr/bin/env python3
"""Validate and atomically replace Lander's public job snapshot."""
from __future__ import annotations

import argparse
import os
import re
import unicodedata

import psycopg2
from psycopg2.extras import RealDictCursor


CANDIDATE_VIEW = "public.vw_lander_publication_candidates"


def _slug(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii").lower().replace("&", " and ")
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:80]
    return value or "job"


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
            cur.execute(f"SELECT COUNT(*)::int AS count FROM {CANDIDATE_VIEW}")
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
                SELECT jp.job_id, r.role_name
                FROM job_postings jp
                JOIN roles r ON r.role_id=jp.role_id
                LEFT JOIN {CANDIDATE_VIEW} candidate ON candidate.job_id=jp.job_id
                WHERE jp.is_public=true AND candidate.job_id IS NULL
            """)
            removals = cur.fetchall()
            cur.execute(f"""
                SELECT jp.job_id, r.role_name
                FROM {CANDIDATE_VIEW} candidate
                JOIN job_postings jp ON jp.job_id=candidate.job_id
                JOIN roles r ON r.role_id=jp.role_id
                WHERE jp.is_public=false
            """)
            additions = cur.fetchall()
            for row, kind in [*((row, "URL_DELETED") for row in removals), *((row, "URL_UPDATED") for row in additions)]:
                url = f"https://www.landerjob.com/openings/{row['job_id']}/{_slug(row['role_name'])}"
                cur.execute(
                    "INSERT INTO public.seo_indexing_queue(job_id,url,notification_type) VALUES(%s,%s,%s)",
                    (row["job_id"], url, kind),
                )

            cur.execute(f"""
                UPDATE job_postings jp
                SET is_public=false
                WHERE jp.is_public=true
                  AND NOT EXISTS (SELECT 1 FROM {CANDIDATE_VIEW} candidate WHERE candidate.job_id=jp.job_id)
            """)
            deactivated = cur.rowcount
            cur.execute(f"""
                UPDATE job_postings jp
                SET is_public=true, published_at=now()
                WHERE jp.is_public=false
                  AND EXISTS (SELECT 1 FROM {CANDIDATE_VIEW} candidate WHERE candidate.job_id=jp.job_id)
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
