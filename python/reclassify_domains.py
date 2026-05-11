#!/usr/bin/env python3
"""
reclassify_domains.py

Re-run domain classification on all raw tier-1 survivors.
Overwrites existing domain assignments — including old data_ml defaults.
Uses title regex → skill alias counts → NULL (no LLM, no fake defaults).

NULL domain after this pass = no clear signal; candidate for a targeted LLM pass.

Usage:
    python python/reclassify_domains.py            # dry-run
    python python/reclassify_domains.py --apply    # write to DB
    python python/reclassify_domains.py --limit N  # dry-run on N random jobs
"""

import argparse
import os
import sys
import time
from pathlib import Path

import psycopg2
import psycopg2.extras
from psycopg2.extras import execute_batch
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from classify_domain import build_alias_map, classify_domain

BATCH_SIZE = 500


def get_conn():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        dbname="job_analytics",
    )


def run(apply: bool, limit):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    alias_map = build_alias_map(conn)
    print(f"Alias map: {len(alias_map):,} entries")

    q = """
        SELECT jp.job_id, r.role_name AS title, jp.description_text,
               jp.domain AS current_domain
        FROM job_postings jp
        LEFT JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.status = 'raw' AND jp.data_tier = 1
    """
    if limit:
        q += f" ORDER BY random() LIMIT {limit}"

    cur.execute(q)
    jobs = cur.fetchall()
    total = len(jobs)
    print(f"Classifying {total:,} survivors (title + skill, no LLM)...")

    results = []          # (job_id, new_domain, new_secondary) — changed rows only
    samples: list[tuple[str, str, str]] = []  # (old, new, title) — for dry-run display
    unchanged = 0
    method_counts: dict[str, int] = {}
    domain_counts: dict[str, int] = {}
    old_domain_counts: dict[str, int] = {}
    old_to_new: dict[str, dict[str, int]] = {}  # old_domain → {new_domain: count}

    for job in jobs:
        domain, secondary, method = classify_domain(
            job["title"] or "",
            job["description_text"] or "",
            alias_map,
            use_llm=False,
        )
        method_counts[method] = method_counts.get(method, 0) + 1
        dk = domain or "NULL"
        domain_counts[dk] = domain_counts.get(dk, 0) + 1

        old = job["current_domain"] or "NULL"
        old_domain_counts[old] = old_domain_counts.get(old, 0) + 1
        if old not in old_to_new:
            old_to_new[old] = {}
        old_to_new[old][dk] = old_to_new[old].get(dk, 0) + 1

        if dk != old:
            results.append((job["job_id"], domain, secondary or None))
            if len(samples) < 20:
                samples.append((old, dk, job["title"] or ""))
        else:
            unchanged += 1

    # ── Report ─────────────────────────────────────────────────────────────────
    changed = len(results)
    print(f"\n=== CLASSIFICATION METHOD ===")
    for m, c in sorted(method_counts.items(), key=lambda x: -x[1]):
        print(f"  {m:<15} {c:>8,}  ({100*c/total:.1f}%)")

    print(f"\n=== NEW DOMAIN BREAKDOWN ===")
    all_verticals = sorted(set(list(domain_counts) + list(old_domain_counts)))
    print(f"  {'domain':<25} {'old':>8}  {'new':>8}  {'delta':>8}")
    print(f"  {'-'*25} {'-'*8}  {'-'*8}  {'-'*8}")
    for d in all_verticals:
        old_c = old_domain_counts.get(d, 0)
        new_c = domain_counts.get(d, 0)
        delta = new_c - old_c
        sign = "+" if delta > 0 else ""
        print(f"  {d:<25} {old_c:>8,}  {new_c:>8,}  {sign}{delta:>7,}")

    print(f"\n=== CHANGE SUMMARY ===")
    print(f"  Changed:   {changed:>8,}  ({100*changed/total:.1f}%)")
    print(f"  Unchanged: {unchanged:>8,}  ({100*unchanged/total:.1f}%)")

    print(f"\n=== TOP REASSIGNMENT FLOWS ===")
    flows: list[tuple[int, str, str]] = []
    for od, nd_map in old_to_new.items():
        for nd, c in nd_map.items():
            if od != nd:
                flows.append((c, od, nd))
    for c, od, nd in sorted(flows, key=lambda x: -x[0])[:15]:
        print(f"  {od:<20} → {nd:<20} {c:>7,}")

    print(f"\n=== SAMPLE CHANGED ROWS (first 20) ===")
    for old, new, title in samples:
        print(f"  {old:<20} → {new:<20} {title[:60]}")

    if not apply:
        print(f"\nDry-run — no DB changes. Re-run with --apply to write {changed:,} changed rows.")
        return

    # ── Apply ──────────────────────────────────────────────────────────────────
    if not results:
        print(f"\nAll {total:,} rows already have the correct domain — nothing to write.")
        return
    print(f"\nWriting {changed:,} domain updates ({unchanged:,} unchanged rows skipped)...")
    start = time.time()
    written = 0

    for i in range(0, len(results), BATCH_SIZE):
        batch = results[i : i + BATCH_SIZE]
        execute_batch(
            cur,
            """
            UPDATE job_postings
            SET domain               = %s,
                domain_secondary     = %s,
                domain_classified_at = now()
            WHERE job_id = %s
            """,
            [(domain, secondary, job_id) for job_id, domain, secondary in batch],
            page_size=BATCH_SIZE,
        )
        conn.commit()
        written += len(batch)
        if written % 10000 == 0 or written == changed:
            print(f"  {written:,} / {changed:,}  ({time.time()-start:.0f}s)")

    print(f"\nDone. {changed:,} domain records updated ({unchanged:,} already correct).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Write to DB (default: dry-run)")
    parser.add_argument("--limit", type=int, metavar="N", help="Process N random jobs only")
    args = parser.parse_args()
    run(apply=args.apply, limit=args.limit)
