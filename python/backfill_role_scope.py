#!/usr/bin/env python3
"""Apply the current role-admission policy to legacy job rows.

New ingestion records the decision inline. This backstop makes the same
boundary true for rows created before role_scope existed, so publication can
fail closed without discarding valid legacy opportunities blindly.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_batch, RealDictCursor

from role_scope import evaluate_role

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def connection():
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.environ["PGHOST"], port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )


def backfill(*, apply: bool, only_missing: bool, batch_size: int = 500) -> dict:
    conn = connection()
    counts: dict[str, int] = {}
    examined = 0
    after_job_id = ""
    try:
        while True:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                missing = "AND jp.scope_status IS NULL" if only_missing else ""
                # Only rows capable of entering the public snapshot need this
                # backstop. Loading every expired/Tier-2 description exceeded
                # the production droplet's memory without changing publication.
                cur.execute(f"""SELECT jp.job_id, r.role_name, jp.description_text
                    FROM job_postings jp JOIN roles r ON r.role_id=jp.role_id
                    WHERE jp.status='raw' AND jp.data_tier=1 {missing}
                      AND jp.job_id > %s
                    ORDER BY jp.job_id LIMIT %s""", (after_job_id, batch_size))
                rows = cur.fetchall()
                if not rows:
                    break
                after_job_id = rows[-1]["job_id"]
                decisions = []
                for row in rows:
                    decision = evaluate_role(row["role_name"], row["description_text"] or "")
                    counts[decision.status] = counts.get(decision.status, 0) + 1
                    decisions.append((decision.status, decision.rule_id,
                                      decision.confidence, row["job_id"]))
                examined += len(rows)
                if not apply:
                    # Preview exactly one bounded batch; a full dry run would
                    # repeatedly select the same unchanged rows.
                    break
                execute_batch(cur, """UPDATE job_postings SET scope_status=%s,
                    scope_rule_id=%s, scope_confidence=%s WHERE job_id=%s""",
                    decisions, page_size=batch_size)
                conn.commit()
        return {"examined": examined, "applied": apply, "outcomes": counts}
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only-missing", action="store_true")
    args = parser.parse_args()
    print(backfill(apply=args.apply, only_missing=args.only_missing))


if __name__ == "__main__":
    main()
