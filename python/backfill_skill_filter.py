#!/usr/bin/env python3
"""
backfill_skill_filter.py

Retroactively apply the skill-match filter to all raw tier-1 jobs.
Jobs with 0 skill aliases in their description → status='ignored'.
Jobs with 1+ skill aliases → left alone (status stays 'raw').

Usage:
    python python/backfill_skill_filter.py                 # dry-run, full set
    python python/backfill_skill_filter.py --limit 1000    # dry-run, 1000 random
    python python/backfill_skill_filter.py --apply         # write to DB, full set
    python python/backfill_skill_filter.py --apply --limit 1000  # write, 1000 sample
"""

import argparse
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))
from vertical_taxonomy import VERTICALS

BATCH_SIZE = 500

# Canonical skill names (lowercased) whose bare form must NOT be used as a filter
# alias — they were intentionally removed from their aliases list because they
# collide with common English words or generic management terms.
# Without this set, build_skill_filter_pattern() would re-add them automatically
# via the `canonical = skill_name.lower()` path.
_SKIP_CANONICAL: frozenset[str] = frozenset({
    "spring",             # Spring framework → collides with the season
    "excel",              # Microsoft Excel → collides with the verb "excel at"
    "sap",                # SAP ERP → collides with "sap strength / sap morale"
    "outreach",           # Outreach.io → collides with "community outreach"
    "principle",          # Principle app → collides with "ethical principles"
    "lean",               # Lean methodology → collides with lean manufacturing / physique
    "project management", # too generic; use "pmp", "agile project management" etc.
    "change management",  # too generic; use "itil change management" etc.
    "swift",              # Swift (iOS lang) → collides with the adjective
    "monday.com",         # Monday.com → redundant, covered by alias "monday.com"
    "rest",               # REST (API) → collides with "the rest of the team" / "rest assured"
})

# Aliases that are too short/common to trust alone.
# "go" collides with the verb; "java" collides with the island / coffee.
# These only count as a skill hit when the description also has another alias match.
NOISY_ALIASES: frozenset[str] = frozenset({"go", "java"})


# ── Skill filter ───────────────────────────────────────────────────────────────

def build_skill_filter_pattern() -> re.Pattern:
    """
    Compile a single word-boundary OR-regex from every alias in vertical_taxonomy.

    Only uses the explicit "aliases" list for each skill — does NOT auto-add the
    canonical skill name, because several canonicals (spring, excel, sap, …) were
    intentionally removed from their alias lists to prevent false-positive matches.
    Skills that need their canonical form matched have it in their aliases list.

    Aliases < 3 chars are skipped (e.g. 'r', 'go', 'py' all have longer forms).
    Special regex chars (c#, c++, .net, …) are re.escape()'d.
    """
    import skill_taxonomy
    aliases: set[str] = set()
    for vskills in skill_taxonomy.skills_by_vertical().values():
        for skill_name, skill_data in vskills.items():
            canonical = skill_name.lower()
            # Only fall back to canonical if it isn't in the skip list AND isn't
            # already covered by the aliases list (avoids double-adding).
            if len(canonical) >= 3 and canonical not in _SKIP_CANONICAL:
                aliases.add(canonical)
            for alias in skill_data.get("aliases", []):
                a = alias.lower()
                if len(a) >= 3:
                    aliases.add(a)
    parts = [r"\b" + re.escape(a) + r"\b" for a in sorted(aliases)]
    return re.compile("|".join(parts), re.IGNORECASE)


def has_skill_match(pattern: re.Pattern, description: str) -> bool:
    """
    Returns True if the description contains at least one non-noisy skill alias,
    OR contains 2+ total unique alias matches (even if all are noisy).

    'go' and 'java' are noisy — they collide with the English verb/noun.
    A single match of only a noisy alias is not enough to keep a job.
    """
    if not description:
        return False
    matched = {m.lower() for m in pattern.findall(description)}
    if not matched:
        return False
    non_noisy = matched - NOISY_ALIASES
    return bool(non_noisy) or len(matched) >= 2


# ── DB helpers ─────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
        dbname="job_analytics",
    )


# ── Main ───────────────────────────────────────────────────────────────────────

def run(apply: bool, limit: Optional[int]):
    pattern = build_skill_filter_pattern()
    alias_count = len(re.findall(r"\\b", pattern.pattern)) // 2
    print(f"Skill filter ready: {alias_count} alias patterns")

    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    if limit:
        cur.execute(
            """
            SELECT jp.job_id, jp.description_text, r.role_name
            FROM job_postings jp
            LEFT JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.status = 'raw' AND jp.data_tier = 1
            ORDER BY random()
            LIMIT %s
            """,
            (limit,),
        )
    else:
        cur.execute(
            """
            SELECT jp.job_id, jp.description_text, r.role_name
            FROM job_postings jp
            LEFT JOIN roles r ON r.role_id = jp.role_id
            WHERE jp.status = 'raw' AND jp.data_tier = 1
            """
        )

    jobs = cur.fetchall()
    total = len(jobs)
    print(f"Evaluating {total:,} jobs...")

    keep_ids: list[str] = []
    drop_ids: list[str] = []
    keep_samples: list[tuple[str, str]] = []  # (title, matched_alias)
    drop_samples: list[str] = []

    for job in jobs:
        desc = job["description_text"] or ""
        title = (job["role_name"] or "").strip()

        if has_skill_match(pattern, desc):
            keep_ids.append(job["job_id"])
            if len(keep_samples) < 15:
                m = pattern.search(desc)
                keep_samples.append((title, m.group(0) if m else "?"))
        else:
            drop_ids.append(job["job_id"])
            if len(drop_samples) < 25:
                drop_samples.append(title)

    # ── Report ─────────────────────────────────────────────────────────────────
    pct_keep = 100 * len(keep_ids) / total if total else 0
    pct_drop = 100 * len(drop_ids) / total if total else 0

    print(f"\n{'='*60}")
    print(f"SKILL FILTER RESULTS  ({total:,} jobs evaluated)")
    print(f"{'='*60}")
    print(f"  KEEP (1+ skill match):  {len(keep_ids):>7,}  ({pct_keep:.1f}%)")
    print(f"  DROP (0 skill matches): {len(drop_ids):>7,}  ({pct_drop:.1f}%)")

    print(f"\nSample KEEP  (title → first matched alias):")
    for title, skill in keep_samples:
        print(f"  {title[:52]:<52} → {skill}")

    print(f"\nSample DROP  (first {len(drop_samples)} titles):")
    for title in drop_samples:
        print(f"  {title}")

    if not apply:
        print(f"\nDry-run — no DB changes.")
        print(f"Re-run with --apply to mark {len(drop_ids):,} jobs as 'ignored'.")
        return

    # ── Apply ──────────────────────────────────────────────────────────────────
    print(f"\nApplying {len(drop_ids):,} → 'ignored' updates...")
    start = time.time()
    updated = 0

    for i in range(0, len(drop_ids), BATCH_SIZE):
        batch = drop_ids[i : i + BATCH_SIZE]
        cur.execute(
            "UPDATE job_postings SET status = 'ignored' WHERE job_id = ANY(%s)",
            (batch,),
        )
        conn.commit()
        updated += len(batch)
        if updated % 5000 == 0 or updated == len(drop_ids):
            elapsed = time.time() - start
            print(f"  {updated:,} / {len(drop_ids):,}  ({elapsed:.0f}s)")

    print(f"\nDone.")
    print(f"  {len(drop_ids):,} jobs → status='ignored'")
    print(f"  {len(keep_ids):,} jobs remain status='raw'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Skill-match backfill: mark raw tier-1 jobs with 0 skill matches as ignored"
    )
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run)")
    parser.add_argument("--limit", type=int, metavar="N", help="Process only N random jobs")
    args = parser.parse_args()
    run(apply=args.apply, limit=args.limit)
