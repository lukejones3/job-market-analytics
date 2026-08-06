#!/usr/bin/env python3
"""Fast deterministic salary extraction for the active Lander inventory.

Salary parsing used to be coupled to the general enrichment queue.  That queue
is deliberately bounded, so a posting missing any other dimension could delay
salary extraction for days.  This command gives compensation its own cheap,
idempotent pass and annualizes hourly/monthly figures for the public UI.
"""
from __future__ import annotations

import argparse
import collections
import re
import time
from decimal import Decimal

from psycopg2.extras import execute_batch

from enrich_job_postings import get_conn, parse_salary_range


ANNUAL_MULTIPLIER = {
    "year": Decimal("1"),
    "month": Decimal("12"),
    "hour": Decimal("2080"),
}
SALARY_LANGUAGE = re.compile(
    r"salary|compensation|pay\s+(?:range|rate|band)|base\s+pay|hourly\s+rate|wage|hiring\s+range",
    re.IGNORECASE,
)


def salary_windows(description: str) -> str:
    """Keep bounded context around money amounts instead of reparsing huge ads."""
    windows: list[str] = []
    for match in re.finditer(r"\$", description or ""):
        window = description[max(0, match.start() - 350):match.start() + 700]
        if SALARY_LANGUAGE.search(window):
            windows.append(window)
        if len(windows) >= 20:
            break
    return "\n".join(windows)


def annualize(value: Decimal, period: str) -> Decimal | None:
    multiplier = ANNUAL_MULTIPLIER.get(period)
    if multiplier is None:
        return None
    result = value * multiplier
    return result if Decimal("15000") <= result <= Decimal("1000000") else None


def extract(*, apply: bool, limit: int | None = None, since_hours: int | None = None,
            repair_periods: bool = False) -> dict:
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor()
    params: list[object] = []
    limit_sql = ""
    since_sql = ""
    if since_hours is not None:
        since_sql = "AND ingested_at >= now() - (%s * interval '1 hour')"
        params.append(since_hours)
    if limit:
        limit_sql = "LIMIT %s"
        params.append(limit)

    salary_state_sql = (
        "AND salary_period IN ('hour', 'month')"
        if repair_periods else
        "AND salary_min_annual IS NULL AND salary_max_annual IS NULL"
    )
    cur.execute(
        f"""
        SELECT job_id, ingestion_source, description_text,
               salary_min, salary_max, salary_period,
               salary_min_annual, salary_max_annual
        FROM job_postings
        WHERE status = 'raw'
          AND COALESCE(data_tier, 1) = 1
          AND description_text IS NOT NULL
          {salary_state_sql}
          AND description_text LIKE '%%$%%'
          AND description_text ~* '(salary|compensation|pay[[:space:]]+(range|rate|band)|base[[:space:]]+pay|hourly[[:space:]]+rate|wage|hiring[[:space:]]+range)'
          {since_sql}
        ORDER BY ingested_at DESC
        {limit_sql}
        """,
        params,
    )
    rows = cur.fetchall()
    updates: list[tuple] = []
    by_source: collections.Counter[str] = collections.Counter()
    by_period: collections.Counter[str] = collections.Counter()

    for (job_id, source, description, raw_min, raw_max, raw_period,
         annual_min, annual_max) in rows:
        relevant_text = salary_windows(description)
        if not relevant_text:
            continue
        lo, hi, period = parse_salary_range(relevant_text, skip_llm=True)
        # Structured ATS compensation wins on the normal path. Repair mode
        # deliberately reinterprets old heuristic hourly/monthly classifications.
        if not repair_periods:
            lo = raw_min if raw_min is not None else lo
            hi = raw_max if raw_max is not None else hi
            period = raw_period or period
        if lo is None or hi is None or period not in ANNUAL_MULTIPLIER:
            continue
        ann_lo, ann_hi = annualize(Decimal(lo), period), annualize(Decimal(hi), period)
        if ann_lo is None or ann_hi is None:
            continue
        updates.append((lo, hi, period, ann_lo, ann_hi, job_id))
        by_source[source or "unknown"] += 1
        by_period[period] += 1

    if apply and updates:
        update_sql = (
            """
            UPDATE job_postings
            SET salary_min = %s, salary_max = %s, salary_period = %s,
                salary_min_annual = %s, salary_max_annual = %s
            WHERE job_id = %s
            """ if repair_periods else """
            UPDATE job_postings
            SET salary_min = COALESCE(salary_min, %s),
                salary_max = COALESCE(salary_max, %s),
                salary_period = COALESCE(salary_period, %s),
                salary_min_annual = COALESCE(salary_min_annual, %s),
                salary_max_annual = COALESCE(salary_max_annual, %s)
            WHERE job_id = %s
            """
        )
        execute_batch(
            cur, update_sql,
            updates,
            page_size=500,
        )
        conn.commit()
    else:
        conn.rollback()
    conn.close()
    return {
        "candidates": len(rows),
        "updated": len(updates),
        "by_source": dict(by_source.most_common()),
        "by_period": dict(by_period.most_common()),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--since-hours", type=int)
    parser.add_argument("--repair-periods", action="store_true",
                        help="Reparse and overwrite heuristic hourly/monthly rows")
    args = parser.parse_args()
    started = time.monotonic()
    result = extract(apply=args.apply, limit=args.limit, since_hours=args.since_hours,
                     repair_periods=args.repair_periods)
    mode = "updated" if args.apply else "would update"
    print(
        f"Salary extraction: scanned {result['candidates']:,}; {mode} "
        f"{result['updated']:,} in {time.monotonic() - started:.1f}s"
    )
    print(f"By source: {result['by_source']}")
    print(f"By period: {result['by_period']}")


if __name__ == "__main__":
    main()
