#!/usr/bin/env python3
"""SQL-based skill extraction — replaces Python regex loop with a single SQL INSERT.

Approach: build a temp table of (domain, skill_id, alias_pattern) from the VERTICALS
taxonomy + DB aliases + FALLBACK_ALIASES, then do one SQL INSERT across all target jobs.

Known differences vs Python --skills-only:
  - skill_priority = 'required' for all rows (no section-aware detection)
  - 'AI' skill is excluded (requires bulletish-line context check not feasible in SQL;
    handled by the weekly --only-missing LLM run instead)
  - extraction_src = 'sql_extract'
"""
import argparse
import logging
import os
import re
import sys
import time
from pathlib import Path

import psycopg2
from psycopg2.extras import DictCursor, execute_batch
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent))
import domain_taxonomy
from enrich_job_postings import (
    FALLBACK_ALIASES,
    SKILL_DENYLIST,
    load_skill_aliases_from_db,
    load_existing_skill_ids,
    _canon_key,
    _canon_norm,
    clean_text,
)

log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")


def get_conn():
    return psycopg2.connect(os.environ.get("DATABASE_URL", "dbname=job_analytics"))


def _escape_pg_regex(text: str) -> str:
    """Escape PostgreSQL ERE special chars and collapse spaces to \\s+."""
    # Escape special ERE chars: . ^ $ * + ? { } [ ] ( ) | \
    escaped = re.sub(r'([.^$*+?{}\[\]()|\\])', r'\\\1', text.strip())
    # Allow flexible whitespace between words (same as Python _mk_pat)
    escaped = re.sub(r'\\ ', r'\\s+', escaped)
    return escaped


def _relevant_skills_for_domain(domain: str) -> set:
    """Skills whose primary vertical is `domain` or whose also_in includes it.
    Single source of truth: config/skill_taxonomy.json (via skill_taxonomy loader)."""
    import skill_taxonomy
    return skill_taxonomy.relevant_skill_names_for_domain(domain)


def build_domain_alias_temp_table(cur, canon_to_skill_id: dict, skill_aliases: dict):
    """
    Create _skill_alias_domain (domain, skill_id, alias_pattern) from VERTICALS +
    DB aliases + FALLBACK_ALIASES.  Returns (n_patterns, n_denylist_skipped).
    """
    # Collect denylist skill_ids
    denylist_ids = {
        sid
        for canon, sid in canon_to_skill_id.items()
        if _canon_norm(canon) in SKILL_DENYLIST
    }

    rows: list[tuple] = []  # (domain, skill_id, alias_pattern)

    for domain in domain_taxonomy.domains():
        relevant = _relevant_skills_for_domain(domain)
        for skill_name in sorted(relevant):
            canon = _canon_key(skill_name)
            if not canon:
                continue
            sid = canon_to_skill_id.get(canon)
            if not sid:
                continue
            if sid in denylist_ids:
                continue
            # AI excluded — requires context check not feasible in SQL
            if _canon_norm(canon) == "ai":
                continue

            # Build alias set: skill_name itself + all DB/FALLBACK aliases
            alias_set: list[str] = []
            seen_normed: set[str] = set()

            def _add(a: str) -> None:
                a = clean_text(a)
                n = _canon_norm(a)
                if a and n not in seen_normed:
                    seen_normed.add(n)
                    alias_set.append(a)

            _add(skill_name)
            for a in skill_aliases.get(canon, []):
                _add(a)

            for alias in alias_set:
                pattern = _escape_pg_regex(alias.lower())
                rows.append((domain, sid, pattern))

    # Create / populate temp table
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _skill_alias_domain (
            domain        text NOT NULL,
            skill_id      text NOT NULL,
            alias_pattern text NOT NULL
        ) ON COMMIT DROP
    """)
    cur.execute("TRUNCATE _skill_alias_domain")
    if rows:
        execute_batch(
            cur,
            "INSERT INTO _skill_alias_domain VALUES (%s, %s, %s)",
            rows,
            page_size=1000,
        )
    cur.execute("CREATE INDEX ON _skill_alias_domain(domain)")

    return len(rows), len(denylist_ids)


def run_sql_extract(cur, *, apply: bool, limit=None,
                    job_ids=None, backfill: bool = False, since_hours: int | None = None):
    """
    Run the SQL INSERT.  Returns (rows_inserted_or_planned, n_jobs_targeted).

    Scope:
      - Default (cron mode): jobs with NO existing job_skills rows
      - --backfill: all raw+tier1+domain jobs (adds skills missed by earlier passes)
      - --limit N: restrict to N newest missing-skills jobs (testing)
      - --job-ids ...: restrict to specific job IDs (comparison testing)
    """
    # Build the WHERE predicate for which jobs to target
    extra_where = ""
    params: list = []

    if job_ids:
        extra_where = "AND jp.job_id = ANY(%s)"
        params = [job_ids]
    elif limit is not None:
        # Pick the N newest jobs with no skills at all
        cur.execute("""
            SELECT jp.job_id FROM job_postings jp
            WHERE jp.status = 'raw'
              AND jp.data_tier = 1
              AND jp.domain IS NOT NULL
              AND jp.description_text IS NOT NULL
              AND length(jp.description_text) > 500
              AND NOT EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id)
            ORDER BY jp.ingested_at DESC
            LIMIT %s
        """, (limit,))
        job_ids = [r[0] for r in cur.fetchall()]
        log.info(f"Limiting to {len(job_ids)} newest missing-skills jobs")
        extra_where = "AND jp.job_id = ANY(%s)"
        params = [job_ids]
    elif not backfill:
        # Default cron mode: only jobs with NO skills at all
        extra_where = "AND NOT EXISTS (SELECT 1 FROM job_skills js2 WHERE js2.job_id = jp.job_id)"
        if since_hours is not None:
            extra_where += " AND jp.ingested_at >= now() - (%s * interval '1 hour')"
            params.append(since_hours)

    sql = f"""
        INSERT INTO job_skills
            (job_skills_id, job_id, skill_id, skill_priority, confidence, extraction_src)
        SELECT DISTINCT
            'JS' || LEFT(md5(jp.job_id || '|' || dsa.skill_id), 10),
            jp.job_id,
            dsa.skill_id,
            'required',
            0.9,
            'sql_extract'
        FROM job_postings jp
        JOIN _skill_alias_domain dsa ON dsa.domain = jp.domain
        WHERE jp.status = 'raw'
          AND jp.data_tier = 1
          AND jp.domain IS NOT NULL
          AND jp.description_text IS NOT NULL
          AND length(jp.description_text) > 500
          {extra_where}
          AND NOT EXISTS (
              SELECT 1 FROM job_skills js
              WHERE js.job_id = jp.job_id AND js.skill_id = dsa.skill_id
          )
          AND jp.description_text ~* ('\\m' || dsa.alias_pattern || '\\M')
        ON CONFLICT (job_skills_id) DO NOTHING
    """

    cur.execute(sql, params)
    rows = cur.rowcount
    n_jobs = len(job_ids) if job_ids else None
    return rows, n_jobs


def main():
    ap = argparse.ArgumentParser(description="SQL-based skill extraction (zero LLM)")
    ap.add_argument("--apply", action="store_true", help="Commit changes (default: dry-run / ROLLBACK)")
    ap.add_argument("--limit", type=int, help="Process only N newest jobs missing skills (for testing)")
    ap.add_argument("--backfill", action="store_true",
                    help="Process ALL raw+tier1+domain jobs, not just no-skills jobs")
    ap.add_argument("--since-hours", type=int,
                    help="Cron mode: only scan jobs first ingested within this many hours")
    ap.add_argument("--job-ids", nargs="+", metavar="JOB_ID",
                    help="Restrict to specific job IDs (for comparison testing)")
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)

    t0 = time.time()

    # Load skill metadata (same sources as enrich_job_postings)
    canon_to_skill_id = load_existing_skill_ids(cur)
    skill_aliases = load_skill_aliases_from_db(cur)
    log.info(f"Loaded {len(canon_to_skill_id)} skills, {sum(len(v) for v in skill_aliases.values())} aliases")

    # Build domain→alias temp table
    n_patterns, n_deny = build_domain_alias_temp_table(cur, canon_to_skill_id, skill_aliases)
    log.info(f"Domain-alias table: {n_patterns} patterns across {len(domain_taxonomy.domains())} domains "
             f"({n_deny} denylist skills excluded, AI excluded)")

    # Run extraction
    t1 = time.time()
    rows, n_jobs = run_sql_extract(
        cur,
        apply=args.apply,
        limit=args.limit,
        job_ids=args.job_ids,
        backfill=args.backfill,
        since_hours=args.since_hours,
    )
    elapsed = time.time() - t1

    scope = f"{n_jobs} jobs" if n_jobs else ("all eligible jobs" if args.backfill else "no-skills jobs")
    log.info(f"SQL extract: {elapsed:.2f}s — {rows} skill rows {'inserted' if args.apply else 'planned'} ({scope})")
    log.info(f"Total: {time.time()-t0:.2f}s")

    if args.apply:
        conn.commit()
        log.info("✅ Applied (COMMIT).")
    else:
        conn.rollback()
        log.info("Dry-run (ROLLBACK). Re-run with --apply to write.")


if __name__ == "__main__":
    main()
