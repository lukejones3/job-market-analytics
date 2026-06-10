#!/usr/bin/env python3
"""
Assigns vertical + also_in to all existing rows in the skills table.

Usage:
    python python/backfill_skill_verticals.py          # dry-run (default)
    python python/backfill_skill_verticals.py --apply  # write to DB

Logic:
  1. Build a reverse-lookup from taxonomy: alias_text -> (primary_vertical, also_in_set)
  2. For each DB skill, look up its canonical name then its aliases
  3. If matched in taxonomy -> use that vertical
  4. If unmatched (pre-existing data/ML skill not yet in taxonomy) -> vertical='data_ml'

Does NOT touch skill_aliases rows — only updates skills.vertical and skills.also_in.
Safe to re-run: uses ON CONFLICT DO UPDATE so already-assigned skills are refreshed
if the taxonomy changes.
"""

import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor, execute_values

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import domain_taxonomy

VALID_VERTICALS = set(domain_taxonomy.domains())


# ── Build taxonomy reverse-lookup ────────────────────────────────────────────

def build_taxonomy_map() -> dict[str, tuple[str, list[str]]]:
    """
    Returns: { canonical_name_lower -> (primary_vertical, sorted_also_in_list) }

    For skills that appear in multiple verticals, the FIRST vertical in the
    VERTICALS dict iteration order is primary; all others become also_in.
    """
    primary: dict[str, str] = {}
    also_in: dict[str, set] = {}

    import skill_taxonomy
    for vkey, vskills in skill_taxonomy.skills_by_vertical().items():
        for skill_name, skill_data in vskills.items():
            sname = skill_name.lower()
            if sname not in primary:
                primary[sname] = vkey
                also_in[sname] = set(skill_data.get("also_in", []))
            else:
                # Subsequent occurrence: this vertical becomes secondary
                also_in[sname].add(vkey)
                also_in[sname].update(skill_data.get("also_in", []))

    # Remove primary from its own also_in (can appear if cross-references exist)
    result = {}
    for sname, vkey in primary.items():
        secondary = sorted(also_in[sname] - {vkey})
        result[sname] = (vkey, secondary)

    return result


def build_alias_to_canonical(taxonomy_map: dict) -> dict[str, str]:
    """
    Returns: { alias_text_lower -> canonical_skill_name_lower }
    Lets us match a DB skill's aliases against the taxonomy.
    """
    alias_map: dict[str, str] = {}
    import skill_taxonomy
    for vkey, vskills in skill_taxonomy.skills_by_vertical().items():
        for skill_name, skill_data in vskills.items():
            sname = skill_name.lower()
            alias_map[sname] = sname  # canonical matches itself
            for alias in skill_data.get("aliases", []):
                alias_map[alias.lower()] = sname
    return alias_map


# ── DB helpers ────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", 5432),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def fetch_skills_with_aliases(conn) -> list[dict]:
    """Returns all skills with their alias_text list."""
    with conn.cursor(cursor_factory=DictCursor) as cur:
        cur.execute("""
            SELECT s.skill_id, s.skill_name, s.vertical, s.also_in,
                   COALESCE(array_agg(sa.alias_text) FILTER (WHERE sa.alias_text IS NOT NULL), '{}') AS aliases
            FROM skills s
            LEFT JOIN skill_aliases sa ON sa.skill_id = s.skill_id
            GROUP BY s.skill_id, s.skill_name, s.vertical, s.also_in
            ORDER BY s.skill_name
        """)
        return [dict(r) for r in cur.fetchall()]


# ── Classification ─────────────────────────────────────────────────────────────

def classify_skill(
    skill: dict,
    taxonomy_map: dict,
    alias_to_canonical: dict,
) -> tuple[str, list[str], str]:
    """
    Returns (vertical, also_in_list, match_source).
    match_source is a short description of how the match was made (for dry-run output).
    """
    sname = skill["skill_name"].lower()

    # 1. Direct canonical name match
    if sname in taxonomy_map:
        vkey, secondary = taxonomy_map[sname]
        return vkey, secondary, f"canonical '{skill['skill_name']}'"

    # 2. Match via any alias in skill_aliases
    for alias in skill.get("aliases", []):
        canonical = alias_to_canonical.get(alias.lower())
        if canonical and canonical in taxonomy_map:
            vkey, secondary = taxonomy_map[canonical]
            return vkey, secondary, f"alias '{alias}'"

    # 3. Fallback: all pre-existing skills are data/ML
    return "data_ml", [], "fallback (pre-existing data/ML skill)"


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Backfill vertical + also_in on existing skills")
    parser.add_argument("--apply", action="store_true", help="Write changes to DB (default: dry-run)")
    args = parser.parse_args()

    taxonomy_map = build_taxonomy_map()
    alias_to_canonical = build_alias_to_canonical(taxonomy_map)

    conn = get_conn()
    skills = fetch_skills_with_aliases(conn)

    print(f"{'DRY RUN' if not args.apply else 'APPLYING'} — {len(skills)} skills to classify\n")

    changes = []  # (skill_id, vertical, also_in_list)

    fmt_also = lambda lst: (", ".join(lst) if lst else "—")

    col_w = (20, 14, 14, 36, 36, 36)
    header = (
        f"{'skill_id':<{col_w[0]}} {'skill_name':<{col_w[1]}} "
        f"{'cur_vertical':<{col_w[2]}} {'→ vertical':<{col_w[3]}} "
        f"{'→ also_in':<{col_w[4]}} {'match_source':<{col_w[5]}}"
    )
    print(header)
    print("─" * sum(col_w))

    changed_count = 0
    already_set_count = 0

    for skill in skills:
        vertical, also_in_list, source = classify_skill(skill, taxonomy_map, alias_to_canonical)
        cur_vertical = skill["vertical"] or "NULL"
        cur_also_in  = skill["also_in"] or []

        is_change = (
            skill["vertical"] != vertical
            or sorted(cur_also_in) != sorted(also_in_list)
        )

        if is_change:
            changed_count += 1
            changes.append((skill["skill_id"], vertical, also_in_list))
            print(
                f"{skill['skill_id']:<{col_w[0]}} {skill['skill_name']:<{col_w[1]}} "
                f"{cur_vertical:<{col_w[2]}} → {vertical:<{col_w[3]-2}} "
                f"{fmt_also(also_in_list):<{col_w[4]}} {source}"
            )
        else:
            already_set_count += 1

    print(f"\nSummary:")
    print(f"  Skills to update:     {changed_count}")
    print(f"  Already correct:      {already_set_count}")

    if not args.apply:
        print(f"\n(Dry run — pass --apply to write {changed_count} updates to DB)")
        conn.close()
        return

    if not changes:
        print("\nNothing to update.")
        conn.close()
        return

    print(f"\nWriting {len(changes)} updates...")
    with conn.cursor() as cur:
        for skill_id, vertical, also_in_list in changes:
            cur.execute(
                """
                UPDATE skills
                SET vertical = %s,
                    also_in  = %s
                WHERE skill_id = %s
                """,
                (vertical, also_in_list if also_in_list else None, skill_id),
            )
    conn.commit()
    print(f"Done. {len(changes)} skills updated.")
    conn.close()


if __name__ == "__main__":
    main()
