#!/usr/bin/env python3
"""
Inserts new skills (from vertical_taxonomy.py) that don't yet exist in the
skills table, along with their aliases in skill_aliases.

Usage:
    python python/seed_new_skills.py           # dry-run (default)
    python python/seed_new_skills.py --apply   # write to DB

Rules:
  - A skill is "new" if its canonical_name AND none of its aliases match any
    existing skill_name or alias_text in the DB.
  - skill_id is generated as 'S' + first 10 hex chars of md5(canonical_name.lower()).
  - Aliases already present in skill_aliases (for ANY skill) are skipped silently —
    the seed is idempotent and can be re-run as the taxonomy grows.
  - The taxonomy canonical_name is always inserted as the first alias_text.
  - Does NOT modify existing skills or aliases.
"""

import hashlib
import os
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))
import domain_taxonomy

VALID_VERTICALS = set(domain_taxonomy.domains())


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_skill_id(canonical_name: str) -> str:
    """S + first 10 hex chars of md5(lowercased canonical name)."""
    digest = hashlib.md5(canonical_name.lower().encode()).hexdigest()
    return f"S{digest[:10]}"


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", 5432),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def fetch_existing(conn) -> tuple[set[str], set[str]]:
    """Returns (existing_skill_names_lower, existing_alias_texts_lower)."""
    with conn.cursor() as cur:
        cur.execute("SELECT LOWER(skill_name) FROM skills")
        names = {r[0] for r in cur.fetchall()}
        cur.execute("SELECT LOWER(alias_text) FROM skill_aliases")
        aliases = {r[0] for r in cur.fetchall()}
    return names, aliases


# ── Build new-skill list ──────────────────────────────────────────────────────

def build_new_skills(
    existing_names: set[str],
    existing_aliases: set[str],
) -> list[dict]:
    """
    Walk VERTICALS in order. For each unique canonical skill name:
      - If it matches an existing name or alias -> skip (already in DB).
      - Otherwise -> add to new-skills list.

    Returns list of dicts: {skill_id, skill_name, vertical, also_in,
                             category, weight, safe_aliases}.
    'safe_aliases' = aliases that don't collide with existing alias_text.
    """
    seen_canonical: set[str] = set()
    new_skills: list[dict] = []

    import skill_taxonomy
    for skill_data in skill_taxonomy.skills():
        skill_name = skill_data["skill_name"]
        sname_lower = skill_name.lower()
        if sname_lower in seen_canonical:
            continue
        seen_canonical.add(sname_lower)

        all_aliases = skill_data.get("aliases", [])
        all_forms = [sname_lower] + [a.lower() for a in all_aliases]

        # Check if any form already exists in DB
        hit = next((f for f in all_forms if f in existing_names or f in existing_aliases), None)
        if hit:
            continue  # already in DB — backfill_skill_verticals.py handles vertical assignment

        # Skill is new — compute safe aliases (exclude any that already exist)
        safe_aliases = [a for a in all_aliases if a.lower() not in existing_aliases]

        new_skills.append({
            "skill_id":   skill_data.get("skill_id") or make_skill_id(skill_name),
            "skill_name": skill_name,
            "vertical":   skill_data.get("vertical", ""),
            "also_in":    skill_data.get("also_in", []),
            "category":   skill_data.get("category", ""),
            "weight":     skill_data.get("weight", 1),
            "safe_aliases": safe_aliases,
        })

    # Deduplicate skill_ids (extremely unlikely hash collision, but guard it)
    seen_ids: dict[str, str] = {}
    for s in new_skills:
        if s["skill_id"] in seen_ids:
            raise ValueError(
                f"skill_id collision: {s['skill_id']} for '{s['skill_name']}' "
                f"and '{seen_ids[s['skill_id']]}'"
            )
        seen_ids[s["skill_id"]] = s["skill_name"]

    return new_skills


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Seed new skills from vertical taxonomy into DB")
    parser.add_argument("--apply", action="store_true", help="Write to DB (default: dry-run)")
    parser.add_argument("--vertical", help="Only seed skills for this vertical (optional filter)")
    args = parser.parse_args()

    if args.vertical and args.vertical not in VALID_VERTICALS:
        print(f"Unknown vertical '{args.vertical}'. Valid: {sorted(VALID_VERTICALS)}")
        sys.exit(1)

    conn = get_conn()
    existing_names, existing_aliases = fetch_existing(conn)

    new_skills = build_new_skills(existing_names, existing_aliases)

    if args.vertical:
        new_skills = [s for s in new_skills if s["vertical"] == args.vertical]

    print(f"{'DRY RUN' if not args.apply else 'APPLYING'} — {len(new_skills)} new skills to insert\n")

    # Group by vertical for display
    from collections import defaultdict
    by_vertical: dict[str, list] = defaultdict(list)
    for s in new_skills:
        by_vertical[s["vertical"]].append(s)

    total_aliases = 0
    for vkey in domain_taxonomy.domains():
        skills_in_v = by_vertical.get(vkey, [])
        if not skills_in_v:
            continue
        print(f"  {vkey} ({len(skills_in_v)} new skills):")
        for s in sorted(skills_in_v, key=lambda x: x["skill_name"]):
            alias_str = ", ".join(f"'{a}'" for a in s["safe_aliases"]) or "(none — no non-conflicting aliases)"
            also_str  = ", ".join(s["also_in"]) if s["also_in"] else "—"
            print(f"    {s['skill_id']}  {s['skill_name']:<35s} also_in=[{also_str}]")
            print(f"       aliases: {alias_str}")
            total_aliases += len(s["safe_aliases"])
        print()

    print(f"Total: {len(new_skills)} skills, {total_aliases} new aliases")

    if not args.apply:
        print(f"\n(Dry run — pass --apply to insert {len(new_skills)} skills + {total_aliases} aliases)")
        conn.close()
        return

    if not new_skills:
        print("\nNothing to insert.")
        conn.close()
        return

    print(f"\nInserting {len(new_skills)} skills + aliases...")
    inserted_skills = 0
    inserted_aliases = 0
    skipped_aliases = 0

    with conn.cursor() as cur:
        for s in new_skills:
            # Insert skill (skip if skill_id already exists — idempotent)
            cur.execute(
                """
                INSERT INTO skills (skill_id, skill_name, vertical, also_in, category, difficulty_relevant)
                VALUES (%s, %s, %s, %s, %s, true)
                ON CONFLICT (skill_id) DO UPDATE
                    SET vertical  = EXCLUDED.vertical,
                        also_in   = EXCLUDED.also_in,
                        category  = EXCLUDED.category
                """,
                (s["skill_id"], s["skill_name"], s["vertical"],
                 s["also_in"] if s["also_in"] else None,
                 s["category"] if s["category"] else None),
            )
            inserted_skills += cur.rowcount

            # Insert canonical name as first alias
            canonical_alias = s["skill_name"].lower()
            cur.execute(
                """
                INSERT INTO skill_aliases (alias_text, skill_id, note)
                VALUES (%s, %s, 'canonical')
                ON CONFLICT (alias_text) DO NOTHING
                """,
                (canonical_alias, s["skill_id"]),
            )
            if cur.rowcount:
                inserted_aliases += 1
            else:
                skipped_aliases += 1

            # Insert additional safe aliases
            for alias in s["safe_aliases"]:
                cur.execute(
                    """
                    INSERT INTO skill_aliases (alias_text, skill_id, note)
                    VALUES (%s, %s, 'taxonomy')
                    ON CONFLICT (alias_text) DO NOTHING
                    """,
                    (alias.lower(), s["skill_id"]),
                )
                if cur.rowcount:
                    inserted_aliases += 1
                else:
                    skipped_aliases += 1

    conn.commit()
    print(f"Done.")
    print(f"  Skills upserted:  {inserted_skills}")
    print(f"  Aliases inserted: {inserted_aliases}")
    print(f"  Aliases skipped (already existed): {skipped_aliases}")
    conn.close()


if __name__ == "__main__":
    main()
