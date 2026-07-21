# Skill Filter Proposal

Status: REVIEW ONLY — nothing has been changed yet.

---

## Context

149,897 raw tier-1 jobs in DB, majority junk.
Root cause: `_is_target_role()` returns `bool(title)` (accepts everything).
Fix: skill-match gate at ingest + backfill on existing rows.

Test run: 1,000 random jobs, dry-run only.
- KEEP (1+ skill match): 513 (51.3%)
- DROP (0 skill matches): 487 (48.7%)

---

## CHANGE 1: ingest_jobs.py

### Where

File: `python/ingest_jobs.py`

Two additions near the top (after the `_ALIAS_MAP` block around line 70),
plus one line change inside `ingest_job()` at line ~1238 and one at line ~1283.

### Addition 1: two new functions (insert after line ~76)

```python
# ── Skill-match gate ───────────────────────────────────────────────────────────
_SKILL_FILTER_PATTERN: Optional[re.Pattern] = None

def _build_skill_filter_pattern() -> re.Pattern:
    """
    Compile a word-boundary OR-regex from all taxonomy skill aliases (length >= 3).
    Aliases < 3 chars are skipped — each has a longer alias that covers the same
    skill (e.g. 'r' -> 'r programming', 'go' -> 'golang', 'py' -> 'python').
    Special regex chars (c#, c++, .net) are re.escape()'d.
    """
    from vertical_taxonomy import VERTICALS
    aliases: set[str] = set()
    for vdata in VERTICALS.values():
        for skill_name, skill_data in vdata["skills"].items():
            canonical = skill_name.lower()
            if len(canonical) >= 3:
                aliases.add(canonical)
            for alias in skill_data.get("aliases", []):
                a = alias.lower()
                if len(a) >= 3:
                    aliases.add(a)
    parts = [r"\b" + re.escape(a) + r"\b" for a in sorted(aliases)]
    return re.compile("|".join(parts), re.IGNORECASE)


def _has_skill_match(description: str) -> bool:
    """Returns True if description contains at least one known skill alias."""
    global _SKILL_FILTER_PATTERN
    if _SKILL_FILTER_PATTERN is None:
        _SKILL_FILTER_PATTERN = _build_skill_filter_pattern()
    return bool(description and _SKILL_FILTER_PATTERN.search(description))
```

### Addition 2: compute status before INSERT (insert after line ~1238, before cur.execute)

```python
    # Skill-match gate: 0 known skill aliases in description → out of feed
    ingest_status = "raw" if _has_skill_match(clean_description) else "ignored"
```

### Change 3: use ingest_status instead of hardcoded "raw" (line ~1283)

Before:
```python
            "raw",
```

After:
```python
            ingest_status,
```

---

## CHANGE 2: backfill_skill_filter.py (NEW FILE)

File: `python/backfill_skill_filter.py`
Status: ALREADY WRITTEN — file exists at that path, nothing applied to DB yet.

Full contents:

```python
#!/usr/bin/env python3
"""
backfill_skill_filter.py

Retroactively apply the skill-match filter to all raw tier-1 jobs.
Jobs with 0 skill aliases in their description -> status='ignored'.
Jobs with 1+ skill aliases -> left alone (status stays 'raw').

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


# ── Skill filter ───────────────────────────────────────────────────────────────

def build_skill_filter_pattern() -> re.Pattern:
    """
    Compile a single word-boundary OR-regex from every alias in vertical_taxonomy.
    Aliases < 3 chars are skipped -- each has a longer alias that covers the same
    skill (e.g. 'r'->'r programming', 'go'->'golang', 'py'->'python').
    Special regex chars in aliases (c#, c++, .net, etc.) are re.escape()'d.
    """
    aliases: set[str] = set()
    for vdata in VERTICALS.values():
        for skill_name, skill_data in vdata["skills"].items():
            canonical = skill_name.lower()
            if len(canonical) >= 3:
                aliases.add(canonical)
            for alias in skill_data.get("aliases", []):
                a = alias.lower()
                if len(a) >= 3:
                    aliases.add(a)
    parts = [r"\b" + re.escape(a) + r"\b" for a in sorted(aliases)]
    return re.compile("|".join(parts), re.IGNORECASE)


def has_skill_match(pattern: re.Pattern, description: str) -> bool:
    return bool(description and pattern.search(description))


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

    print(f"\nSample KEEP  (title -> first matched alias):")
    for title, skill in keep_samples:
        print(f"  {title[:52]:<52} -> {skill}")

    print(f"\nSample DROP  (first {len(drop_samples)} titles):")
    for title in drop_samples:
        print(f"  {title}")

    if not apply:
        print(f"\nDry-run -- no DB changes.")
        print(f"Re-run with --apply to mark {len(drop_ids):,} jobs as 'ignored'.")
        return

    # ── Apply ──────────────────────────────────────────────────────────────────
    print(f"\nApplying {len(drop_ids):,} -> 'ignored' updates...")
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
    print(f"  {len(drop_ids):,} jobs -> status='ignored'")
    print(f"  {len(keep_ids):,} jobs remain status='raw'")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Skill-match backfill: mark raw tier-1 jobs with 0 skill matches as ignored"
    )
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run)")
    parser.add_argument("--limit", type=int, metavar="N", help="Process only N random jobs")
    args = parser.parse_args()
    run(apply=args.apply, limit=args.limit)
```

---

## Known issues / decisions needed

### 1. Alias collisions (false positives — junk kept)

These aliases from vertical_taxonomy match in non-tech contexts:

| Alias | Intended skill | Collision |
|-------|---------------|-----------|
| `lean` | Lean methodology (ops) | Lean manufacturing — keeps factory/production jobs |
| `principle` | Principle (design app) | "ethical principles" — keeps therapist/nursing jobs |
| `aws` | Amazon Web Services | AWS = American Welding Society certification |
| `monday` | Monday.com | "every Monday" in schedule text |
| `excel` | Microsoft Excel | "excel at customer service" (verb) |
| `spring` | Spring framework | spring (season / noun) |
| `swift` | Swift (iOS lang) | swift (adjective) |

Fix option: remove the bare short-form aliases and rely only on unambiguous longer aliases:
- `lean` -> keep only `lean six sigma`, `lean methodology`
- `principle` -> keep only `principle app`
- `aws` -> still needed (Amazon Web Services is primary meaning in a tech JD context)

### 2. False negatives (real jobs dropped)

Jobs with zero skill alias coverage despite being knowledge-worker roles:

- Commissioning Engineer, Data Center Controls (4,382 chars, no alias match — uses BACnet/HVAC vocabulary)
- Some high-level executive descriptions (abstract language, no tool names)
- Jobs with empty/near-empty description_text stored

These are acceptable losses for an emergency cleanup — they can be re-ingested or manually promoted later.

### 3. Threshold decision

| Threshold | Keep (of 149K) | Drop | Notes |
|-----------|----------------|------|-------|
| 1+ match  | ~52% (~78K)    | ~48% (~72K) | Too permissive; collision aliases inflate keep |
| 2+ matches | ~32% (~48K)   | ~68% (~101K) | Closer to your 30-50K target; drops some legit single-skill jobs |

Recommendation: fix collision aliases first (remove `lean`/`principle`/`spring`/`swift` bare forms), then run at threshold=1. Should land closer to 35-45% keep.

---

## Execution order (once you approve)

1. Optionally fix collision aliases in vertical_taxonomy.py
2. Apply CHANGE 1 to ingest_jobs.py
3. Run: `python python/backfill_skill_filter.py --apply` (full 149K, ~5 min)
4. Run domain re-classify (Step 3 from original plan)
5. Re-run enrichment on survivors (Step 4)
