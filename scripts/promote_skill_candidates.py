#!/usr/bin/env python3
import os
import re
import csv
import argparse
from datetime import datetime
from typing import Optional, List, Tuple, Dict

import psycopg2
from psycopg2.extras import DictCursor


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
        cursor_factory=DictCursor,
    )


def normalize(s: str) -> str:
    s = (s or "").strip().lower()
    s = s.replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s


def next_skill_id(cur) -> str:
    """
    Generates next skills.skill_id.
    Tries to preserve existing numeric width (defaults to 4).
    Assumes IDs look like S0001, S0123, etc.
    """
    cur.execute(
        r"""
        SELECT skill_id
        FROM skills
        WHERE skill_id ~ '^S[0-9]+$'
        ORDER BY length(skill_id) DESC, skill_id DESC
        LIMIT 1
        """
    )
    row = cur.fetchone()
    if not row:
        return "S0001"

    last = row["skill_id"]
    digits = re.sub(r"\D", "", last)
    width = max(4, len(digits))
    n = int(digits) + 1
    return f"S{n:0{width}d}"


def upsert_skill(cur, skill_name: str, skill_group: Optional[str] = None) -> str:
    cur.execute(
        "SELECT skill_id FROM skills WHERE lower(skill_name)=lower(%s) LIMIT 1",
        (skill_name,),
    )
    r = cur.fetchone()
    if r:
        return r["skill_id"]

    sid = next_skill_id(cur)
    cur.execute(
        "INSERT INTO skills (skill_id, skill_name, skill_group) VALUES (%s, %s, %s)",
        (sid, skill_name, skill_group),
    )
    return sid


def insert_alias(cur, alias_text: str, skill_id: str, note: Optional[str] = None) -> bool:
    alias_text = normalize(alias_text)
    if not alias_text:
        return False
    cur.execute(
        """
        INSERT INTO skill_aliases (alias_text, skill_id, note)
        VALUES (%s, %s, %s)
        ON CONFLICT (alias_text) DO NOTHING
        """,
        (alias_text, skill_id, note),
    )
    return cur.rowcount == 1


def update_candidate(cur, normalized_text: str, skill_id: str, new_status: str) -> int:
    cur.execute(
        """
        UPDATE skill_candidates
        SET mapped_skill_id=%s,
            status=%s
        WHERE normalized_text=%s
        """,
        (skill_id, new_status, normalized_text),
    )
    return cur.rowcount


def delete_candidate(cur, normalized_text: str) -> int:
    cur.execute(
        "DELETE FROM skill_candidates WHERE normalized_text=%s",
        (normalized_text,),
    )
    return cur.rowcount


def load_promotions_csv(path: str) -> List[Dict[str, object]]:
    """
    CSV columns:
      normalized_text, skill_name, aliases, skill_group, note

    aliases: pipe-separated list (e.g. "vlookup|vlookups|v-lookups")
    """
    out: List[Dict[str, object]] = []
    with open(path, "r", encoding="utf-8") as f:
        rdr = csv.DictReader(f)
        for row in rdr:
            norm = normalize(row.get("normalized_text", ""))
            skill_name = (row.get("skill_name") or "").strip()
            if not norm or not skill_name:
                continue
            aliases_raw = row.get("aliases") or ""
            aliases = [a.strip() for a in aliases_raw.split("|") if a.strip()]
            skill_group = (row.get("skill_group") or "").strip() or None
            note = (row.get("note") or "").strip() or None
            out.append(
                {
                    "normalized_text": norm,
                    "skill_name": skill_name,
                    "aliases": aliases,
                    "skill_group": skill_group,
                    "note": note,
                }
            )
    return out


def show_top_candidates(cur, limit: int, min_conf: float, min_seen: int, status: str):
    cur.execute(
        """
        SELECT normalized_text,
               seen_count,
               confidence,
               skill_type_guess,
               status,
               sample_job_id
        FROM skill_candidates
        WHERE confidence >= %s
          AND seen_count >= %s
          AND (%s = 'any' OR status = %s)
        ORDER BY confidence DESC, seen_count DESC, last_seen_at DESC
        LIMIT %s
        """,
        (min_conf, min_seen, status, status, limit),
    )
    rows = cur.fetchall()
    print(f"\nTop {len(rows)} candidates (min_conf={min_conf}, min_seen={min_seen}, status={status}):")
    for r in rows:
        print(
            f"- {r['normalized_text']:<40} "
            f"seen={r['seen_count']:<4} conf={float(r['confidence']):.3f} "
            f"type={r['skill_type_guess']:<7} status={r['status']:<10} ex={r['sample_job_id']}"
        )
    print("")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--show", action="store_true", help="Show top candidates")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--min-conf", type=float, default=0.80)
    ap.add_argument("--min-seen", type=int, default=1)
    ap.add_argument("--status", type=str, default="new", help="new|any|promoted|ignored|...")

    ap.add_argument("--map", type=str, default=None, help="CSV mapping file to promote candidates")
    ap.add_argument("--apply", action="store_true", help="Actually write changes")
    ap.add_argument("--delete-candidates", action="store_true", help="Delete promoted candidates instead of marking them")
    ap.add_argument("--promoted-status", type=str, default="promoted", help="Status to set after promotion")

    args = ap.parse_args()

    conn = get_conn()
    try:
        cur = conn.cursor()

        if args.show:
            show_top_candidates(cur, args.limit, args.min_conf, args.min_seen, args.status)

        if not args.map:
            if not args.show:
                print("Nothing to do. Use --show or --map <file.csv> (and optionally --apply).")
            conn.rollback()
            return

        promotions = load_promotions_csv(args.map)
        if not promotions:
            print(f"No valid promotions found in: {args.map}")
            conn.rollback()
            return

        # Validate candidate existence up-front
        norms = [p["normalized_text"] for p in promotions]
        cur.execute(
            "SELECT normalized_text FROM skill_candidates WHERE normalized_text = ANY(%s)",
            (norms,),
        )
        exists = set([r["normalized_text"] for r in cur.fetchall()])
        missing = [n for n in norms if n not in exists]
        if missing:
            print("⚠️ These normalized_text entries were not found in skill_candidates:")
            for m in missing:
                print(f"  - {m}")
            print("Continuing with the ones that exist...\n")

        planned = [p for p in promotions if p["normalized_text"] in exists]
        print(f"Planned promotions: {len(planned)}")

        inserted_skills = 0
        inserted_aliases = 0
        updated_candidates = 0
        deleted_candidates = 0

        for p in planned:
            norm = p["normalized_text"]
            skill_name = p["skill_name"]
            aliases = p["aliases"]
            skill_group = p["skill_group"]
            note = p["note"] or f"promoted_from_candidate:{norm}"

            # Ensure the candidate text itself becomes an alias too (always)
            alias_set = []
            alias_set.append(norm)
            for a in aliases:
                alias_set.append(a)
            # de-dupe normalized
            alias_set = list(dict.fromkeys([normalize(a) for a in alias_set if normalize(a)]))

            if not args.apply:
                print(f"[DRY] promote '{norm}' -> skill '{skill_name}' aliases={alias_set}")
                continue

            sid_before = None
            cur.execute("SELECT skill_id FROM skills WHERE lower(skill_name)=lower(%s) LIMIT 1", (skill_name,))
            rr = cur.fetchone()
            sid_before = rr["skill_id"] if rr else None

            sid = upsert_skill(cur, skill_name=skill_name, skill_group=skill_group)
            if not sid_before:
                inserted_skills += 1

            for a in alias_set:
                if insert_alias(cur, a, sid, note=note):
                    inserted_aliases += 1

            if args.delete_candidates:
                deleted_candidates += delete_candidate(cur, norm)
            else:
                updated_candidates += update_candidate(cur, norm, sid, args.promoted_status)

        if not args.apply:
            print("\nDry-run only. Re-run with --apply to write.\n")
            conn.rollback()
            return

        conn.commit()
        print("\n✅ Done.")
        print(f"Inserted skills: {inserted_skills}")
        print(f"Inserted aliases: {inserted_aliases}")
        print(f"Updated candidates: {updated_candidates}")
        print(f"Deleted candidates: {deleted_candidates}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
