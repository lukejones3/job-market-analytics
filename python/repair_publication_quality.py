#!/usr/bin/env python3
"""Repair authoritative employer and location evidence before publication."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path

import psycopg2
from psycopg2.extras import Json, execute_values
from dotenv import load_dotenv

from company_identity import workday_company_overrides
from location_normalizer import normalize_location


load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def connection():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def company_id(name: str) -> str:
    return "C" + hashlib.md5(name.encode()).hexdigest()[:10]


def repair(*, apply: bool) -> dict[str, int]:
    stats = {"workday_jobs": 0, "discovery_labels": 0, "locations": 0, "evidence": 0}
    with connection() as conn, conn.cursor() as cur:
        for tenant, name in workday_company_overrides().items():
            cid = company_id(name)
            cur.execute(
                "INSERT INTO companies(company_id,company_name) VALUES(%s,%s) ON CONFLICT(company_id) DO NOTHING",
                (cid, name),
            )
            cur.execute(
                """UPDATE job_postings SET company_id=%s
                   WHERE ingestion_source='workday' AND lower(crawl_tenant)=%s
                     AND company_id IS DISTINCT FROM %s""",
                (cid, tenant, cid),
            )
            stats["workday_jobs"] += cur.rowcount
            cur.execute(
                """UPDATE discovered_companies SET company_name=%s
                   WHERE ats_source='workday'
                     AND lower(split_part(board_token,'/',1))=%s
                     AND company_name IS DISTINCT FROM %s""",
                (name, tenant, name),
            )
            stats["discovery_labels"] += cur.rowcount

        cur.execute(
            """SELECT job_id,loc_city,loc_state,workplace_type
               FROM job_postings
               WHERE data_tier=1 AND lower(COALESCE(loc_country,'')) IN ('','unknown')"""
        )
        repairs = []
        for job_id, city, state, workplace in cur.fetchall():
            raw = ", ".join(value for value in (city, state) if value)
            normalized = normalize_location(raw or None, workplace)
            if normalized.country == "unknown":
                continue
            evidence = {
                "method": "publication_quality_renormalization",
                "raw_legacy_location": raw or None,
                "normalized_city": normalized.city,
                "normalized_state": normalized.state,
                "normalized_country": normalized.country,
            }
            repairs.append((normalized.city, normalized.state, normalized.country, Json(evidence), job_id))
        if repairs:
            execute_values(
                cur,
                """UPDATE job_postings AS jp SET
                       loc_city=v.city,loc_state=v.state,loc_country=v.country,
                       location_evidence=v.evidence
                   FROM (VALUES %s) AS v(city,state,country,evidence,job_id)
                   WHERE jp.job_id=v.job_id""",
                repairs,
                template="(%s,%s,%s,%s::jsonb,%s)",
                page_size=5000,
            )
            stats["locations"] = len(repairs)

        cur.execute(
            """UPDATE job_postings
               SET location_evidence=jsonb_build_object(
                 'method','legacy_normalized_location',
                 'normalized_city',loc_city,
                 'normalized_state',loc_state,
                 'normalized_country',loc_country
               )
               WHERE data_tier=1
                 AND lower(COALESCE(loc_country,'')) IN ('us','united states','usa')
                 AND location_evidence='{}'::jsonb"""
        )
        stats["evidence"] = cur.rowcount
        if not apply:
            conn.rollback()
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    print(repair(apply=args.apply))


if __name__ == "__main__":
    main()
