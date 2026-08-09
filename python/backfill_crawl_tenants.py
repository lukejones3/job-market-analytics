#!/usr/bin/env python3
"""Repair deterministic crawl-tenant keys on historical Tier-1 rows."""
from __future__ import annotations

import argparse
import os
import re
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2.extras import execute_values
from dotenv import load_dotenv


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


PATTERNS: dict[str, tuple[re.Pattern[str], ...]] = {
    "greenhouse": (
        re.compile(r"(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", re.I),
        re.compile(r"api\.greenhouse\.io/v1/boards/([^/?#]+)", re.I),
    ),
    "lever": (re.compile(r"jobs\.lever\.co/([^/?#]+)", re.I),),
    "ashby": (re.compile(r"jobs\.ashbyhq\.com/([^/?#]+)", re.I),),
    "workday": (re.compile(r"https?://([^.]+)\.wd\d+\.myworkdayjobs\.com", re.I),),
    "smartrecruiters": (
        re.compile(r"jobs\.smartrecruiters\.com/([^/?#]+)", re.I),
        re.compile(r"api\.smartrecruiters\.com/v1/companies/([^/?#]+)", re.I),
    ),
    "workable": (re.compile(r"apply\.workable\.com/([^/?#]+)", re.I),),
    "icims": (re.compile(r"https?://([^.]+)\.icims\.com", re.I),),
    "taleo": (re.compile(r"https?://([^.]+)\.taleo\.net", re.I),),
    "jobvite": (re.compile(r"jobs\.jobvite\.com/([^/?#]+)", re.I),),
    "bamboohr": (re.compile(r"https?://([^.]+)\.bamboohr\.com", re.I),),
    "eightfold": (re.compile(r"https?://([^.]+)\.eightfold\.ai", re.I),),
}


def infer_crawl_tenant(source: str | None, source_id: str | None, job_url: str | None) -> Optional[str]:
    normalized_source = (source or "").lower()
    if normalized_source == "amazon":
        return "amazon"
    haystack = " ".join(filter(None, (job_url, source_id)))
    for pattern in PATTERNS.get(normalized_source, ()):
        match = pattern.search(haystack)
        if match:
            token = match.group(1).strip().lower()
            if token not in {"jobs", "job", "embed", "api", "www"}:
                return token
    if normalized_source in {"jobvite", "bamboohr"} and source_id and "|" in source_id:
        return source_id.split("|", 1)[0].lower()
    if normalized_source in {"icims", "taleo", "eightfold"} and source_id and "_" in source_id:
        return source_id.split("_", 1)[0].lower()
    return None


def connection():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def backfill(*, apply: bool, limit: int) -> dict[str, int]:
    with connection() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT job_id, COALESCE(ingestion_source, source), source_id, job_url
               FROM job_postings
               WHERE data_tier=1 AND crawl_tenant IS NULL
               ORDER BY last_seen_at DESC NULLS LAST
               LIMIT %s""",
            (limit,),
        )
        rows = cur.fetchall()
        repaired = []
        for job_id, source, source_id, job_url in rows:
            tenant = infer_crawl_tenant(source, source_id, job_url)
            if tenant:
                repaired.append((tenant, job_id))
        if apply and repaired:
            cur.execute(
                """CREATE TEMP TABLE crawl_tenant_backfill (
                       crawl_tenant text NOT NULL,
                       job_id text PRIMARY KEY
                   ) ON COMMIT DROP"""
            )
            execute_values(
                cur,
                "INSERT INTO crawl_tenant_backfill (crawl_tenant, job_id) VALUES %s",
                repaired,
                page_size=5000,
            )
            cur.execute(
                """UPDATE job_postings AS jobs
                   SET crawl_tenant = repair.crawl_tenant
                   FROM crawl_tenant_backfill AS repair
                   WHERE jobs.job_id = repair.job_id
                     AND jobs.crawl_tenant IS NULL"""
            )
        if not apply:
            conn.rollback()
        return {"examined": len(rows), "repaired": len(repaired), "unresolved": len(rows) - len(repaired)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100000)
    args = parser.parse_args()
    print(backfill(apply=args.apply, limit=max(1, args.limit)))


if __name__ == "__main__":
    main()
