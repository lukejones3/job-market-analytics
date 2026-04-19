#!/usr/bin/env python3
"""
serper_harvest_v3.py

Big sweep — YC companies, Fortune 500 subsidiaries, international remote,
defense/climate/space tech, and long-tail job titles.

Usage:
    python3 python/serper_harvest_v3.py --apply
"""

import os, re, time, hashlib, logging, argparse, requests, psycopg2
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

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

ROLE_QUERIES = [
    # ── YC Companies ──────────────────────────────────────────────────────────
    'site:boards.greenhouse.io "data" "Y Combinator" OR "YC" OR "yc-backed"',
    'site:boards.greenhouse.io "data engineer" "backed by" "sequoia" OR "a16z" OR "benchmark"',
    'site:boards.greenhouse.io "data scientist" "series b" "remote"',
    'site:boards.greenhouse.io "analytics engineer" "yc" OR "techstars"',
    'site:boards.greenhouse.io "machine learning" "ycombinator"',
    'site:boards.greenhouse.io "data" "500 startups" OR "first round" OR "founders fund"',
    'site:boards.greenhouse.io "ml engineer" "accel" OR "tiger global" OR "coatue"',
    'site:boards.greenhouse.io "data engineer" "insight partners" OR "general catalyst"',
    'site:boards.greenhouse.io "data scientist" "andreessen horowitz"',
    'site:boards.greenhouse.io "analytics" "lightspeed" OR "NEA" OR "GV"',

    # ── Fortune 500 / Large Enterprise divisions ───────────────────────────────
    'site:boards.greenhouse.io "data analyst" "microsoft" OR "google" OR "amazon"',
    'site:boards.greenhouse.io "data engineer" "jpmorgan" OR "goldman sachs" OR "morgan stanley"',
    'site:boards.greenhouse.io "data scientist" "mckinsey" OR "bain" OR "bcg"',
    'site:boards.greenhouse.io "analytics" "deloitte" OR "pwc" OR "kpmg" OR "ey"',
    'site:boards.greenhouse.io "machine learning" "capital one" OR "american express"',
    'site:boards.greenhouse.io "data engineer" "walmart" OR "target" OR "costco"',
    'site:boards.greenhouse.io "data scientist" "uber" OR "lyft" OR "doordash"',
    'site:boards.greenhouse.io "analytics engineer" "twitter" OR "pinterest" OR "reddit"',
    'site:boards.greenhouse.io "data" "johnson johnson" OR "pfizer" OR "moderna"',
    'site:boards.greenhouse.io "data engineer" "boeing" OR "lockheed" OR "raytheon"',

    # ── International companies hiring US remote ───────────────────────────────
    'site:boards.greenhouse.io "data engineer" "remote" "europe" OR "uk" OR "germany"',
    'site:boards.greenhouse.io "data scientist" "remote" "canada" OR "toronto" OR "vancouver"',
    'site:boards.greenhouse.io "machine learning" "remote" "israel" OR "tel aviv"',
    'site:boards.greenhouse.io "analytics engineer" "remote" "australia" OR "sydney"',
    'site:boards.greenhouse.io "data engineer" "remote" "india" OR "bangalore" OR "hyderabad"',
    'site:boards.greenhouse.io "data" "remote" "singapore" OR "hong kong"',
    'site:boards.greenhouse.io "ml engineer" "remote" "latin america" OR "latam" OR "brazil"',
    'site:boards.greenhouse.io "data scientist" "global" "remote-first"',
    'site:boards.greenhouse.io "analytics" "distributed team" "remote"',
    'site:boards.greenhouse.io "data engineer" "async" OR "asynchronous" "remote"',

    # ── Defense / GovTech ─────────────────────────────────────────────────────
    'site:boards.greenhouse.io "data scientist" "defense" OR "national security"',
    'site:boards.greenhouse.io "data engineer" "clearance" OR "secret clearance"',
    'site:boards.greenhouse.io "machine learning" "government" OR "federal"',
    'site:boards.greenhouse.io "data analyst" "dod" OR "department of defense"',
    'site:boards.greenhouse.io "ml engineer" "intelligence" "clearance"',
    'site:boards.greenhouse.io "data" "anduril" OR "palantir" OR "shield ai"',
    'site:boards.greenhouse.io "data engineer" "spacex" OR "blue origin" OR "rocket lab"',
    'site:boards.greenhouse.io "data scientist" "military" OR "army" OR "navy"',
    'site:boards.greenhouse.io "analytics" "cia" OR "nsa" OR "darpa"',
    'site:boards.greenhouse.io "data" "govtech" OR "civic tech"',

    # ── Climate / Clean Tech ──────────────────────────────────────────────────
    'site:boards.greenhouse.io "data engineer" "solar" OR "wind" OR "renewable"',
    'site:boards.greenhouse.io "data scientist" "climate" OR "carbon" OR "sustainability"',
    'site:boards.greenhouse.io "analytics engineer" "electric vehicle" OR "ev" OR "battery"',
    'site:boards.greenhouse.io "machine learning" "energy" "grid" OR "utility"',
    'site:boards.greenhouse.io "data" "cleantech" OR "greentech" OR "net zero"',
    'site:boards.greenhouse.io "data engineer" "tesla" OR "rivian" OR "lucid"',
    'site:boards.greenhouse.io "ml engineer" "climate change" OR "emissions"',
    'site:boards.greenhouse.io "data scientist" "agriculture" OR "agtech" OR "food"',
    'site:boards.greenhouse.io "analytics" "water" OR "ocean" OR "environment"',
    'site:boards.greenhouse.io "data" "nuclear" OR "fusion" OR "hydrogen"',

    # ── Space Tech ────────────────────────────────────────────────────────────
    'site:boards.greenhouse.io "data engineer" "space" OR "satellite" OR "aerospace"',
    'site:boards.greenhouse.io "data scientist" "nasa" OR "spacex" OR "planet labs"',
    'site:boards.greenhouse.io "machine learning" "satellite" OR "imagery" OR "geospatial"',
    'site:boards.greenhouse.io "data analyst" "astronomy" OR "telescope" OR "astrophysics"',
    'site:boards.greenhouse.io "ml engineer" "autonomous" "space" OR "drone"',

    # ── Quantum / Deep Tech ───────────────────────────────────────────────────
    'site:boards.greenhouse.io "data scientist" "quantum" OR "quantum computing"',
    'site:boards.greenhouse.io "machine learning" "semiconductor" OR "chip" OR "hardware"',
    'site:boards.greenhouse.io "data engineer" "robotics" OR "automation"',
    'site:boards.greenhouse.io "ml engineer" "computer vision" "manufacturing"',
    'site:boards.greenhouse.io "data" "synthetic biology" OR "genomics" OR "proteomics"',

    # ── Long-tail job titles ──────────────────────────────────────────────────
    'site:boards.greenhouse.io "data steward"',
    'site:boards.greenhouse.io "analytics lead"',
    'site:boards.greenhouse.io "business systems analyst" "data"',
    'site:boards.greenhouse.io "data governance analyst"',
    'site:boards.greenhouse.io "master data management"',
    'site:boards.greenhouse.io "data catalog" engineer OR analyst',
    'site:boards.greenhouse.io "metadata engineer"',
    'site:boards.greenhouse.io "data mesh" engineer',
    'site:boards.greenhouse.io "feature engineer" OR "feature store"',
    'site:boards.greenhouse.io "model risk analyst"',
    'site:boards.greenhouse.io "credit risk analyst" "data"',
    'site:boards.greenhouse.io "actuarial analyst" "data"',
    'site:boards.greenhouse.io "econometrician" OR "econometrics"',
    'site:boards.greenhouse.io "operations research analyst"',
    'site:boards.greenhouse.io "industrial engineer" "data"',
    'site:boards.greenhouse.io "simulation engineer" "data"',
    'site:boards.greenhouse.io "digital twin" engineer OR analyst',
    'site:boards.greenhouse.io "data journalist" OR "journalism" "data"',
    'site:boards.greenhouse.io "clinical data manager"',
    'site:boards.greenhouse.io "biostatistician"',
    'site:boards.greenhouse.io "epidemiologist" "data"',
    'site:boards.greenhouse.io "health economist"',
    'site:boards.greenhouse.io "real world evidence" analyst',
    'site:boards.greenhouse.io "market research analyst" "data"',
    'site:boards.greenhouse.io "competitive intelligence analyst"',
    'site:boards.greenhouse.io "consumer insights analyst"',
    'site:boards.greenhouse.io "customer analytics"',
    'site:boards.greenhouse.io "retention analyst"',
    'site:boards.greenhouse.io "conversion rate optimization"',
    'site:boards.greenhouse.io "search analyst" "data"',

    # ── Specific high-value untapped companies ────────────────────────────────
    'site:boards.greenhouse.io "data" "two sigma" OR "d.e. shaw" OR "renaissance"',
    'site:boards.greenhouse.io "data engineer" "jane street" OR "optiver" OR "imc"',
    'site:boards.greenhouse.io "data scientist" "hudson river trading" OR "virtu"',
    'site:boards.greenhouse.io "ml engineer" "citadel securities" OR "jump trading"',
    'site:boards.greenhouse.io "data" "millennium management" OR "bridgewater"',
    'site:boards.greenhouse.io "data engineer" "airwallex" OR "wise" OR "revolut"',
    'site:boards.greenhouse.io "data scientist" "canva" OR "atlassian" OR "afterpay"',
    'site:boards.greenhouse.io "analytics engineer" "shopify" OR "stripe" OR "square"',
    'site:boards.greenhouse.io "data" "notion" OR "figma" OR "miro" OR "loom"',
    'site:boards.greenhouse.io "ml engineer" "hugging face" OR "cohere" OR "mistral"',
    'site:boards.greenhouse.io "data engineer" "databricks" OR "snowflake" OR "dbt labs"',
    'site:boards.greenhouse.io "data scientist" "openai" OR "anthropic" OR "deepmind"',
    'site:boards.greenhouse.io "analytics" "linear" OR "retool" OR "vercel"',
    'site:boards.greenhouse.io "data" "rippling" OR "gusto" OR "deel" OR "remote"',
    'site:boards.greenhouse.io "data engineer" "brex" OR "ramp" OR "mercury"',
]

TARGET_RE = re.compile(
    r'\b(data analyst|data engineer|analytics engineer|data scientist|'
    r'machine learning|ml engineer|ai engineer|applied scientist|'
    r'research scientist|quantitative|llm engineer|mlops|computer vision|'
    r'data architect|bi engineer|business intelligence|revenue operations|'
    r'sales operations|marketing operations|staff data|principal data|'
    r'lead data|growth analyst|pricing analyst|people analytics|hr analytics|'
    r'insights analyst|reporting analyst|decision scientist|causal inference|'
    r'data steward|data governance|biostatistician|econometr|actuarial|'
    r'clinical data|market research analyst|consumer insights|customer analytics|'
    r'retention analyst|model risk|credit risk analyst|operations research)\b',
    re.IGNORECASE
)

GH_TOKEN_RE  = re.compile(r'boards\.greenhouse\.io/([a-z0-9_-]+)', re.IGNORECASE)
JUNK_TOKENS  = {'embed','js','css','api','v1','jobs','index','boards','apply','careers','embed'}

def get_conn():
    return psycopg2.connect(**DB_CONFIG)

def load_known_tokens() -> set:
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT board_token FROM discovered_companies WHERE ats_source='greenhouse'")
    known = {r[0] for r in cur.fetchall()}
    cur.close(); conn.close()
    return known

def insert_company(name, token, active):
    now = datetime.now(timezone.utc)
    cid = "DC" + hashlib.md5(f"greenhouse|{token}".encode()).hexdigest()[:10]
    conn = get_conn(); conn.autocommit = True; cur = conn.cursor()
    cur.execute("""
        INSERT INTO discovered_companies
            (company_id, company_name, ats_source, board_token,
             discovery_source, first_seen_at, last_seen_at, active_roles, total_seen, enabled)
        VALUES (%s,%s,'greenhouse',%s,'serper_v3',%s,%s,%s,1,true)
        ON CONFLICT (ats_source, board_token) DO NOTHING
    """, (cid, name, token, now, now, active))
    cur.close(); conn.close()

def serper_search(query):
    try:
        r = requests.post(SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 10}, timeout=10)
        return r.json().get("organic", []) if r.status_code == 200 else []
    except:
        return []

def probe_greenhouse(token):
    try:
        r = requests.get(f"https://api.greenhouse.io/v1/boards/{token}/jobs",
            timeout=8, headers={"User-Agent": "JobAnalyticsPipeline/1.0"})
        if r.status_code != 200:
            return token.replace("-"," ").title(), 0
        jobs   = r.json().get("jobs", [])
        active = sum(1 for j in jobs if TARGET_RE.search(j.get("title","")))
        return token.replace("-"," ").title(), active
    except:
        return token.replace("-"," ").title(), 0

def main(apply):
    known  = load_known_tokens()
    log.info(f"Known tokens: {len(known)}")

    new_tokens = {}
    credits    = 0

    for query in ROLE_QUERIES:
        log.info(f"[{credits+1}/{len(ROLE_QUERIES)}] {query[:70]}")
        for r in serper_search(query):
            text = r.get("link","") + " " + r.get("snippet","")
            for m in GH_TOKEN_RE.finditer(text):
                token = m.group(1).lower()
                if token in JUNK_TOKENS or len(token) < 3:
                    continue
                if re.match(r'^[0-9]+$', token):
                    continue
                if token not in known and token not in new_tokens:
                    new_tokens[token] = query[:50]
        credits += 1
        time.sleep(REQUEST_DELAY)

    log.info(f"\nSearch done — {len(new_tokens)} new tokens ({credits} credits used)")

    found = 0
    for i, (token, q) in enumerate(new_tokens.items()):
        name, active = probe_greenhouse(token)
        log.info(f"  [{i+1}/{len(new_tokens)}] {name} ({token}): {'✅ '+str(active)+' roles' if active else '○  0 roles'}")
        if apply:
            insert_company(name, token, active)
            known.add(token)
        if active > 0:
            found += 1
        time.sleep(REQUEST_DELAY)

    log.info(f"\n{'='*60}")
    log.info(f"Credits used:             {credits}")
    log.info(f"New tokens found:         {len(new_tokens)}")
    log.info(f"New companies with roles: {found}")
    log.info(f"{'✅ Written to DB' if apply else 'Dry run — add --apply'}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    main(apply=args.apply)
