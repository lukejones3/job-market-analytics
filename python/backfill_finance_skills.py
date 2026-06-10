#!/usr/bin/env python3
"""
One-off: re-extract ALL finance domain skills (including new CPA/CFA/CFP/CMA credentials)
from job descriptions. No LLM, no role_category changes — pure regex alias matching.

Usage:
    python3 python/backfill_finance_skills.py          # dry-run
    python3 python/backfill_finance_skills.py --apply  # write to DB
"""
import os, re, hashlib, argparse
import psycopg2
from psycopg2.extras import DictCursor
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")



def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=int(os.getenv("PGPORT", 5432)),
        dbname=os.getenv("PGDATABASE"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def _md5_id(prefix: str, key: str) -> str:
    return prefix + hashlib.md5(key.lower().encode()).hexdigest()[:10]


def _matches(alias: str, desc_lower: str, desc_words: set) -> bool:
    if alias.isalpha() and ' ' not in alias:
        return alias in desc_words
    return alias in desc_lower


def build_finance_alias_map() -> dict[str, str]:
    """Returns {alias_lower: canonical_skill_name} for all finance skills."""
    import skill_taxonomy
    result = {}
    for canon, meta in skill_taxonomy.skills_by_vertical().get("finance", {}).items():
        for alias in meta["aliases"]:
            result[alias.lower()] = canon
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)

    import skill_taxonomy
    alias_map = build_finance_alias_map()
    print(f"Finance skills: {len(skill_taxonomy.skills_by_vertical().get('finance', {}))} canonical, {len(alias_map)} aliases")

    # Fetch all finance tier-1 raw jobs + their existing skill ids to avoid dupes
    cur.execute("""
        SELECT jp.job_id, jp.description_text,
               ARRAY(SELECT js.skill_id FROM job_skills js WHERE js.job_id = jp.job_id) AS existing_skill_ids
        FROM job_postings jp
        WHERE jp.domain = 'finance'
          AND jp.data_tier = 1
          AND jp.status = 'raw'
          AND jp.description_text IS NOT NULL
          AND length(jp.description_text) > 200
    """)
    jobs = cur.fetchall()
    print(f"Finance jobs to scan: {len(jobs)}")

    # Ensure skill rows exist in skills table
    cur.execute("SELECT skill_id, skill_name FROM skills")
    skill_id_map = {row["skill_name"]: row["skill_id"] for row in cur.fetchall()}

    # Upsert any missing canonical skill rows
    for canon in skill_taxonomy.skills_by_vertical().get("finance", {}):
        if canon not in skill_id_map:
            sid = _md5_id("S", canon)
            cur.execute(
                "INSERT INTO skills (skill_id, skill_name) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (sid, canon),
            )
            skill_id_map[canon] = sid

    inserts = 0
    jobs_with_new_skill = set()
    credential_hits: dict[str, int] = {"CPA": 0, "CFA": 0, "CFP": 0, "CMA": 0}

    for job in jobs:
        job_id = job["job_id"]
        desc = (job["description_text"] or "").lower()
        desc_words = set(re.split(r'\W+', desc))
        desc_words.discard('')
        existing = set(job["existing_skill_ids"] or [])

        matched_canons: set[str] = set()
        for alias, canon in alias_map.items():
            if _matches(alias, desc, desc_words):
                matched_canons.add(canon)

        for i, canon in enumerate(sorted(matched_canons)):
            sid = skill_id_map[canon]
            if sid in existing:
                continue
            jss_id = _md5_id("JSS", f"{job_id}{sid}")
            if args.apply:
                cur.execute("""
                    INSERT INTO job_skills (job_skills_id, job_id, skill_id, skill_priority, confidence, extraction_src)
                    VALUES (%s, %s, %s, %s, %s, 'regex')
                    ON CONFLICT DO NOTHING
                """, (jss_id, job_id, sid, 'required', 0.9))
            inserts += 1
            jobs_with_new_skill.add(job_id)
            if canon in credential_hits:
                credential_hits[canon] += 1

    # Restore role_classified_at for any finance jobs still NULL (safety net for daily cron)
    if args.apply:
        cur.execute("""
            UPDATE job_postings
            SET role_classified_at = NOW()
            WHERE domain = 'finance'
              AND data_tier = 1
              AND status = 'raw'
              AND role_classified_at IS NULL
        """)
        restored = cur.rowcount
        conn.commit()
        print(f"\nRestored role_classified_at for {restored} finance jobs with NULL timestamp")
    else:
        conn.rollback()

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"\n=== {mode} RESULTS ===")
    print(f"  Jobs scanned:            {len(jobs)}")
    print(f"  Jobs with new skills:    {len(jobs_with_new_skill)}")
    print(f"  Total skill rows added:  {inserts}")
    print(f"\n  Credential hits:")
    for cred, count in credential_hits.items():
        print(f"    {cred}: {count} jobs")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
