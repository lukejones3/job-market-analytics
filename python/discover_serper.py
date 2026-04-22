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
        # Direct company name searches — known data-heavy companies
        'site:boards.greenhouse.io "Robinhood" data',
        'site:boards.greenhouse.io "Chime" data',
        'site:boards.greenhouse.io "Brex" data',
        'site:boards.greenhouse.io "Rippling" data',
        'site:boards.greenhouse.io "Gusto" data',
        'site:boards.greenhouse.io "Lattice" data',
        'site:boards.greenhouse.io "Carta" data',
        'site:boards.greenhouse.io "Deel" data',
        'site:boards.greenhouse.io "Remote" data',
        'site:boards.greenhouse.io "Mercury" data',
        'site:boards.greenhouse.io "Ramp" data',
        'site:boards.greenhouse.io "Navan" data',
        'site:boards.greenhouse.io "Zip" data engineer',
        'site:boards.greenhouse.io "Ironclad" data',
        'site:boards.greenhouse.io "Clio" data',
        'site:boards.greenhouse.io "Verkada" data',
        'site:boards.greenhouse.io "Verkada" machine learning',
        'site:boards.greenhouse.io "Samsara" data',
        'site:boards.greenhouse.io "Motive" data',
        'site:boards.greenhouse.io "KeepTruckin" data',
        'site:boards.greenhouse.io "Procore" data',
        'site:boards.greenhouse.io "Buildkite" data',
        'site:boards.greenhouse.io "PagerDuty" data',
        'site:boards.greenhouse.io "Grafana" data',
        'site:boards.greenhouse.io "Hashicorp" data',
        'site:boards.greenhouse.io "Cloudflare" data',
        'site:boards.greenhouse.io "Fastly" data',
        'site:boards.greenhouse.io "Akamai" data',
        'site:boards.greenhouse.io "Twitch" data',
        'site:boards.greenhouse.io "Discord" data',
        'site:boards.greenhouse.io "Snap" data scientist',
        'site:boards.greenhouse.io "Pinterest" data',
        'site:boards.greenhouse.io "Bumble" data',
        'site:boards.greenhouse.io "Hinge" data',
        'site:boards.greenhouse.io "Duolingo" data',
        'site:boards.greenhouse.io "Coursera" data',
        'site:boards.greenhouse.io "Udemy" data',
        'site:boards.greenhouse.io "Chegg" data',
        'site:boards.greenhouse.io "Calm" data',
        'site:boards.greenhouse.io "Headspace" data',
        'site:boards.greenhouse.io "Noom" data',
        'site:boards.greenhouse.io "Ro" data scientist',
        'site:boards.greenhouse.io "Hims" data',
        'site:boards.greenhouse.io "23andMe" data',
        'site:boards.greenhouse.io "Color Health" data',
        'site:boards.greenhouse.io "Tempus" data',
        'site:boards.greenhouse.io "Flatiron" data',
        'site:job-boards.greenhouse.io "Rippling" data',
        'site:job-boards.greenhouse.io "Carta" data',
        'site:job-boards.greenhouse.io "Procore" data',
        'site:job-boards.greenhouse.io "Verkada" data',
    ],
    "ashby_powered": [
        'site:jobs.ashbyhq.com "Powered by Ashby" data',
        'site:jobs.ashbyhq.com "Powered by Ashby" engineer',
        'site:jobs.ashbyhq.com "Powered by Ashby" scientist',
        'site:jobs.ashbyhq.com "Powered by Ashby" analyst',
        'site:jobs.ashbyhq.com "Powered by Ashby" machine learning',
        'site:jobs.ashbyhq.com "Powered by Ashby" analytics',
        'site:jobs.ashbyhq.com "Powered by Ashby" infrastructure',
        'site:jobs.ashbyhq.com "Powered by Ashby" platform',
    ],
    "lever": [
        'site:jobs.lever.co "actuarial analyst"',
        'site:jobs.lever.co "credit risk analyst"',
        'site:jobs.lever.co "campaign analyst"',
        'site:jobs.lever.co "media mix model"',
        'site:jobs.lever.co "marketing science"',
        'site:jobs.lever.co "measurement scientist"',
        'site:jobs.lever.co "experimentation scientist"',
        'site:jobs.lever.co "trust and safety" data',
        'site:jobs.lever.co "integrity analyst"',
        'site:jobs.lever.co "clinical data scientist"',
        'site:jobs.lever.co "bioinformatics"',
        'site:jobs.lever.co "computational biologist"',
        'site:jobs.lever.co "geospatial analyst"',
        'site:jobs.lever.co "supply chain analyst"',
        'site:jobs.lever.co "demand forecasting"',
        'site:jobs.lever.co "pricing scientist"',
        'site:jobs.lever.co "attribution analyst"',
        'site:jobs.lever.co "incrementality"',
        'site:jobs.lever.co "financial data scientist"',
        'site:jobs.lever.co "economist" data',
        'site:jobs.lever.co "research engineer" data',
        'site:jobs.lever.co "yield analyst"',
        'site:jobs.lever.co "health economist"',
        'site:jobs.lever.co "policy data"',
    ],
    # ashby excluded — Google index doesn't match Ashby API slugs reliably
    # Use discover_companies.py --source ashby instead
    # workday excluded — Google returns instance IDs not company subdomains
    # Workday companies are manually curated in ingest_jobs.py
    "icims": [
        'site:icims.com "data scientist" "United States"',
        'site:icims.com "data engineer" "United States"',
        'site:icims.com "data analyst" "United States"',
        'site:icims.com "analytics engineer" "United States"',
        'site:icims.com "machine learning engineer" "United States"',
        'site:icims.com "applied scientist" "United States"',
        'site:icims.com "business intelligence" "United States"',
        'site:icims.com "ml engineer" "United States"',
        'site:icims.com "research scientist" "United States"',
        'site:icims.com "staff data" "United States"',
        'site:icims.com "principal data" "United States"',
        'site:icims.com "data science manager" "United States"',
        'site:icims.com "revenue operations" "United States"',
        'site:icims.com "quantitative analyst" "United States"',
    ],
    "workday_dork": [
        'site:myworkdayjobs.com "data scientist"',
        'site:myworkdayjobs.com "data engineer"',
        'site:myworkdayjobs.com "analytics engineer"',
        'site:myworkdayjobs.com "machine learning engineer"',
        'site:myworkdayjobs.com "data analyst" "senior"',
        'site:myworkdayjobs.com "applied scientist"',
        'site:myworkdayjobs.com "business intelligence"',
        'site:myworkdayjobs.com "ml engineer"',
        'site:myworkdayjobs.com "research scientist"',
        'site:myworkdayjobs.com "staff data"',
    ],
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

def extract_icims_token(url: str) -> Optional[str]:
    """Extract company slug from iCIMS URLs.
    Patterns: careers-{company}.icims.com, {prefix}-{company}.icims.com
    Returns the full subdomain as token since that's what we need to probe.
    """
    import re as _re
    m = _re.search(r"https?://([a-z0-9\-]+)\.icims\.com", url, _re.IGNORECASE)
    if m:
        subdomain = m.group(1).lower()
        # Filter out generic subdomains
        if subdomain not in ("www", "social", "hrjobs", "jobs"):
            return subdomain
    return None

def extract_workday_dork_token(url: str) -> Optional[str]:
    """Extract company slug and instance from real Workday URLs like
    accenture.wd103.myworkdayjobs.com/AccentureCareers/..."""
    import re as _re
    m = _re.search(r"([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#\s]+)", url, _re.IGNORECASE)
    if m:
        company = m.group(1).lower()
        instance = m.group(2).lower()
        tenant = m.group(3)
        # Return as compound token: company|instance|tenant
        return f"{company}|{instance}|{tenant}"
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
    elif source == "workday_dork":
        return extract_workday_dork_token(url)
    elif source == "icims":
        return extract_icims_token(url)
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

def probe_icims(subdomain: str) -> tuple[bool, int, str]:
    """Probe an iCIMS company board for data/analytics roles."""
    try:
        # iCIMS search API
        url = f"https://{subdomain}.icims.com/jobs/search"
        params = {
            "ss": "1",
            "searchKeyword": "data",
            "searchLocation": "",
            "in_iframe": "1",
        }
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT,
                        headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return False, 0, ""

        # Parse job titles from response
        import re as _re
        titles = _re.findall(r'<span[^>]*class="[^"]*job-title[^"]*"[^>]*>([^<]+)<', r.text)
        if not titles:
            # Try alternate pattern
            titles = _re.findall(r'"jobTitle"\s*:\s*"([^"]+)"', r.text)

        target = [t for t in titles if TARGET_ROLE_RE.search(t)]
        name = subdomain.replace("careers-", "").replace("-jobs", "").replace("-", " ").title()
        return len(target) > 0, len(target), name
    except Exception:
        return False, 0, ""

def probe_workday_dork(token: str) -> tuple[bool, int, str]:
    """Probe a workday company using the exact instance/tenant from Google."""
    try:
        parts = token.split("|")
        if len(parts) != 3:
            return False, 0, ""
        company, instance, tenant = parts
        url = f"https://{company}.{instance}.myworkdayjobs.com/wday/cxs/{company}/{tenant}/jobs"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        r = requests.post(
            url,
            json={"limit": 20, "offset": 0, "searchText": "data"},
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            jobs = r.json().get("jobPostings", [])
            target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("title", ""))]
            name = company.replace("-", " ").title()
            return len(target) > 0, len(target), name
    except Exception:
        pass
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
    elif source == "workday_dork":
        return probe_workday_dork(token)
    elif source == "icims":
        return probe_icims(token)
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
    # For workday_dork, extract real board_token and use "workday" as source
    real_source = source
    real_token = token
    if source == "icims":
        real_source = "icims"
        real_token = token
    elif source == "workday_dork":
        parts = token.split("|")
        if len(parts) == 3:
            company, instance, tenant = parts
            real_source = "workday"
            real_token = f"{company}/{instance}/{tenant}"

    company_id = "C" + hashlib.md5(f"{real_source}:{real_token}".encode()).hexdigest()[:9]
    if apply:
        cur.execute("""
            INSERT INTO discovered_companies
                (company_id, company_name, ats_source, board_token,
                 active_roles, total_seen, discovery_source, enabled)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (ats_source, board_token) DO UPDATE SET
                last_seen_at = now(),
                active_roles = EXCLUDED.active_roles,
                last_had_roles = CASE
                    WHEN EXCLUDED.active_roles > 0 THEN now()
                    ELSE discovered_companies.last_had_roles
                END
        """, (company_id, name or token, real_source, real_token,
              active_roles, active_roles, "serper_dork", True))
    return True

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--source", choices=["greenhouse", "lever", "workday_dork", "icims", "all"],
                    default="all")
    ap.add_argument("--limit", type=int, default=500,
                    help="Max Serper queries to run")
    args = ap.parse_args()

    apply = args.apply and not args.dry_run
    sources = ["greenhouse", "lever", "workday_dork", "icims"] if args.source == "all" else [args.source]

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
