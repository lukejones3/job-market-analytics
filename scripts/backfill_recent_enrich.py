import os, sys, re
from decimal import Decimal
from typing import Dict, Tuple
import psycopg2
from psycopg2.extras import DictCursor

# Make repo root importable (so "python.enrich_job_postings" works)
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from python.enrich_job_postings import (
    extract_title_company_location_from_description,
    infer_experience_level,
    parse_workplace_type,
    parse_salary_range,
    load_skill_alias_patterns,
    extract_skill_priorities,
    upsert_company,
    upsert_role,
    upsert_location,
)

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def next_job_skills_id(cur) -> str:
    cur.execute("""
      SELECT job_skills_id
      FROM job_skills
      WHERE job_skills_id LIKE 'JS%'
      ORDER BY job_skills_id DESC
      LIMIT 1
    """)
    row = cur.fetchone()
    if not row:
        n = 1
    else:
        last = row["job_skills_id"] if isinstance(row, dict) else row[0]
        n = int(re.sub(r"\D", "", last)) + 1
    return f"JS{n:04d}"

def insert_missing_job_skills(cur, job_id: str, skill_priority_map: Dict[str, str]) -> Tuple[int,int]:
    """Return (inserted, upgraded_priority). Avoid dupes by checking existing (job_id, skill_id)."""
    if not skill_priority_map:
        return 0, 0

    priority_rank = {"required": 3, "preferred": 2, "nice-to-have": 1}

    cur.execute("SELECT skill_id, skill_priority FROM job_skills WHERE job_id=%s", (job_id,))
    existing_rows = cur.fetchall()

    existing: Dict[str, str] = {}
    for r in existing_rows:
        sid = r["skill_id"] if isinstance(r, dict) else r[0]
        pri = r["skill_priority"] if isinstance(r, dict) else r[1]
        existing[sid] = pri or "required"

    inserted = 0
    upgraded = 0

    for sid, pri in skill_priority_map.items():
        pri = pri or "required"
        if sid not in existing:
            jsid = next_job_skills_id(cur)
            cur.execute(
                "INSERT INTO job_skills (job_skills_id, job_id, skill_id, skill_priority) VALUES (%s,%s,%s,%s)",
                (jsid, job_id, sid, pri),
            )
            inserted += 1
        else:
            old = existing[sid] or "required"
            if priority_rank.get(pri, 0) > priority_rank.get(old, 0):
                cur.execute(
                    "UPDATE job_skills SET skill_priority=%s WHERE job_id=%s AND skill_id=%s",
                    (pri, job_id, sid),
                )
                upgraded += 1

    return inserted, upgraded

def main(limit: int = 200, apply: bool = False):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    patterns = load_skill_alias_patterns(cur)

    cur.execute(
        """
        SELECT
          jp.job_id,
          jp.description_text,
          jp.company_id, jp.role_id, jp.location_id,
          jp.workplace_type, jp.salary_min, jp.salary_max, jp.salary_period,
          jp.experience_level,
          EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id) AS has_skills
        FROM job_postings jp
        ORDER BY jp.ingested_at DESC
        LIMIT %s
        """,
        (limit,),
    )
    jobs = cur.fetchall()

    touched = 0
    planned_updates = 0
    planned_ins = 0
    planned_upd = 0

    for job in jobs:
        job_id = job["job_id"]
        desc = (job["description_text"] or "").strip()
        if not desc:
            continue

        ex = extract_title_company_location_from_description(desc)
        title_hint = ex.get("title")

        smin, smax, period = parse_salary_range(desc)
        workplace = parse_workplace_type(desc)
        exp_level = infer_experience_level(desc, title_hint=title_hint)

        company_id = job["company_id"]
        role_id = job["role_id"]
        location_id = job["location_id"]

        if (not company_id) and ex.get("company"):
            company_id = upsert_company(cur, ex["company"])
        if (not role_id) and ex.get("title"):
            role_id = upsert_role(cur, ex["title"])
        if (not location_id) and ex.get("location"):
            location_id = upsert_location(cur, ex["location"], ex.get("state"))

        fields = []
        params = []

        if company_id and company_id != job["company_id"]:
            fields.append("company_id=%s"); params.append(company_id)
        if role_id and role_id != job["role_id"]:
            fields.append("role_id=%s"); params.append(role_id)
        if location_id and location_id != job["location_id"]:
            fields.append("location_id=%s"); params.append(location_id)

        if workplace and workplace != job["workplace_type"]:
            fields.append("workplace_type=%s"); params.append(workplace)

        if smin is not None and smin != job["salary_min"]:
            fields.append("salary_min=%s"); params.append(smin)
        if smax is not None and smax != job["salary_max"]:
            fields.append("salary_max=%s"); params.append(smax)
        if period is not None and period != job["salary_period"]:
            fields.append("salary_period=%s"); params.append(period)

        if exp_level is not None and exp_level != job["experience_level"]:
            fields.append("experience_level=%s"); params.append(exp_level)

        ins = upd = 0
        if not job["has_skills"]:
            skill_map = extract_skill_priorities(desc, patterns)
            ins, upd = insert_missing_job_skills(cur, job_id, skill_map)

        if fields or ins or upd:
            touched += 1
        if fields:
            planned_updates += 1
            if apply:
                params.append(job_id)
                cur.execute(
                    f"UPDATE job_postings SET {', '.join(fields)} WHERE job_id=%s",
                    tuple(params),
                )

        planned_ins += ins
        planned_upd += upd

    print(f"Recent window: {len(jobs)} jobs scanned")
    print(f"Jobs touched (would change something): {touched}")
    print(f"Planned job_postings updates: {planned_updates}")
    print(f"Planned job_skills inserts: {planned_ins}")
    print(f"Planned job_skills priority upgrades: {planned_upd}")

    if apply:
        conn.commit()
        print("✅ Applied changes (COMMIT).")
    else:
        conn.rollback()
        print("Dry-run only (ROLLBACK). Re-run with --apply to write.")

    cur.close()
    conn.close()

if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    main(limit=args.limit, apply=args.apply)
