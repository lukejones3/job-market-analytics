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


def backfill(*, apply: bool, only_missing: bool) -> dict:
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        predicate = "jp.scope_status IS NULL" if only_missing else "true"
        cur.execute(f"""SELECT jp.job_id, jp.source, jp.source_id, jp.crawl_tenant,
                r.role_name, c.company_name, jp.description_text
            FROM job_postings jp
            JOIN roles r ON r.role_id=jp.role_id
            JOIN companies c ON c.company_id=jp.company_id
            WHERE {predicate}""")
        rows = cur.fetchall()
        decisions = []
        counts: dict[str, int] = {}
        for row in rows:
            decision = evaluate_role(row["role_name"], row["description_text"] or "")
            counts[decision.status] = counts.get(decision.status, 0) + 1
            decisions.append((decision.status, decision.rule_id, decision.confidence,
                              row["job_id"]))
        if apply and decisions:
            execute_batch(cur, """UPDATE job_postings SET scope_status=%s,
                scope_rule_id=%s, scope_confidence=%s WHERE job_id=%s""",
                decisions, page_size=1000)
        return {"examined": len(rows), "applied": apply, "outcomes": counts}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--only-missing", action="store_true")
    args = parser.parse_args()
    print(backfill(apply=args.apply, only_missing=args.only_missing))


if __name__ == "__main__":
    main()
