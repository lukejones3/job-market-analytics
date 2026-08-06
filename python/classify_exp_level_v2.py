#!/usr/bin/env python3
"""Apply the evidence-first v3 experience classifier.

The historical filename remains as the Airflow entry point. No embeddings or ML
artifact are used. Pass --all for an intentional corpus-wide migration.
"""

import argparse
import logging
import os
import time
from collections import Counter
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_batch
from dotenv import load_dotenv

from experience_level_v3 import VERSION, classify_experience

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)
BATCH_SIZE = 2000


def get_conn():
    return psycopg2.connect(
        host=os.environ["PGHOST"], port=os.environ.get("PGPORT", "5432"),
        dbname=os.environ["PGDATABASE"], user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )


def ensure_schema(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("""
            ALTER TABLE job_postings
              ADD COLUMN IF NOT EXISTS experience_level_v3 text,
              ADD COLUMN IF NOT EXISTS experience_level_confidence double precision,
              ADD COLUMN IF NOT EXISTS experience_level_evidence jsonb,
              ADD COLUMN IF NOT EXISTS experience_classifier_version text,
              ADD COLUMN IF NOT EXISTS experience_classified_at timestamptz,
              ADD COLUMN IF NOT EXISTS management_level text
        """)
    conn.commit()


def _classify_rows(rows):
    updates = []
    distribution = Counter()
    for job_id, title, description in rows:
        decision = classify_experience(title, description)
        distribution[decision.level] += 1
        updates.append((
            decision.level, decision.level, decision.confidence,
            Json(decision.evidence()), VERSION, decision.management_level, job_id,
        ))
    return updates, distribution


def _apply_updates(conn, updates) -> None:
    # Stable lock order prevents two classifier workers from deadlocking each
    # other. Ingestion may still touch a row concurrently, so retry PostgreSQL's
    # serialization/deadlock failures without losing the completed batches.
    updates = sorted(updates, key=lambda row: row[-1])
    for attempt in range(1, 6):
        try:
            with conn.cursor() as cur:
                execute_batch(cur, """
                    UPDATE job_postings SET
                      experience_level_v3=%s,
                      experience_level=%s,
                      experience_level_confidence=%s,
                      experience_level_evidence=%s,
                      experience_classifier_version=%s,
                      experience_classified_at=now(),
                      management_level=%s
                    WHERE job_id=%s
                """, updates, page_size=500)
            conn.commit()
            return
        except (psycopg2.errors.DeadlockDetected, psycopg2.errors.SerializationFailure):
            conn.rollback()
            if attempt == 5:
                raise
            delay = attempt * 2
            log.warning("Concurrent row update; retrying batch in %ss (attempt %s/5)", delay, attempt)
            time.sleep(delay)


def classify_jobs(conn, apply: bool, all_rows: bool, limit: int | None) -> None:
    ensure_schema(conn)
    predicate = (
        "jp.experience_classifier_version IS DISTINCT FROM %s"
        if all_rows else
        "(jp.experience_level_v3 IS NULL "
        "OR jp.experience_level IS DISTINCT FROM jp.experience_level_v3 "
        "OR jp.experience_classifier_version IS DISTINCT FROM %s)"
    )
    predicate_params = (VERSION,)
    base_query = f"""
            SELECT jp.job_id, r.role_name, COALESCE(jp.description_text, '')
            FROM job_postings jp
            JOIN roles r USING(role_id)
            WHERE jp.status='raw' AND jp.data_tier=1 AND jp.domain IS NOT NULL
              AND ({predicate})
            ORDER BY jp.ingested_at DESC
    """

    if not apply:
        # A server-side cursor prevents 50k full descriptions from occupying
        # most of the production droplet's RAM during audits.
        total = 0
        distribution = Counter()
        with conn.cursor(name="experience_v3_shadow") as cur:
            cur.itersize = BATCH_SIZE
            query = base_query + (" LIMIT %s" if limit else "")
            params = predicate_params + ((limit,) if limit else ())
            cur.execute(query, params)
            while True:
                rows = cur.fetchmany(BATCH_SIZE)
                if not rows:
                    break
                _, batch_dist = _classify_rows(rows)
                distribution.update(batch_dist)
                total += len(rows)
                if total % 10000 == 0:
                    log.info("Shadow-classified %s rows", total)
        log.info("v3 shadow distribution for %s rows: %s", total, dict(distribution))
        return

    total = 0
    distribution = Counter()
    remaining_limit = limit
    while remaining_limit is None or remaining_limit > 0:
        batch_limit = min(BATCH_SIZE, remaining_limit) if remaining_limit else BATCH_SIZE
        with conn.cursor() as cur:
            cur.execute(base_query + " LIMIT %s", predicate_params + (batch_limit,))
            rows = cur.fetchall()
        if not rows:
            break
        updates, batch_dist = _classify_rows(rows)
        _apply_updates(conn, updates)
        distribution.update(batch_dist)
        total += len(rows)
        if remaining_limit is not None:
            remaining_limit -= len(rows)
        log.info("Applied %s to %s jobs", VERSION, total)
    log.info("v3 applied distribution for %s rows: %s", total, dict(distribution))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--all", action="store_true", help="Reclassify every eligible raw tier-1 job")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    conn = get_conn()
    try:
        classify_jobs(conn, args.apply, args.all, args.limit)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
