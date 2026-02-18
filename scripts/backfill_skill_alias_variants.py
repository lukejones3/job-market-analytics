#!/usr/bin/env python3
import re
import os
import psycopg2
from psycopg2.extras import DictCursor

DB = os.getenv("PGDATABASE", "job_analytics")

def variants(a: str):
    a = (a or "").strip().lower()
    out = set()
    if not a:
        return out
    out.add(a)
    out.add(re.sub(r"\s+", " ", a).strip())
    out.add(re.sub(r"[^a-z0-9]+", " ", a).strip())
    out.add(re.sub(r"[^a-z0-9]+", "", a))
    out.add(a.replace("microsoft ", "ms "))
    out.add(a.replace("ms ", "microsoft "))
    out.add(a.replace(".", ""))
    # avoid garbage
    out2 = set()
    for v in out:
        v = v.strip()
        if not v:
            continue
        if len(v) == 1 and v not in {"r","c"}:
            continue
        out2.add(v)
    return out2

def main(dry_run=True, limit=None):
    conn = psycopg2.connect(dbname=DB)
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=DictCursor) as cur:
            cur.execute("""
              SELECT skill_id, alias_text
              FROM skill_aliases
              WHERE alias_text IS NOT NULL AND btrim(alias_text) <> ''
            """)
            rows = cur.fetchall()

            to_add = []
            for r in rows:
                sid = r["skill_id"]
                a = r["alias_text"]
                for v in variants(a):
                    if v == a.strip().lower():
                        continue
                    to_add.append((sid, v))

            # de-dup
            to_add = list(dict.fromkeys(to_add))
            if limit:
                to_add = to_add[:limit]

            if not to_add:
                print("No variants to add.")
                return

            # filter out existing
            cur.execute("""
              SELECT lower(alias_text) AS a, skill_id
              FROM skill_aliases
            """)
            existing = {(rr["skill_id"], rr["a"]) for rr in cur.fetchall()}

            final = [(sid, a) for (sid, a) in to_add if (sid, a) not in existing]

            print(f"Planned inserts: {len(final)} (out of {len(to_add)} candidates)")

            if dry_run:
                print("Dry-run: not inserting. Re-run with --apply to write.")
                return

            for sid, a in final:
                cur.execute("""
                  INSERT INTO skill_aliases (skill_id, alias_text)
                  VALUES (%s, %s)
                  ON CONFLICT DO NOTHING
                """, (sid, a))

            conn.commit()
            print(f"Inserted: {len(final)}")
    finally:
        conn.close()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Actually insert rows")
    ap.add_argument("--limit", type=int, default=None)
    args = ap.parse_args()
    main(dry_run=(not args.apply), limit=args.limit)
