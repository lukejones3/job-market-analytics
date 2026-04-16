#!/usr/bin/env python3
"""
discover_serper.py

Uses Serper.dev API to Google-dork ATS platforms for new companies
posting data/analytics/ML roles. Extracts company board tokens from
URLs and probes each new company's full board before inserting into
discovered_companies.

Usage:
    python python/discover_serper.py --apply --limit 100
    python python/discover_serper.py --apply --source greenhouse
    python python/discover_serper.py --apply --source lever
    python python/discover_serper.py --apply --source ashby
    python python/discover_serper.py --dry-run

Cost: ~1 Serper credit per query. 100 queries = ~1,000 URLs = many new companies.
"""

import os
import re
import time
import logging
import argparse
import hashlib
from pathlib import Path
from typing import Optional
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
REQUEST_DELAY  = 0.5
REQUEST_TIMEOUT = 8

# ── Search queries per ATS ──────────────────────────────────────────────────

QUERIES = {
    "greenhouse": [
        # Salary/comp signals
        'site:boards.greenhouse.io "data" "$" salary',
        'site:boards.greenhouse.io "data" "equity" "401k"',
        # Stack signals
        'site:boards.greenhouse.io "data" "dbt" "snowflake"',
        'site:boards.greenhouse.io "data" "spark" "python"',
        'site:boards.greenhouse.io "data" "airflow" "kubernetes"',
        'site:boards.greenhouse.io "data" "pytorch" "tensorflow"',
        'site:boards.greenhouse.io "data" "databricks"',
        'site:boards.greenhouse.io "data" "looker" "tableau"',
        # Industry signals
        'site:boards.greenhouse.io "data" "fintech"',
        'site:boards.greenhouse.io "data" "healthtech"',
        'site:boards.greenhouse.io "data" "series b" OR "series c"',
        'site:boards.greenhouse.io "data" "YC" OR "y combinator"',
        'site:boards.greenhouse.io "data" "remote" "senior"',
        # Role variants not in first run
        'site:boards.greenhouse.io "staff data"',
        'site:boards.greenhouse.io "principal data"',
        'site:boards.greenhouse.io "data science manager"',
        'site:boards.greenhouse.io "head of data"',
        'site:boards.greenhouse.io "data engineering manager"',
        'site:boards.greenhouse.io "decision scientist"',
        'site:boards.greenhouse.io "causal inference"',
        'site:job-boards.greenhouse.io "dbt" "snowflake"',
        'site:job-boards.greenhouse.io "pytorch" "tensorflow"',
        'site:job-boards.greenhouse.io "staff data"',
        'site:job-boards.greenhouse.io "principal data"',
        'site:job-boards.greenhouse.io "head of data"',
    ],
    "lever": [
        'site:jobs.lever.co "dbt" "snowflake"',
        'site:jobs.lever.co "pytorch" "tensorflow"',
        'site:jobs.lever.co "databricks"',
        'site:jobs.lever.co "staff data"',
        'site:jobs.lever.co "principal data"',
        'site:jobs.lever.co "head of data"',
        'site:jobs.lever.co "data science manager"',
        'site:jobs.lever.co "causal inference"',
        'site:jobs.lever.co "series b" OR "series c"',
        'site:jobs.lever.co "remote" "senior" "data"',
    ],
    # ashby excluded — Google index doesn't match Ashby API slugs reliably
    # Use discover_companies.py --source ashby instead
    # workday excluded — Google returns instance IDs not company subdomains
    # Workday companies are manually curated in ingest_jobs.py
}

# ── URL parsing ─────────────────────────────────────────────────────────────

def extract_greenhouse_token(url: str) -> Optional[str]:
    """Extract board token from Greenhouse URLs."""
    patterns = [
        r"boards\.greenhouse\.io/([^/?#\s]+)",
        r"job-boards\.greenhouse\.io/([^/?#\s]+)",
    ]
    for p in patterns:
        m = re.search(p, url, re.IGNORECASE)
        if m:
            token = m.group(1).lower().strip("/")
            if token and len(token) > 1 and token not in ("embed", "jobs", "api"):
                return token
    return None

def extract_lever_token(url: str) -> Optional[str]:
    """Extract company slug from Lever URLs."""
    m = re.search(r"jobs\.lever\.co/([^/?#\s]+)", url, re.IGNORECASE)
    if m:
        token = m.group(1).lower().strip("/")
        if token and len(token) > 1:
            return token
    return None

def extract_ashby_token(url: str) -> Optional[str]:
    """Extract company slug from Ashby URLs."""
    m = re.search(r"jobs\.ashbyhq\.com/([^/?#\s]+)", url, re.IGNORECASE)
    if m:
        token = m.group(1).lower().strip("/")
        if token and len(token) > 1:
            return token
    return None

def extract_workday_token(url: str) -> Optional[str]:
    """Extract subdomain from Workday URLs like company.myworkdayjobs.com"""
    m = re.search(r"([a-z0-9\-]+)\.myworkdayjobs\.com", url, re.IGNORECASE)
    if m:
        token = m.group(1).lower()
        if token and len(token) > 1 and token not in ("www", "apply", "wd1", "wd3", "wd5"):
            return token
    return None

def extract_token(url: str, source: str) -> Optional[str]:
    if source == "greenhouse":
        return extract_greenhouse_token(url)
    elif source == "lever":
        return extract_lever_token(url)
    elif source == "ashby":
        return extract_ashby_token(url)
    elif source == "workday":
        return extract_workday_token(url)
    return None

# ── Serper search ───────────────────────────────────────────────────────────

def serper_search(query: str, num: int = 10) -> list[str]:
    """Run a Serper.dev search and return URLs."""
    if not SERPER_API_KEY:
        raise ValueError("SERPER_API_KEY not set in .env")
    
    resp = requests.post(
        "https://google.serper.dev/search",
        headers={
            "X-API-KEY": SERPER_API_KEY,
            "Content-Type": "application/json",
        },
        json={"q": query, "num": num, "gl": "us", "hl": "en"},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    
    urls = []
    for result in data.get("organic", []):
        if link := result.get("link"):
            urls.append(link)
    return urls

# ── ATS probing ─────────────────────────────────────────────────────────────

TARGET_ROLE_RE = re.compile(
    r"data\s+(analyst|scientist|engineer|architect|steward|governance|quality|platform|ops|operations)|"
    r"analytics\s+engineer|machine\s+learning|ml\s+engineer|ai\s+engineer|"
    r"applied\s+scientist|research\s+scientist|business\s+intelligence|"
    r"revenue\s+operations|revops|marketing\s+ops|sales\s+ops|"
    r"quantitative\s+(analyst|researcher|scientist)|"
    r"llm\s+engineer|data\s+infrastructure|bi\s+(analyst|engineer|developer)",
    re.IGNORECASE,
)

def probe_greenhouse(token: str) -> tuple[bool, int, str]:
    """Returns (has_target_roles, role_count, company_name)."""
    try:
        url = f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs"
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return False, 0, ""
        jobs = r.json().get("jobs", [])
        target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("title", ""))]
        # Try to get company name
        name = ""
        try:
            meta = requests.get(
                f"https://boards-api.greenhouse.io/v1/boards/{token}",
                timeout=REQUEST_TIMEOUT
            )
            if meta.status_code == 200:
                name = meta.json().get("name", "")
        except Exception:
            pass
        return len(target) > 0, len(target), name
    except Exception:
        return False, 0, ""

def probe_lever(token: str) -> tuple[bool, int, str]:
    try:
        url = f"https://api.lever.co/v0/postings/{token}?mode=json&limit=250"
        r = requests.get(url, timeout=REQUEST_TIMEOUT)
        if r.status_code != 200:
            return False, 0, ""
        jobs = r.json() if isinstance(r.json(), list) else []
        target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("text", ""))]
        name = token.replace("-", " ").title()
        return len(target) > 0, len(target), name
    except Exception:
        return False, 0, ""

def probe_ashby(token: str) -> tuple[bool, int, str]:
    try:
        url = "https://api.ashbyhq.com/posting-api/job-board"
        r = requests.post(
            url,
            json={"organizationHostedJobsPageName": token},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code != 200:
            return False, 0, ""
        data = r.json()
        jobs = data.get("jobPostings", [])
        target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("title", ""))]
        name = data.get("organization", {}).get("name", token.replace("-", " ").title())
        return len(target) > 0, len(target), name
    except Exception:
        return False, 0, ""

def probe_workday(subdomain: str) -> tuple[bool, int, str]:
    """Probe Workday by trying common tenant patterns."""
    # Workday tenants follow pattern: subdomain.myworkdayjobs.com/tenant/jobs
    # Try to find jobs via the CXS search API
    tenant_candidates = [
        f"{subdomain}/jobs",
        f"{subdomain}External",
        f"{subdomain}_External",
        f"External_{subdomain}",
        f"{subdomain}Careers",
        f"{subdomain}_Career",
    ]
    
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    
    for tenant in tenant_candidates:
        try:
            url = f"https://{subdomain}.myworkdayjobs.com/wday/cxs/{subdomain}/{tenant}/jobs"
            r = requests.post(
                url,
                json={"limit": 20, "offset": 0, "searchText": "data"},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                jobs = data.get("jobPostings", [])
                target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("title", ""))]
                name = subdomain.replace("-", " ").title()
                return len(target) > 0, len(target), name
        except Exception:
            continue
    return False, 0, ""

def probe(source: str, token: str) -> tuple[bool, int, str]:
    if source == "greenhouse":
        return probe_greenhouse(token)
    elif source == "lever":
        return probe_lever(token)
    elif source == "ashby":
        return probe_ashby(token)
    elif source == "workday":
        return probe_workday(token)
    return False, 0, ""

# ── DB helpers ──────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def load_existing_tokens(cur, source: str) -> set[str]:
    cur.execute(
        "SELECT board_token FROM discovered_companies WHERE ats_source = %s",
        (source,)
    )
    return {r["board_token"] for r in cur.fetchall()}

def insert_company(cur, source: str, token: str, name: str, 
                   active_roles: int, apply: bool) -> bool:
    company_id = "C" + hashlib.md5(f"{source}:{token}".encode()).hexdigest()[:9]
    if apply:
        cur.execute("""
            INSERT INTO discovered_companies 
                (company_id, company_name, ats_source, board_token, 
                 active_roles, total_seen, discovery_source)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ats_source, board_token) DO UPDATE SET
                last_seen_at = now(),
                active_roles = EXCLUDED.active_roles,
                last_had_roles = CASE 
                    WHEN EXCLUDED.active_roles > 0 THEN now() 
                    ELSE discovered_companies.last_had_roles 
                END
        """, (company_id, name or token, source, token, 
              active_roles, active_roles, "serper_dork"))
    return True

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", choices=["greenhouse", "lever", "all"],
                    default="all")
    ap.add_argument("--limit", type=int, default=500,
                    help="Max Serper queries to run")
    args = ap.parse_args()

    apply = args.apply and not args.dry_run
    sources = ["greenhouse", "lever"] if args.source == "all" else [args.source]

    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    total_queries = 0
    total_new = 0
    total_with_roles = 0

    for source in sources:
        log.info(f"=== {source.upper()} discovery ===")
        existing = load_existing_tokens(cur, source)
        log.info(f"  Existing {source} companies: {len(existing)}")

        queries = QUERIES.get(source, [])
        seen_tokens = set()

        for query in queries:
            if total_queries >= args.limit:
                log.info("Query limit reached")
                break

            log.info(f"  Searching: {query}")
            try:
                urls = serper_search(query, num=10)
                total_queries += 1
            except Exception as e:
                log.warning(f"  Search failed: {e}")
                time.sleep(2)
                continue

            for url in urls:
                token = extract_token(url, source)
                if not token:
                    continue
                if token in existing or token in seen_tokens:
                    continue
                seen_tokens.add(token)

                log.info(f"  Probing new {source} company: {token}")
                time.sleep(REQUEST_DELAY)

                has_roles, role_count, name = probe(source, token)

                if has_roles:
                    log.info(f"  ✅ {name or token} — {role_count} target roles")
                    insert_company(cur, source, token, name, role_count, apply)
                    if apply:
                        conn.commit()
                    total_new += 1
                    total_with_roles += 1
                else:
                    log.info(f"  ⚪ {token} — no target roles")
                    # Still insert so we don't re-probe
                    insert_company(cur, source, token, name or token, 0, apply)
                    if apply:
                        conn.commit()
                    total_new += 1

                time.sleep(REQUEST_DELAY)

    log.info(f"\n=== SUMMARY ===")
    log.info(f"Serper queries used: {total_queries}")
    log.info(f"New companies found: {total_new}")
    log.info(f"With target roles:   {total_with_roles}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
