#!/usr/bin/env python3
import os
import argparse
import psycopg2
from typing import Optional
from psycopg2.extras import DictCursor

BAD_EXACT = {
  "analysis","insights","accuracy","technology","example",
  "deadlines","diplomacy","ordinances","techcrunch","concisely",
  "analysis in a clear","analysis to provide strategic",
  "and/or with data modeling","associated analysis",
}

BAD_CONTAINS = (
  "analysis to ",
  "analysis in a ",
  "and/or with ",
  "associated analysis",
)

def main(apply: bool, limit: Optional[int]):
  conn = psycopg2.connect(
    host=os.getenv("PGHOST", "localhost"),
    port=int(os.getenv("PGPORT", "5432")),
    dbname=os.getenv("PGDATABASE", "job_analytics"),
    user=os.getenv("PGUSER"),
    password=os.getenv("PGPASSWORD"),
  )
  try:
    with conn.cursor(cursor_factory=DictCursor) as cur:
      cur.execute("""
        SELECT normalized_text, COUNT(*) AS n, ROUND(AVG(confidence)::numeric, 3) AS avg_conf,
               MIN(sample_job_id) AS example_job_id
        FROM skill_candidates
        GROUP BY normalized_text
        ORDER BY n DESC, avg_conf DESC
        LIMIT 50
      """)
      print("\nTop 50 candidates:")
      for r in cur.fetchall():
        print(f"{r['normalized_text']:<40}  n={r['n']:<4} avg={r['avg_conf']:<5} ex={r['example_job_id']}")

      # build deletion set
      cur.execute("SELECT DISTINCT normalized_text FROM skill_candidates")
      all_norms = [row["normalized_text"] for row in cur.fetchall()]

      to_delete = []
      for t in all_norms:
        if t in BAD_EXACT:
          to_delete.append(t)
          continue
        if any(x in t for x in BAD_CONTAINS):
          to_delete.append(t)

      to_delete = sorted(set(to_delete))
      if limit is not None:
        to_delete = to_delete[:limit]

      print(f"\nPlanned deletes: {len(to_delete)}")
      for t in to_delete[:100]:
        print(" -", t)
      if len(to_delete) > 100:
        print(" ... (truncated list)")

      if not apply:
        print("\nDry-run only. Re-run with --apply to delete.")
        return

      cur.execute("DELETE FROM skill_candidates WHERE normalized_text = ANY(%s)", (to_delete,))
      print(f"\nDeleted rows: {cur.rowcount}")
      conn.commit()
  finally:
    conn.close()

if __name__ == "__main__":
  ap = argparse.ArgumentParser()
  ap.add_argument("--apply", action="store_true", help="Actually delete rows")
  ap.add_argument("--limit", type=int, default=None, help="Delete only first N candidate texts (safety)")
  args = ap.parse_args()
  main(apply=args.apply, limit=args.limit)
