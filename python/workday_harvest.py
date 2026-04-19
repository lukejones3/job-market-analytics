#!/usr/bin/env python3
"""
workday_harvest.py

Uses Serper to find Workday job board URLs for target roles,
extracts tenant/board/server combos, validates each one,
and adds new companies to WORKDAY_COMPANIES in ingest_jobs.py.

Usage:
    python3 python/workday_harvest.py          # discover and print
    python3 python/workday_harvest.py --apply  # also patch ingest_jobs.py
"""

import os, re, time, json, logging, argparse, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "26fc19fe9a8ac6037597fe034439f7f5f29cd178")
SERPER_URL     = "https://google.serper.dev/search"
REQUEST_DELAY  = 0.5

# Regex to parse Workday URLs
# https://{tenant}.{server}.myworkdayjobs.com/en-US/{board}/job/...
WD_URL_RE = re.compile(
    r'https://([a-zA-Z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com'
    r'(?:/[a-zA-Z_-]+)?/([a-zA-Z0-9_-]+)',
    re.IGNORECASE
)

TARGET_RE = re.compile(
    r'\b(data analyst|data engineer|analytics engineer|data scientist|'
    r'machine learning|ml engineer|ai engineer|business intelligence|'
    r'revenue operations|marketing analyst|product analyst|'
    r'financial analyst|data architect|mlops|quantitative)\b',
    re.IGNORECASE
)

# Queries targeting Workday job pages across all servers
QUERIES = [
    # Core roles across all WD servers
    'site:wd1.myworkdayjobs.com "data engineer"',
    'site:wd5.myworkdayjobs.com "data engineer"',
    'site:wd3.myworkdayjobs.com "data engineer"',
    'site:wd12.myworkdayjobs.com "data engineer"',
    'site:wd1.myworkdayjobs.com "data scientist"',
    'site:wd5.myworkdayjobs.com "data scientist"',
    'site:wd3.myworkdayjobs.com "data scientist"',
    'site:wd1.myworkdayjobs.com "analytics engineer"',
    'site:wd5.myworkdayjobs.com "analytics engineer"',
    'site:wd1.myworkdayjobs.com "machine learning engineer"',
    'site:wd5.myworkdayjobs.com "machine learning engineer"',
    'site:wd3.myworkdayjobs.com "machine learning engineer"',
    'site:wd1.myworkdayjobs.com "data analyst"',
    'site:wd5.myworkdayjobs.com "data analyst"',
    'site:wd3.myworkdayjobs.com "data analyst"',
    'site:wd12.myworkdayjobs.com "data analyst"',

    # Sector-specific
    'site:wd1.myworkdayjobs.com "data scientist" "financial"',
    'site:wd5.myworkdayjobs.com "data engineer" "healthcare"',
    'site:wd1.myworkdayjobs.com "data analyst" "insurance"',
    'site:wd5.myworkdayjobs.com "machine learning" "retail"',
    'site:wd1.myworkdayjobs.com "data engineer" "manufacturing"',
    'site:wd5.myworkdayjobs.com "data scientist" "pharma" OR "biotech"',
    'site:wd1.myworkdayjobs.com "analytics engineer" "consulting"',
    'site:wd3.myworkdayjobs.com "data analyst" "government" OR "defense"',
    'site:wd1.myworkdayjobs.com "data scientist" "bank" OR "finance"',
    'site:wd5.myworkdayjobs.com "data engineer" "media" OR "entertainment"',
    'site:wd1.myworkdayjobs.com "ml engineer" "technology"',
    'site:wd12.myworkdayjobs.com "data scientist"',

    # Senior roles
    'site:wd1.myworkdayjobs.com "senior data engineer"',
    'site:wd5.myworkdayjobs.com "senior data scientist"',
    'site:wd1.myworkdayjobs.com "principal data"',
    'site:wd5.myworkdayjobs.com "staff data"',
    'site:wd1.myworkdayjobs.com "lead data analyst"',
    'site:wd5.myworkdayjobs.com "director of data" OR "head of data"',

    # Salary disclosed
    'site:wd1.myworkdayjobs.com "data engineer" "salary"',
    'site:wd5.myworkdayjobs.com "data scientist" "$"',
    'site:wd1.myworkdayjobs.com "data analyst" "per year" OR "annually"',
    'site:wd5.myworkdayjobs.com "machine learning" "compensation"',

    # Tech stack
    'site:wd1.myworkdayjobs.com "data engineer" "snowflake" OR "databricks"',
    'site:wd5.myworkdayjobs.com "data engineer" "spark" OR "kafka"',
    'site:wd1.myworkdayjobs.com "analytics engineer" "dbt"',
    'site:wd5.myworkdayjobs.com "data scientist" "python" "remote"',
    'site:wd1.myworkdayjobs.com "ml engineer" "pytorch" OR "tensorflow"',

    # Industries we're missing
    'site:wd1.myworkdayjobs.com "data" "university" OR "education"',
    'site:wd5.myworkdayjobs.com "data analyst" "nonprofit" OR "foundation"',
    'site:wd1.myworkdayjobs.com "data engineer" "energy" OR "utilities"',
    'site:wd5.myworkdayjobs.com "data scientist" "transportation" OR "logistics"',
    'site:wd1.myworkdayjobs.com "data analyst" "real estate" OR "construction"',
    'site:wd3.myworkdayjobs.com "data" "global" OR "international"',
    'site:wd1.myworkdayjobs.com "data engineer" "telecommunications"',
    'site:wd5.myworkdayjobs.com "data scientist" "legal" OR "law"',
]


def serper_search(query: str) -> list:
    try:
        r = requests.post(SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 10}, timeout=10)
        return r.json().get("organic", []) if r.status_code == 200 else []
    except:
        return []


def parse_workday_url(url: str):
    """Extract (tenant, server, board) from a Workday URL."""
    m = WD_URL_RE.search(url)
    if not m:
        return None
    tenant = m.group(1).lower()
    server = m.group(2).lower()
    board  = m.group(3)
    # Skip if board looks like a job ID or path segment
    if board.lower() in {'job', 'jobs', 'en-us', 'en', 'external', 'apply'}:
        # Try to get the next segment
        parts = url.split('/')
        try:
            idx = parts.index('en-US') if 'en-US' in parts else parts.index('en-us')
            board = parts[idx + 1] if idx + 1 < len(parts) else board
        except:
            pass
    return tenant, server, board


def validate_workday(tenant: str, server: str, board: str) -> tuple:
    """
    Hit the Workday API to confirm the board works and count target roles.
    Returns (works: bool, active_roles: int, sample_title: str)
    """
    url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    try:
        r = requests.post(url,
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "data"},
            headers=headers, timeout=12)
        if r.status_code != 200:
            return False, 0, ""
        data     = r.json()
        postings = data.get("jobPostings", [])
        active   = sum(1 for p in postings if TARGET_RE.search(p.get("title", "")))
        sample   = next((p["title"] for p in postings if TARGET_RE.search(p.get("title",""))), "")
        return True, active, sample
    except:
        return False, 0, ""


def guess_company_name(tenant: str) -> str:
    """Convert tenant slug to a readable company name."""
    # Clean up common suffixes
    name = tenant
    for suffix in ['careers', 'jobs', 'external', 'corp', 'inc', 'llc', 'ltd']:
        name = re.sub(rf'{suffix}$', '', name, flags=re.IGNORECASE)
    # Convert camelCase or snake_case to Title Case
    name = re.sub(r'([a-z])([A-Z])', r'\1 \2', name)
    name = name.replace('_', ' ').replace('-', ' ')
    return name.strip().title()


def load_existing_tenants() -> set:
    """Load tenants already in ingest_jobs.py WORKDAY_COMPANIES."""
    path = Path(__file__).parent / "ingest_jobs.py"
    content = path.read_text()
    return set(re.findall(r'\"([a-z0-9_-]+)\",\s+\"(wd\d+)\"', content))


def main(apply: bool):
    existing = load_existing_tenants()
    existing_tenants = {t for t, _ in existing}
    log.info(f"Existing Workday tenants: {len(existing_tenants)}")

    # Collect all new tenant/server/board combos
    found = {}  # tenant -> (server, board, source_query)
    credits = 0

    for query in QUERIES:
        log.info(f"[{credits+1}/{len(QUERIES)}] {query[:70]}")
        results = serper_search(query)
        credits += 1

        for r in results:
            url = r.get("link", "")
            parsed = parse_workday_url(url)
            if not parsed:
                continue
            tenant, server, board = parsed
            if tenant in existing_tenants or tenant in found:
                continue
            # Filter junk
            if len(tenant) < 2 or tenant in {'www', 'app', 'api', 'jobs', 'careers'}:
                continue
            found[tenant] = (server, board, query[:40])

        time.sleep(REQUEST_DELAY)

    log.info(f"\nFound {len(found)} new Workday tenants — validating...")

    valid = []
    for i, (tenant, (server, board, src)) in enumerate(found.items()):
        works, active, sample = validate_workday(tenant, server, board)
        name = guess_company_name(tenant)
        status = f"✅ {active} roles — {sample[:50]}" if works and active > 0 else ("⚠️  works, 0 target roles" if works else "❌ failed")
        log.info(f"  [{i+1}/{len(found)}] {name} ({tenant}.{server}/{board}): {status}")

        if works:
            valid.append((name, tenant, board, server, active))

        time.sleep(REQUEST_DELAY)

    # Summary
    with_roles = [(n, t, b, s, a) for n, t, b, s, a in valid if a > 0]
    log.info(f"\n{'='*60}")
    log.info(f"Credits used:              {credits}")
    log.info(f"New tenants found:         {len(found)}")
    log.info(f"Valid boards:              {len(valid)}")
    log.info(f"With target roles:         {len(with_roles)}")

    if with_roles:
        log.info(f"\nNew companies to add:")
        for name, tenant, board, server, active in sorted(with_roles, key=lambda x: -x[4]):
            log.info(f"  ({active:3d} roles) (\"{name}\", \"{tenant}\", \"{board}\", \"{server}\"),")

    if apply and with_roles:
        # Patch ingest_jobs.py
        path = Path(__file__).parent / "ingest_jobs.py"
        content = path.read_text()

        new_lines = "\n    # Workday harvest " + __import__('datetime').date.today().isoformat() + "\n"
        for name, tenant, board, server, active in sorted(with_roles, key=lambda x: -x[4]):
            new_lines += f"    (\"{name}\", \"{tenant}\", \"{board}\", \"{server}\"),\n"

        # Insert before the closing bracket of WORKDAY_COMPANIES
        insert_marker = "]\n\ndef fetch_workday_company"
        if insert_marker in content:
            content = content.replace(insert_marker, new_lines + insert_marker)
            path.write_text(content)
            log.info(f"\n✅ Patched ingest_jobs.py with {len(with_roles)} new companies")
            log.info("Deploy to server: scp python/ingest_jobs.py root@208.68.38.249:/opt/job-market-analytics/python/ingest_jobs.py")
        else:
            log.warning("Could not find insert marker in ingest_jobs.py — print manually above")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Patch ingest_jobs.py with new companies")
    args = ap.parse_args()
    main(apply=args.apply)
