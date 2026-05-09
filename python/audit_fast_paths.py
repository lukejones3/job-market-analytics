#!/usr/bin/env python3
"""
audit_fast_paths.py

Samples N random raw survivors and shows the classify + salary fast-path
breakdown WITHOUT calling any LLM.

Usage:
    python python/audit_fast_paths.py            # 1000 jobs
    python python/audit_fast_paths.py --n 5000   # custom sample
"""

import argparse
import os
import sys
from pathlib import Path
from collections import Counter

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
sys.path.insert(0, str(Path(__file__).resolve().parent))

from llm_client import (
    _data_title_subcategory,
    _is_federal_staffing,
    is_blocked_aggregator,
)
from enrich_job_postings import (
    get_conn,
    _load_cat_cache,
    parse_salary_range,
    _salary_llm_snippet,
    strip_linkedin_chrome,
)


def run(n: int):
    conn = get_conn()
    cur = conn.cursor(cursor_factory=psycopg2.extras.DictCursor)

    cat_cache = _load_cat_cache(cur)
    print(f"cat_cache: {len(cat_cache):,} entries")

    cur.execute("""
        SELECT
            jp.job_id,
            jp.domain,
            jp.role_category,
            jp.salary_min,
            jp.description_text,
            c.company_name,
            r.role_name
        FROM job_postings jp
        LEFT JOIN companies c  ON c.company_id  = jp.company_id
        LEFT JOIN roles     r  ON r.role_id      = jp.role_id
        WHERE jp.status = 'raw'
        ORDER BY random()
        LIMIT %s
    """, (n,))
    jobs = cur.fetchall()
    cur.close()
    conn.close()

    total = len(jobs)
    print(f"Sampled {total:,} raw survivors\n")

    # ── Classify breakdown ─────────────────────────────────────────────────────
    cls_already_has      = 0   # role_category already set — no work needed
    cls_no_domain        = 0   # domain is NULL — classify can't run
    cls_blocked_agg      = 0   # blocked aggregator
    cls_federal          = 0   # federal staffing fast-path
    cls_data_title       = 0   # _data_title_subcategory fast-path
    cls_cat_cache        = 0   # cat_cache (company+role already classified)
    cls_llm_required     = 0   # residual — needs LLM

    cls_domain_llm: Counter = Counter()   # domain → LLM-required count

    # ── Salary breakdown ───────────────────────────────────────────────────────
    sal_already_has      = 0   # salary_min already in DB
    sal_regex_hit        = 0   # parse_salary_range regex succeeded
    sal_no_keyword       = 0   # no salary keyword in text → LLM wouldn't help
    sal_llm_required     = 0   # has keyword but regex got nothing → LLM needed

    for job in jobs:
        job_id      = job["job_id"]
        domain      = job["domain"]
        existing_cat= job["role_category"]
        existing_sal= job["salary_min"]
        desc        = job["description_text"] or ""
        company     = job["company_name"] or ""
        role        = job["role_name"] or ""

        # ── Classify ──────────────────────────────────────────────────────────
        if existing_cat is not None:
            cls_already_has += 1
        elif domain is None:
            cls_no_domain += 1
        elif is_blocked_aggregator(company):
            cls_blocked_agg += 1
        else:
            company_lower = company.strip().lower()
            role_lower    = role.strip().lower()
            ck = (company_lower, role_lower)

            if domain == "data_ml" and _is_federal_staffing(company):
                cls_federal += 1
            elif domain == "data_ml" and _data_title_subcategory(role):
                cls_data_title += 1
            elif cat_cache.get(ck):
                cls_cat_cache += 1
            else:
                cls_llm_required += 1
                cls_domain_llm[domain or "NULL"] += 1

        # ── Salary ────────────────────────────────────────────────────────────
        if existing_sal is not None:
            sal_already_has += 1
        else:
            smin, smax, sper = parse_salary_range(desc, skip_llm=True)
            if smin is not None:
                sal_regex_hit += 1
            else:
                stripped = strip_linkedin_chrome(desc)
                snippet  = _salary_llm_snippet(stripped)
                if snippet is None:
                    sal_no_keyword += 1
                else:
                    sal_llm_required += 1

    # ── Report ─────────────────────────────────────────────────────────────────
    w = 36
    pct = lambda x: f"{100*x/total:.1f}%"

    print("=" * 55)
    print(f"{'CLASSIFY BREAKDOWN':^55}")
    print("=" * 55)
    print(f"  {'already classified (no work)':.<{w}} {cls_already_has:>6,}  {pct(cls_already_has)}")
    print(f"  {'domain=NULL (unclassifiable)':.<{w}} {cls_no_domain:>6,}  {pct(cls_no_domain)}")
    print(f"  {'blocked aggregator':.<{w}} {cls_blocked_agg:>6,}  {pct(cls_blocked_agg)}")
    print(f"  {'federal staffing fast-path':.<{w}} {cls_federal:>6,}  {pct(cls_federal)}")
    print(f"  {'data-title fast-path':.<{w}} {cls_data_title:>6,}  {pct(cls_data_title)}")
    print(f"  {'cat_cache hit':.<{w}} {cls_cat_cache:>6,}  {pct(cls_cat_cache)}")
    print(f"  {'LLM required':.<{w}} {cls_llm_required:>6,}  {pct(cls_llm_required)}")
    print()
    if cls_llm_required:
        print("  LLM-required by domain:")
        for dom, cnt in cls_domain_llm.most_common():
            print(f"    {dom:<28} {cnt:>6,}  {pct(cnt)}")

    # Extrapolate to full 59K raw survivors
    raw_total = 59495
    rate = cls_llm_required / total
    est_llm = int(rate * raw_total)
    print()
    print(f"  Extrapolated LLM calls for {raw_total:,} survivors: ~{est_llm:,}")

    print()
    print("=" * 55)
    print(f"{'SALARY BREAKDOWN':^55}")
    print("=" * 55)
    print(f"  {'already has salary (DB)':.<{w}} {sal_already_has:>6,}  {pct(sal_already_has)}")
    print(f"  {'regex parse hit':.<{w}} {sal_regex_hit:>6,}  {pct(sal_regex_hit)}")
    print(f"  {'no salary keyword (LLM skipped)':.<{w}} {sal_no_keyword:>6,}  {pct(sal_no_keyword)}")
    print(f"  {'LLM required':.<{w}} {sal_llm_required:>6,}  {pct(sal_llm_required)}")

    est_sal_llm = int((sal_llm_required / total) * raw_total)
    print()
    print(f"  Extrapolated salary LLM calls for {raw_total:,} survivors: ~{est_sal_llm:,}")

    print()
    print("=" * 55)
    total_est = est_llm + est_sal_llm
    print(f"  TOTAL estimated LLM calls: ~{total_est:,}")
    if total_est <= 10_000:
        print(f"  → Within 10K cap. Run as-is.")
    else:
        print(f"  → Exceeds 10K cap by ~{total_est - 10_000:,}.")
        print(f"     Options: raise cap OR add title fast-paths for non-data_ml domains.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--n", type=int, default=1000, help="Sample size (default 1000)")
    args = parser.parse_args()
    run(args.n)
