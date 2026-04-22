#!/usr/bin/env python3
"""
serper_harvest_v2.py

Expanded query set targeting more role types, company sizes,
and specific tech stacks to find new Greenhouse boards.

Usage:
    python3 python/serper_harvest_v2.py --apply
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

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
if not SERPER_API_KEY:
    raise ValueError("SERPER_API_KEY not set in .env")
SERPER_URL     = "https://google.serper.dev/search"
REQUEST_DELAY  = 0.5

DB_CONFIG = dict(
    host=os.getenv("PGHOST"),
    port=int(os.getenv("PGPORT", 5432)),
    dbname=os.getenv("PGDATABASE", "job_analytics"),
    user=os.getenv("PGUSER", "lukejones"),
    password=os.getenv("PGPASSWORD"),
)

# ── Expanded query set (~100 queries, ~100 credits) ───────────────────────────
ROLE_QUERIES = [
    # ── By tech stack (high signal for data roles) ──
    'site:boards.greenhouse.io "dbt" "data engineer"',
    'site:boards.greenhouse.io "snowflake" "analytics engineer"',
    'site:boards.greenhouse.io "databricks" "data engineer"',
    'site:boards.greenhouse.io "spark" "data engineer"',
    'site:boards.greenhouse.io "airflow" "data engineer"',
    'site:boards.greenhouse.io "kafka" "data engineer"',
    'site:boards.greenhouse.io "pytorch" "machine learning"',
    'site:boards.greenhouse.io "tensorflow" "machine learning engineer"',
    'site:boards.greenhouse.io "kubernetes" "ml engineer"',
    'site:boards.greenhouse.io "looker" "analytics engineer"',
    'site:boards.greenhouse.io "tableau" "data analyst"',
    'site:boards.greenhouse.io "power bi" "data analyst"',
    'site:boards.greenhouse.io "python" "data scientist" "remote"',
    'site:boards.greenhouse.io "sql" "data analyst" "remote"',
    'site:boards.greenhouse.io "r" "statistician" OR "data scientist"',

    # ── By salary disclosure ──
    'site:boards.greenhouse.io "data engineer" "$" "per year"',
    'site:boards.greenhouse.io "data scientist" "salary range"',
    'site:boards.greenhouse.io "machine learning" "compensation range"',
    'site:boards.greenhouse.io "analytics engineer" "base salary"',
    'site:boards.greenhouse.io "data analyst" "pay range"',
    'site:boards.greenhouse.io "data engineer" "annually"',
    'site:boards.greenhouse.io "ml engineer" "total compensation"',

    # ── By company type / sector ──
    'site:boards.greenhouse.io "data scientist" "fintech"',
    'site:boards.greenhouse.io "data engineer" "healthtech" OR "health tech"',
    'site:boards.greenhouse.io "machine learning" "autonomous" OR "self-driving"',
    'site:boards.greenhouse.io "data analyst" "crypto" OR "blockchain" OR "web3"',
    'site:boards.greenhouse.io "data engineer" "climate" OR "cleantech"',
    'site:boards.greenhouse.io "data scientist" "biotech" OR "pharma"',
    'site:boards.greenhouse.io "analytics engineer" "saas"',
    'site:boards.greenhouse.io "data engineer" "marketplace"',
    'site:boards.greenhouse.io "ml engineer" "defense" OR "government"',
    'site:boards.greenhouse.io "data scientist" "gaming"',
    'site:boards.greenhouse.io "data analyst" "ecommerce" OR "e-commerce"',
    'site:boards.greenhouse.io "data engineer" "logistics" OR "supply chain"',
    'site:boards.greenhouse.io "data scientist" "insurance" OR "insurtech"',
    'site:boards.greenhouse.io "data analyst" "media" OR "streaming"',
    'site:boards.greenhouse.io "data engineer" "real estate" OR "proptech"',

    # ── By experience level ──
    'site:boards.greenhouse.io "staff data scientist"',
    'site:boards.greenhouse.io "staff data engineer"',
    'site:boards.greenhouse.io "staff analytics engineer"',
    'site:boards.greenhouse.io "distinguished engineer" "data"',
    'site:boards.greenhouse.io "principal machine learning"',
    'site:boards.greenhouse.io "principal analytics engineer"',
    'site:boards.greenhouse.io "senior staff data"',
    'site:boards.greenhouse.io "director of data"',
    'site:boards.greenhouse.io "head of data"',
    'site:boards.greenhouse.io "vp of data" OR "vp data"',
    'site:boards.greenhouse.io "junior data analyst"',
    'site:boards.greenhouse.io "associate data scientist"',
    'site:boards.greenhouse.io "entry level data analyst"',

    # ── Emerging roles ──
    'site:boards.greenhouse.io "ai safety researcher"',
    'site:boards.greenhouse.io "prompt engineer"',
    'site:boards.greenhouse.io "rag engineer" OR "retrieval augmented"',
    'site:boards.greenhouse.io "fine-tuning engineer" OR "model training"',
    'site:boards.greenhouse.io "data reliability engineer"',
    'site:boards.greenhouse.io "analytics engineer" "metrics"',
    'site:boards.greenhouse.io "decision scientist"',
    'site:boards.greenhouse.io "causal inference"',
    'site:boards.greenhouse.io "experimentation platform"',
    'site:boards.greenhouse.io "growth data scientist"',
    'site:boards.greenhouse.io "product data scientist"',
    'site:boards.greenhouse.io "marketing data scientist"',
    'site:boards.greenhouse.io "risk data scientist"',
    'site:boards.greenhouse.io "fraud data scientist"',
    'site:boards.greenhouse.io "clinical data scientist"',

    # ── By location (remote-friendly companies) ──
    'site:boards.greenhouse.io "data engineer" "remote" "United States"',
    'site:boards.greenhouse.io "data scientist" "remote" "anywhere"',
    'site:boards.greenhouse.io "machine learning" "fully remote"',
    'site:boards.greenhouse.io "analytics engineer" "remote-first"',
    'site:boards.greenhouse.io "data analyst" "remote" "$"',

    # ── By company stage ──
    'site:boards.greenhouse.io "data engineer" "series a" OR "series b"',
    'site:boards.greenhouse.io "data scientist" "series c" OR "series d"',
    'site:boards.greenhouse.io "first data hire" OR "founding data"',
    'site:boards.greenhouse.io "data team" "seed" OR "early stage"',
    'site:boards.greenhouse.io "machine learning" "unicorn" OR "billion"',

    # ── BI/Reporting specific ──
    'site:boards.greenhouse.io "bi analyst" OR "business intelligence analyst"',
    'site:boards.greenhouse.io "bi developer" OR "business intelligence developer"',
    'site:boards.greenhouse.io "reporting analyst"',
    'site:boards.greenhouse.io "insights analyst"',
    'site:boards.greenhouse.io "data visualization engineer"',
    'site:boards.greenhouse.io "dashboard engineer"',
    'site:boards.greenhouse.io "metric engineer"',

    # ── Ops/RevOps specific ──
    'site:boards.greenhouse.io "revenue operations manager"',
    'site:boards.greenhouse.io "sales operations manager" "data"',
    'site:boards.greenhouse.io "marketing operations" "analytics"',
    'site:boards.greenhouse.io "growth analyst"',
    'site:boards.greenhouse.io "pricing analyst"',
    'site:boards.greenhouse.io "supply chain analyst" "data"',
    'site:boards.greenhouse.io "workforce analyst"',
    'site:boards.greenhouse.io "people analytics"',
    'site:boards.greenhouse.io "hr analytics"',
    'site:boards.greenhouse.io "compensation analyst" "data"',

    # ── Specific high-value companies not yet in DB ──
    'site:boards.greenhouse.io "data" site:boards.greenhouse.io/palantir',
    'site:boards.greenhouse.io "data" site:boards.greenhouse.io/twosigma',
    'site:boards.greenhouse.io "data" site:boards.greenhouse.io/citadel',
    'site:boards.greenhouse.io "machine learning" "hedge fund"',
    'site:boards.greenhouse.io "data engineer" "quantitative"',
    'site:boards.greenhouse.io "quant researcher" OR "quantitative analyst"',

    # ── By specific tools/platforms ──
    'site:boards.greenhouse.io "redshift" "data engineer"',
    'site:boards.greenhouse.io "bigquery" "data engineer"',
    'site:boards.greenhouse.io "dbt cloud" "analytics"',
    'site:boards.greenhouse.io "fivetran" "data engineer"',
    'site:boards.greenhouse.io "prefect" OR "dagster" "data engineer"',
    'site:boards.greenhouse.io "great expectations" "data quality"',
    'site:boards.greenhouse.io "mlflow" "machine learning"',
    'site:boards.greenhouse.io "sagemaker" "machine learning"',
    'site:boards.greenhouse.io "vertex ai" "machine learning"',
    'site:boards.greenhouse.io "langchain" OR "llamaindex" "engineer"',
]

TARGET_RE = re.compile(
    r'\b(data analyst|data engineer|analytics engineer|data scientist|'
    r'machine learning|ml engineer|ai engineer|applied scientist|'
    r'research scientist|quantitative researcher|llm engineer|mlops|'
    r'computer vision|data architect|bi engineer|business intelligence|'
    r'revenue operations|sales operations|marketing operations|'
    r'staff data|principal data|lead data|staff machine|principal machine|'
    r'growth analyst|pricing analyst|people analytics|hr analytics|'
    r'insights analyst|reporting analyst|decision scientist|'
    r'causal inference|experimentation|quant researcher|quantitative analyst)\b',
    re.IGNORECASE
)

GH_TOKEN_RE = re.compile(r'boards\.greenhouse\.io/([a-z0-9_-]+)', re.IGNORECASE)
JUNK_TOKENS = {'embed','js','css','api','v1','jobs','index','boards','apply','careers'}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def load_known_tokens() -> set:
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT board_token FROM discovered_companies WHERE ats_source='greenhouse'")
    known = {r[0] for r in cur.fetchall()}
    cur.close(); conn.close()
    return known

def insert_company(name: str, token: str, active: int):
    now = datetime.now(timezone.utc)
    cid = "DC" + hashlib.md5(f"greenhouse|{token}".encode()).hexdigest()[:10]
    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO discovered_companies
            (company_id, company_name, ats_source, board_token,
             discovery_source, first_seen_at, last_seen_at, active_roles, total_seen, enabled)
        VALUES (%s,%s,'greenhouse',%s,'serper_v2',%s,%s,%s,1,true)
        ON CONFLICT (ats_source, board_token) DO NOTHING
    """, (cid, name, token, now, now, active))
    cur.close(); conn.close()

def serper_search(query: str) -> list:
    try:
        resp = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 10},
            timeout=10,
        )
        if resp.status_code == 200:
            return resp.json().get("organic", [])
        log.warning(f"Serper HTTP {resp.status_code}")
        return []
    except Exception as e:
        log.warning(f"Serper error: {e}")
        return []

def probe_greenhouse(token: str) -> tuple:
    try:
        resp = requests.get(
            f"https://api.greenhouse.io/v1/boards/{token}/jobs",
            timeout=8, headers={"User-Agent": "JobAnalyticsPipeline/1.0"}
        )
        if resp.status_code != 200:
            return token.replace("-"," ").title(), 0
        jobs = resp.json().get("jobs", [])
        active = sum(1 for j in jobs if TARGET_RE.search(j.get("title","")))
        return token.replace("-"," ").title(), active
    except Exception:
        return token.replace("-"," ").title(), 0

def main(apply: bool):
    known_tokens = load_known_tokens()
    log.info(f"Known tokens: {len(known_tokens)}")

    new_tokens = {}
    credits_used = 0

    for query in ROLE_QUERIES:
        log.info(f"[{credits_used+1}/{len(ROLE_QUERIES)}] {query[:70]}")
        results = serper_search(query)
        credits_used += 1

        for r in results:
            text = r.get("link","") + " " + r.get("snippet","")
            for m in GH_TOKEN_RE.finditer(text):
                token = m.group(1).lower()
                if token in JUNK_TOKENS or len(token) < 3:
                    continue
                # Filter obvious junk (random strings, numbers only)
                if re.match(r'^[0-9]+$', token):
                    continue
                if token not in known_tokens and token not in new_tokens:
                    new_tokens[token] = query[:50]

        time.sleep(REQUEST_DELAY)

    log.info(f"\nSearch complete — {len(new_tokens)} new tokens found ({credits_used} credits used)")
    log.info("Probing each token...")

    found = 0
    for i, (token, source_query) in enumerate(new_tokens.items()):
        name, active = probe_greenhouse(token)
        status = f"✅ {active} roles" if active > 0 else "○  0 roles"
        log.info(f"  [{i+1}/{len(new_tokens)}] {name} ({token}): {status}")

        if apply and active >= 0:
            insert_company(name, token, active)
            known_tokens.add(token)

        if active > 0:
            found += 1

        time.sleep(REQUEST_DELAY)

    log.info(f"\n{'='*60}")
    log.info(f"Serper credits used:          {credits_used}")
    log.info(f"New tokens discovered:        {len(new_tokens)}")
    log.info(f"New companies with roles:     {found}")
    if apply:
        log.info(f"✅ Written to discovered_companies")
    else:
        log.info(f"Dry run — add --apply to write")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    main(apply=args.apply)
