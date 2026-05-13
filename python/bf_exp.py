#!/usr/bin/env python3
"""
bf_exp.py — Backfill experience_level for active tier-1 jobs based on title keywords.

Conservative policy:
- Title contains Senior/Sr/Staff/Principal/Lead/Head/Director/Manager/VP/Chief → force 'senior'
- Title contains Intern/Junior/Jr/New Grad/Entry/Apprentice/Trainee/Early Career → force 'entry'
- Title contains Associate → force 'associate'
- Otherwise: leave existing value alone (LLM may have inferred from description)

Audit log goes to experience_backfill_changes for full rollback.

Usage:
    python python/bf_exp.py --dry-run
    python python/bf_exp.py
"""

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path("/opt/job-market-analytics")
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "python"))

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

BATCH_TAG = f"exp_backfill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

# sr./jr. end with a non-word char (dot) so \b fails after them — use lookahead instead
SENIOR_RE = re.compile(
    r"\b(?:senior|staff|principal|lead|head\s+of|director|manager|vp|vice\s+president|chief|distinguished|fellow)\b"
    r"|\bsr\.?(?=\W|$)",
    re.I,
)
ENTRY_RE = re.compile(
    r"\b(?:intern(?:ship)?|entry[\s-]?level|entry|junior|new\s+grad|graduate\s+program|apprentice|trainee|early\s+career)\b"
    r"|\bjr\.?(?=\W|$)",
    re.I,
)
ASSOC_RE = re.compile(r"\bassociate\b", re.I)


def get_conn():
    return psycopg2.connect(
        dbname=os.environ["PGDATABASE"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        host=os.environ["PGHOST"],
        port=int(os.environ["PGPORT"]),
    )


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS experience_backfill_changes (
    id SERIAL PRIMARY KEY,
    job_id TEXT NOT NULL,
    role_name TEXT,
    old_level TEXT,
    new_level TEXT,
    batch_tag TEXT,
    changed_at TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_ebc_job_id ON experience_backfill_changes(job_id);
CREATE INDEX IF NOT EXISTS idx_ebc_batch ON experience_backfill_changes(batch_tag);
"""


def ensure_schema():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
        conn.commit()


def classify(title: str) -> str | None:
    """Return target level if title has clear signal, else None.

    Entry beats senior — 'Jr. Manager' and 'Product Manager Intern' are entry.
    """
    if ENTRY_RE.search(title):
        return "entry"
    if SENIOR_RE.search(title):
        return "senior"
    if ASSOC_RE.search(title):
        return "associate"
    return None


def fetch_candidates(rescan_all: bool = False):
    tier_clause = "" if rescan_all else "AND COALESCE(jp.data_tier, 1) = 1"
    sql = f"""
        SELECT jp.job_id, jp.experience_level, r.role_name
        FROM job_postings jp
        LEFT JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.status = 'raw'
          {tier_clause}
          AND r.role_name IS NOT NULL
    """
    with get_conn() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(sql)
        return cur.fetchall()


def dry_run(jobs):
    flips = {"senior": 0, "entry": 0, "associate": 0}
    no_signal = 0
    no_change = 0
    samples = []
    for j in jobs:
        target = classify(j["role_name"])
        if not target:
            no_signal += 1
            continue
        if target == j["experience_level"]:
            no_change += 1
            continue
        flips[target] += 1
        if len(samples) < 10:
            samples.append((j["role_name"], j["experience_level"], target))

    print(f"\n{'=' * 70}")
    print(f"DRY RUN — {len(jobs)} candidates")
    print(f"{'=' * 70}\n")
    print(f"  Will flip to 'senior':    {flips['senior']}")
    print(f"  Will flip to 'entry':     {flips['entry']}")
    print(f"  Will flip to 'associate': {flips['associate']}")
    print(f"  No-change (already correct): {no_change}")
    print(f"  Title has no clear signal (skip): {no_signal}")
    print(f"\n  Sample flips:")
    for title, current, target in samples:
        print(f"    {title[:55]:55} | {current!r:12} -> {target!r}")
    total = sum(flips.values())
    print(f"\n  Total flips: {total}")
    print(f"\nRun without --dry-run to execute.\n")


def real_run(jobs):
    print(f"\n{'=' * 70}")
    print(f"BACKFILL — batch tag: {BATCH_TAG}")
    print(f"{'=' * 70}\n")

    flipped = 0
    errors = 0

    with get_conn() as conn:
        for i, j in enumerate(jobs, 1):
            target = classify(j["role_name"])
            if not target or target == j["experience_level"]:
                continue
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE job_postings SET experience_level = %s WHERE job_id = %s",
                        (target, j["job_id"]),
                    )
                    cur.execute(
                        """
                        INSERT INTO experience_backfill_changes
                          (job_id, role_name, old_level, new_level, batch_tag)
                        VALUES (%s, %s, %s, %s, %s)
                        """,
                        (j["job_id"], j["role_name"], j["experience_level"], target, BATCH_TAG),
                    )
                flipped += 1
            except Exception as e:
                errors += 1
                print(f"  ERR {j['job_id']}: {e}")

            if i % 1000 == 0 or i == len(jobs):
                print(f"  [{i}/{len(jobs)}] flipped={flipped} errors={errors}")
        conn.commit()

    print(f"\n{'=' * 70}")
    print(f"DONE")
    print(f"{'=' * 70}")
    print(f"  Flipped: {flipped}")
    print(f"  Errors: {errors}")
    print(f"  Batch tag: {BATCH_TAG}")
    print(f"\nRollback if needed:")
    print(f"  UPDATE job_postings jp SET experience_level = ebc.old_level")
    print(f"  FROM experience_backfill_changes ebc")
    print(f"  WHERE jp.job_id = ebc.job_id AND ebc.batch_tag = '{BATCH_TAG}';")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="Show what would change without writing")
    ap.add_argument("--apply", action="store_true", help="Write changes (default when --dry-run omitted)")
    ap.add_argument("--rescan-all", action="store_true", help="Include all data_tiers, not just tier-1")
    args = ap.parse_args()

    ensure_schema()
    jobs = fetch_candidates(rescan_all=args.rescan_all)

    if not jobs:
        print("No candidates found.")
        return

    if args.dry_run:
        dry_run(jobs)
    else:
        real_run(jobs)


if __name__ == "__main__":
    main()
