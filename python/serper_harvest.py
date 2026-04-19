#!/usr/bin/env python3
"""
serper_harvest.py

Uses Serper (Google Search API) to find Greenhouse job board URLs
for target data/ML roles, extracts new board tokens, probes each
company, and inserts into discovered_companies.

Usage:
    python3 python/serper_harvest.py --apply
    python3 python/serper_harvest.py --dry-run
"""

import os
import re
import time
import hashlib
import logging
import argparse
import requests
import psycopg2
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "REMOVED_SERPER_API_KEY")
SERPER_URL     = "https://google.serper.dev/search"
REQUEST_DELAY  = 0.5

DB_CONFIG = dict(
    host=os.getenv("PGHOST", "REMOVED_DB_HOST"),
    port=int(os.getenv("PGPORT", 5432)),
    dbname=os.getenv("PGDATABASE", "job_analytics"),
    user=os.getenv("PGUSER", "lukejones"),
    password=os.getenv("PGPASSWORD", "REMOVED_DB_PASSWORD"),
)

# ── Target role queries ───────────────────────────────────────────────────────
# Each query is designed to surface Greenhouse board pages for specific roles
ROLE_QUERIES = [
    # Core roles
    'site:boards.greenhouse.io "machine learning engineer"',
    'site:boards.greenhouse.io "data scientist"',
    'site:boards.greenhouse.io "analytics engineer"',
    'site:boards.greenhouse.io "data engineer"',
    'site:boards.greenhouse.io "ai engineer"',

    # Senior/Staff/Principal variants
    'site:boards.greenhouse.io "staff machine learning engineer"',
    'site:boards.greenhouse.io "principal data scientist"',
    'site:boards.greenhouse.io "staff data engineer"',
    'site:boards.greenhouse.io "lead data scientist"',
    'site:boards.greenhouse.io "principal data engineer"',

    # Revenue/Ops
    'site:boards.greenhouse.io "revenue operations analyst"',
    'site:boards.greenhouse.io "sales operations analyst"',
    'site:boards.greenhouse.io "marketing operations analyst"',

    # High-value niche roles
    'site:boards.greenhouse.io "quantitative researcher"',
    'site:boards.greenhouse.io "applied scientist"',
    'site:boards.greenhouse.io "research scientist" "machine learning"',
    'site:boards.greenhouse.io "llm engineer"',
    'site:boards.greenhouse.io "mlops engineer"',
    'site:boards.greenhouse.io "data platform engineer"',
    'site:boards.greenhouse.io "analytics engineer" "dbt"',

    # Salary-disclosed (high data quality)
    'site:boards.greenhouse.io "machine learning engineer" "$"',
    'site:boards.greenhouse.io "data scientist" "salary"',
    'site:boards.greenhouse.io "data engineer" "per year"',
    'site:boards.greenhouse.io "analytics engineer" "salary range"',

    # Emerging
    'site:boards.greenhouse.io "ai researcher"',
    'site:boards.greenhouse.io "computer vision engineer"',
    'site:boards.greenhouse.io "data architect"',
    'site:boards.greenhouse.io "bi engineer"',
    'site:boards.greenhouse.io "business intelligence engineer"',
]

TARGET_RE = re.compile(
    r'\b(data analyst|data engineer|analytics engineer|data scientist|'
    r'machine learning|ml engineer|ai engineer|applied scientist|'
    r'research scientist|quantitative researcher|llm engineer|mlops|'
    r'computer vision|data architect|bi engineer|business intelligence engineer|'
    r'revenue operations|sales operations|marketing operations|'
    r'staff data|principal data|lead data|staff machine|principal machine)\b',
    re.IGNORECASE
)

GH_TOKEN_RE = re.compile(
    r'boards\.greenhouse\.io/([a-z0-9_-]+)',
    re.IGNORECASE
)

# ── DB helpers ────────────────────────────────────────────────────────────────
def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def load_known_tokens() -> set:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT board_token FROM discovered_companies WHERE ats_source='greenhouse'")
    known = {r[0] for r in cur.fetchall()}
    cur.close()
    conn.close()
    return known

def insert_company(name: str, token: str, active: int, apply: bool):
    if not apply:
        return
    now = datetime.now(timezone.utc)
    cid = "DC" + hashlib.md5(f"greenhouse|{token}".encode()).hexdigest()[:10]
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO discovered_companies
            (company_id, company_name, ats_source, board_token,
             discovery_source, first_seen_at, last_seen_at, active_roles, total_seen, enabled)
        VALUES (%s,%s,'greenhouse',%s,'serper_harvest',%s,%s,%s,1,true)
        ON CONFLICT (ats_source, board_token) DO NOTHING
    """, (cid, name, token, now, now, active))
    cur.close()
    conn.close()

# ── Serper search ─────────────────────────────────────────────────────────────
def serper_search(query: str, num: int = 10) -> list:
    """Run a Google search via Serper and return organic results."""
    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("organic", [])
        else:
            log.warning(f"Serper HTTP {resp.status_code} for: {query}")
            return []
    except Exception as e:
        log.warning(f"Serper error: {e}")
        return []

# ── Greenhouse probe ──────────────────────────────────────────────────────────
def probe_greenhouse(token: str) -> tuple:
    """Returns (company_name, active_role_count)."""
    try:
        resp = requests.get(
            f"https://api.greenhouse.io/v1/boards/{token}/jobs",
            timeout=8,
            headers={"User-Agent": "JobAnalyticsPipeline/1.0"},
        )
        if resp.status_code != 200:
            return token.replace("-", " ").title(), 0
        data = resp.json()
        jobs = data.get("jobs", [])
        active = sum(1 for j in jobs if TARGET_RE.search(j.get("title", "")))
        # Try to get real company name
        name = token.replace("-", " ").title()
        if jobs:
            dept = jobs[0].get("departments", [])
            # company name isn't in job response but we can infer from metadata
        return name, active
    except Exception:
        return token.replace("-", " ").title(), 0

# ── Main ──────────────────────────────────────────────────────────────────────
def main(apply: bool):
    known_tokens = load_known_tokens()
    log.info(f"Loaded {len(known_tokens)} known Greenhouse tokens")

    # Collect all new tokens from Serper
    new_tokens = {}  # token -> set of queries that found it
    credits_used = 0

    for query in ROLE_QUERIES:
        log.info(f"Searching: {query}")
        results = serper_search(query, num=10)
        credits_used += 1

        for r in results:
            url = r.get("link", "") + " " + r.get("snippet", "")
            for m in GH_TOKEN_RE.finditer(url):
                token = m.group(1).lower()
                if token in {"embed", "js", "css", "api", "v1", "jobs", "index", "boards"}:
                    continue
                if token not in known_tokens:
                    if token not in new_tokens:
                        new_tokens[token] = set()
                    new_tokens[token].add(query[:40])

        time.sleep(REQUEST_DELAY)

    log.info(f"Found {len(new_tokens)} new tokens using {credits_used} Serper credits")

    # Probe each new token
    found_with_roles = 0
    for i, (token, queries) in enumerate(new_tokens.items()):
        name, active = probe_greenhouse(token)
        status = f"✅ {active} roles" if active > 0 else "○  0 roles"
        log.info(f"  [{i+1}/{len(new_tokens)}] {name} ({token}): {status}")

        if apply:
            insert_company(name, token, active, apply=True)
            known_tokens.add(token)

        if active > 0:
            found_with_roles += 1

        time.sleep(REQUEST_DELAY)

    log.info(f"\n{'='*50}")
    log.info(f"Serper credits used: {credits_used}")
    log.info(f"New tokens found: {len(new_tokens)}")
    log.info(f"New companies with target roles: {found_with_roles}")
    if apply:
        log.info(f"✅ All inserted into discovered_companies")
    else:
        log.info(f"Dry run — add --apply to write")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    main(apply=args.apply and not args.dry_run)
