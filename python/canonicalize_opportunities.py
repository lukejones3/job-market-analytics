#!/usr/bin/env python3
"""Group materially identical source postings without deleting raw evidence.

Exact source URLs and requisition ids are intentionally preserved on every raw
row.  The public snapshot emits one representative per canonical opportunity.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import os
import re
from collections import defaultdict
from pathlib import Path

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
NON_WORD_RE = re.compile(r"[^a-z]+")


def connection():
    return psycopg2.connect(host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"))


def normalized_description(value: str | None) -> str:
    """Remove source boilerplate noise used only to mint tracking variants."""
    text = html.unescape(TAG_RE.sub(" ", value or "")).lower()
    text = URL_RE.sub(" ", text)
    # Requisition numbers, tracking ids, and punctuation are not meaningful
    # differences between otherwise identical employer-authored descriptions.
    return " ".join(NON_WORD_RE.sub(" ", text).split())


def shingles(value: str, width: int = 5) -> frozenset[tuple[str, ...]]:
    words = value.split()
    if len(words) < width:
        return frozenset([tuple(words)]) if words else frozenset()
    return frozenset(tuple(words[index:index + width]) for index in range(len(words) - width + 1))


def near_identical(left: frozenset, right: frozenset, threshold: float = 0.995) -> bool:
    if not left or not right:
        return False
    # Avoid constructing the union for pairs whose sizes alone make the target
    # similarity impossible.
    if min(len(left), len(right)) / max(len(left), len(right)) < threshold:
        return False
    intersection = len(left & right)
    return intersection / (len(left) + len(right) - intersection) >= threshold


def _canonical_id(parts: tuple[str, ...]) -> str:
    return "CO" + hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()[:20]


def build_updates(rows: list[tuple]) -> list[tuple[str, str]]:
    """Return (job_id, canonical_id) assignments for database candidate rows."""
    assignments: dict[str, str] = {}
    groups: dict[tuple[str, ...], list[tuple[str, str, frozenset, str]]] = defaultdict(list)

    for (job_id, company_id, role_name, description, source_id, country, state, city,
         workplace_type) in rows:
        role_key = "".join(NON_WORD_RE.sub(" ", (role_name or "").lower()).split())
        description_key = normalized_description(description)
        location_key = (
            str(company_id or "unknown"), role_key, str(country or "").lower(),
            str(state or "").lower(), str(city or "").lower(),
            str(workplace_type or "").lower(),
        )
        evidence_key = description_key or f"source:{source_id or job_id}"
        base_id = _canonical_id((*location_key, evidence_key))
        assignments[job_id] = base_id
        if description_key:
            groups[location_key].append((job_id, description_key, shingles(description_key), base_id))

    # Exact normalization already shares base_id. This second pass catches only
    # descriptions that differ by a vanishingly small amount (>=99.5% Jaccard),
    # such as an ATS footer or tracking token, within an identical role/location.
    for members in groups.values():
        if len(members) < 2:
            continue
        parent = list(range(len(members)))

        def find(index: int) -> int:
            while parent[index] != index:
                parent[index] = parent[parent[index]]
                index = parent[index]
            return index

        def union(left: int, right: int) -> None:
            left_root, right_root = find(left), find(right)
            if left_root != right_root:
                parent[max(left_root, right_root)] = min(left_root, right_root)

        for left in range(len(members)):
            for right in range(left + 1, len(members)):
                if members[left][1] == members[right][1] or near_identical(
                    members[left][2], members[right][2]
                ):
                    union(left, right)

        clusters: dict[int, list[int]] = defaultdict(list)
        for index in range(len(members)):
            clusters[find(index)].append(index)
        for indexes in clusters.values():
            cluster_id = min(members[index][3] for index in indexes)
            for index in indexes:
                assignments[members[index][0]] = cluster_id

    return sorted(assignments.items())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    with connection() as conn, conn.cursor() as cur:
        # Schema changes belong to migrations (sql/ingestion_observability.sql).
        # Avoid an ACCESS EXCLUSIVE schema lock around this data backfill.
        cur.execute("SET LOCAL lock_timeout = '5s'")
        cur.execute("""
            SELECT jp.job_id, jp.company_id, r.role_name, jp.description_text,
                   jp.source_id, jp.loc_country, jp.loc_state, jp.loc_city,
                   jp.workplace_type
            FROM job_postings jp
            LEFT JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.data_tier = 1
        """)
        updates = build_updates(cur.fetchall())
        cur.execute("""
            CREATE TEMP TABLE tmp_canonical_updates (
                job_id TEXT PRIMARY KEY,
                canonical_id TEXT NOT NULL
            ) ON COMMIT DROP
        """)
        execute_values(
            cur,
            "INSERT INTO tmp_canonical_updates (job_id, canonical_id) VALUES %s",
            updates,
            page_size=5000,
        )
        cur.execute("""
            UPDATE job_postings jp
               SET canonical_opportunity_id = updates.canonical_id
              FROM tmp_canonical_updates updates
             WHERE jp.job_id = updates.job_id
               AND jp.canonical_opportunity_id IS DISTINCT FROM updates.canonical_id
        """)
        changed = cur.rowcount
        if not args.apply:
            conn.rollback()
        print(f"{'Would update' if not args.apply else 'Updated'} {changed} postings")


if __name__ == "__main__":
    main()
