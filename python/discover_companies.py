#!/usr/bin/env python3
"""
discover_companies.py

Three-tier company discovery pipeline:

  Tier 1 — Adzuna redirect_url parsing (free, automated)
    Search Adzuna for target roles → parse redirect_urls →
    extract Greenhouse tokens and Lever slugs →
    probe each company full board → store in discovered_companies

  Tier 2 — Lever sitemap (free)
    Fetch jobs.lever.co sitemap → extract all company slugs →
    probe for target roles → store in discovered_companies

  Tier 3 — Seed from ingest_jobs.py manual lists (one-time)
    Import your existing hardcoded lists into discovered_companies

Usage:
    python python/discover_companies.py --apply --limit 20   # test run
    python python/discover_companies.py --apply              # full run
    python python/discover_companies.py --apply --source lever
    python python/discover_companies.py --apply --source seed
    python python/discover_companies.py --apply --source refresh

Nightly cron (1am, before ingest at 2am):
    0 1 * * * cd ~/github/job-market-analytics && python python/discover_companies.py --apply >> logs/discover.log 2>&1
"""

import os
import re
import time
import logging
import argparse
import hashlib
from pathlib import Path
from typing import List, Tuple, Set, Optional, Dict
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

REQUEST_DELAY   = 0.3
REQUEST_TIMEOUT = 8

# ============================================================
# TARGET ROLE PHRASES (single source of truth)
# ============================================================
TARGET_ROLE_PHRASES = [
    r"data analyst", r"analytics analyst", r"business analyst",
    r"business intelligence analyst", r"bi analyst",
    r"marketing analyst", r"product analyst", r"financial analyst",
    r"fp&a analyst", r"revenue analyst", r"operations analyst",
    r"reporting analyst", r"insights analyst", r"supply chain analyst",
    r"pricing analyst", r"growth analyst", r"quantitative analyst",
    r"research analyst", r"strategy analyst", r"people analyst",
    r"workforce analyst", r"sales analyst", r"credit analyst",
    r"risk analyst", r"data science analyst",
    r"decision scientist", r"customer insights analyst",
    r"consumer insights analyst", r"market research analyst",
    r"competitive intelligence analyst", r"web analyst",
    r"digital analyst", r"ecommerce analyst", r"retail analyst",
    r"inventory analyst", r"demand planning analyst",
    r"workforce planning analyst", r"compensation analyst",
    r"hr analyst", r"clinical data analyst", r"healthcare analyst",
    r"investment analyst", r"portfolio analyst", r"fraud analyst",
    r"data governance analyst", r"data quality analyst",
    r"data operations analyst", r"data steward",
    r"data engineer", r"analytics engineer", r"bi engineer",
    r"business intelligence engineer", r"machine learning engineer",
    r"ml engineer", r"data platform engineer",
    r"data infrastructure engineer", r"data reliability engineer",
    r"applied ml engineer", r"ai engineer", r"llm engineer",
    r"analytics platform engineer",
    r"data scientist", r"applied scientist", r"research scientist",
    r"applied data scientist", r"quantitative researcher",
    r"quantitative scientist", r"principal data scientist",
    r"staff data scientist",
    r"bi developer", r"business intelligence developer",
    r"tableau developer", r"power bi developer",
    r"looker developer", r"reporting developer",
    r"data architect", r"bi architect", r"analytics architect",
    r"revenue operations", r"revops", r"marketing operations",
    r"sales operations",
    r"analytics manager", r"data manager", r"analytics lead",
    r"data lead", r"data science manager", r"data engineering manager",
    r"director of analytics", r"director of data",
    r"head of analytics", r"head of data",
    r"data specialist", r"analytics specialist",
    r"analytics consultant", r"data product manager",
    r"ai analyst", r"quantitative developer",
    r"staff data engineer", r"staff analytics engineer",
    r"staff machine learning engineer",
    r"machine learning scientist", r"applied researcher",
    r"ai researcher", r"founding data scientist",
    r"data science lead", r"growth data analyst",
    r"decision analyst", r"ai/ml engineer",
]

ROLE_BLOCKLIST = [
    r"\bandroid\b", r"\bios\b", r"\bfrontend\b", r"\bbackend\b",
    r"\bfullstack\b", r"\bdevops\b", r"\bsre\b",
    r"\bsoftware engineer\b", r"\bmobile engineer\b",
    r"\bav builds\b", r"\baccount executive\b",
    r"\bsales development\b", r"\brecruiter\b",
    r"\bcustomer success\b", r"\bsolutions engineer\b",
    r"\bproject manager\b", r"\bproduct manager\b",
    r"\bproduct designer\b", r"\bux\b", r"\bcontent\b",
    r"\blegal\b", r"\baccounting\b", r"\bplatform engineer\b",
    r"\binfrastructure engineer\b", r"\bsecurity engineer\b",
    r"\bnetwork engineer\b", r"\bembedded\b", r"\bhardware\b",
]

_PHRASE_PATS = [re.compile(r"\b" + p + r"\b", re.IGNORECASE) for p in TARGET_ROLE_PHRASES]
_BLOCK_PATS  = [re.compile(p, re.IGNORECASE) for p in ROLE_BLOCKLIST]

_NON_US_RE = re.compile(
    r"\b(canada|uk|united kingdom|ireland|india|mexico|australia|"
    r"singapore|germany|france|netherlands|spain|brazil|japan|"
    r"toronto|vancouver|london|dublin|bangalore|bengaluru|hyderabad|"
    r"chennai|pune|mumbai|sydney|berlin|amsterdam|paris|madrid|"
    r"emea|apac|latam|remote\s*-\s*international)\b",
    re.IGNORECASE,
)

def _is_target_role(title: str) -> bool:
    if not title:
        return False
    for pat in _BLOCK_PATS:
        if pat.search(title):
            return False
    for pat in _PHRASE_PATS:
        if pat.search(title):
            return True
    return False

def _is_us_location(location: str) -> bool:
    if not location:
        return True
    return not _NON_US_RE.search(location)

# ============================================================
# HTTP
# ============================================================

def _get(url: str, params: dict = None) -> Optional[dict]:
    try:
        r = requests.get(url, params=params, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "JobAnalyticsPipeline/1.0"})
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None

def _get_text(url: str) -> Optional[str]:
    try:
        r = requests.get(url, timeout=20,
                         headers={"User-Agent": "JobAnalyticsPipeline/1.0"})
        if r.status_code == 200:
            return r.text
        return None
    except Exception:
        return None

def _throttle():
    time.sleep(REQUEST_DELAY)

# ============================================================
# SCHEMA
# ============================================================

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS discovered_companies (
    company_id       text PRIMARY KEY,
    company_name     text NOT NULL,
    ats_source       text NOT NULL,
    board_token      text NOT NULL,
    discovery_source text,
    first_seen_at    timestamptz NOT NULL DEFAULT now(),
    last_seen_at     timestamptz NOT NULL DEFAULT now(),
    last_had_roles   timestamptz,
    active_roles     int NOT NULL DEFAULT 0,
    total_seen       int NOT NULL DEFAULT 0,
    enabled          boolean NOT NULL DEFAULT true,
    UNIQUE(ats_source, board_token)
);
CREATE INDEX IF NOT EXISTS idx_dc_source   ON discovered_companies(ats_source);
CREATE INDEX IF NOT EXISTS idx_dc_enabled  ON discovered_companies(enabled, ats_source);
CREATE INDEX IF NOT EXISTS idx_dc_roles    ON discovered_companies(active_roles DESC);
"""

def ensure_schema(cur):
    cur.execute(SCHEMA_SQL)

# ============================================================
# DB
# ============================================================

def _cid(ats_source: str, token: str) -> str:
    h = hashlib.md5(f"{ats_source}|{token}".encode()).hexdigest()[:10]
    return f"DC{h}"

def upsert_company(cur, name: str, ats: str, token: str,
                   active: int, discovery_source: str = "unknown"):
    now = datetime.now(timezone.utc)
    cur.execute(
        """
        INSERT INTO discovered_companies
            (company_id, company_name, ats_source, board_token,
             discovery_source, first_seen_at, last_seen_at,
             last_had_roles, active_roles, total_seen, enabled)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,1,true)
        ON CONFLICT (ats_source, board_token) DO UPDATE SET
            last_seen_at  = EXCLUDED.last_seen_at,
            active_roles  = EXCLUDED.active_roles,
            total_seen    = discovered_companies.total_seen + 1,
            last_had_roles = CASE
                WHEN EXCLUDED.active_roles > 0 THEN EXCLUDED.last_seen_at
                ELSE discovered_companies.last_had_roles
            END
        """,
        (_cid(ats, token), name, ats, token, discovery_source,
         now, now, now if active > 0 else None, active)
    )

def load_known_tokens(cur) -> Set[str]:
    cur.execute("SELECT board_token FROM discovered_companies")
    return {r["board_token"] for r in cur.fetchall()}

# ============================================================
# PROBES
# ============================================================

def probe_greenhouse(token: str) -> int:
    url = f"https://api.greenhouse.io/v1/boards/{token}/jobs"
    data = _get(url)
    _throttle()
    if not data or "jobs" not in data:
        return 0
    return sum(
        1 for j in data["jobs"]
        if _is_target_role(j.get("title", ""))
        and _is_us_location(
            ((j.get("offices") or [{}])[0]).get("name", "")
        )
    )

def probe_lever(slug: str) -> int:
    url = f"https://api.lever.co/v0/postings/{slug}"
    data = _get(url, params={"mode": "json"})
    _throttle()
    if not data or not isinstance(data, list):
        return 0
    return sum(
        1 for j in data
        if _is_target_role(j.get("text", ""))
        and _is_us_location(
            (j.get("categories") or {}).get("location", "") or ""
        )
    )

def _parse_ats_from_url(url: str) -> Optional[Tuple[str, str, str]]:
    """Extract (ats_source, token, name_guess) from a job redirect URL."""
    if not url:
        return None
    # Greenhouse
    gh = re.search(
        r'(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io|'
        r'api\.greenhouse\.io/v1/boards)/([a-z0-9_-]+)',
        url, re.IGNORECASE
    )
    if gh:
        token = gh.group(1).lower()
        if token in {"embed", "js", "css", "api", "v1", "jobs"}:
            return None
        return ("greenhouse", token, token.replace("-", " ").title())
    # Lever
    lv = re.search(r'jobs\.lever\.co/([a-z0-9_-]+)', url, re.IGNORECASE)
    if lv:
        slug = lv.group(1).lower()
        return ("lever", slug, slug.replace("-", " ").title())
    return None


# ============================================================
# TIER 2: LEVER SITEMAP
# ============================================================

def discover_via_lever_sitemap(cur, apply: bool, limit: int) -> int:
    log.info("Fetching Lever sitemap...")
    text = _get_text("https://jobs.lever.co/sitemap.xml")
    if not text:
        log.warning("Could not fetch Lever sitemap")
        return 0

    slugs = sorted(set(re.findall(
        r'https://jobs\.lever\.co/([a-z0-9_-]+)/', text
    )))
    log.info(f"  {len(slugs)} unique slugs in Lever sitemap")

    known = load_known_tokens(cur)
    new_slugs = [s for s in slugs if s not in known]
    log.info(f"  {len(new_slugs)} new to probe")

    if limit:
        new_slugs = new_slugs[:limit]

    found = 0
    for slug in new_slugs:
        active = probe_lever(slug)
        name = slug.replace("-", " ").title()
        if active > 0:
            log.info(f"  ✅ [lever] {name} ({slug}): {active} roles")
            found += 1
        if apply:
            upsert_company(cur, name, "lever", slug, active, "lever_sitemap")

    return found

# ============================================================
# TIER 3: SEED FROM INGEST_JOBS.PY LISTS
# ============================================================

def seed_from_ingest_lists(cur, apply: bool) -> int:
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from ingest_jobs import GREENHOUSE_COMPANIES, LEVER_COMPANIES
    except ImportError as e:
        log.warning(f"Could not import from ingest_jobs: {e}")
        return 0

    known = load_known_tokens(cur)
    seeded = 0

    log.info(f"  Seeding {len(GREENHOUSE_COMPANIES)} Greenhouse companies...")
    for name, token in GREENHOUSE_COMPANIES:
        if token in known:
            continue
        active = probe_greenhouse(token)
        if apply:
            upsert_company(cur, name, "greenhouse", token, active, "manual")
        if active > 0:
            log.info(f"  ✅ [greenhouse] {name}: {active} roles")
        seeded += 1

    log.info(f"  Seeding {len(LEVER_COMPANIES)} Lever companies...")
    for name, slug in LEVER_COMPANIES:
        if slug in known:
            continue
        active = probe_lever(slug)
        if apply:
            upsert_company(cur, name, "lever", slug, active, "manual")
        if active > 0:
            log.info(f"  ✅ [lever] {name}: {active} roles")
        seeded += 1

    return seeded

# ============================================================
# REFRESH EXISTING
# ============================================================

def refresh_existing(cur, apply: bool) -> None:
    cur.execute(
        "SELECT company_name, ats_source, board_token FROM discovered_companies WHERE enabled = true ORDER BY last_seen_at ASC"
    )
    rows = cur.fetchall()
    log.info(f"  Refreshing {len(rows)} existing companies...")
    for row in rows:
        active = probe_greenhouse(row["board_token"]) if row["ats_source"] == "greenhouse" else probe_lever(row["board_token"])
        if apply:
            upsert_company(cur, row["company_name"], row["ats_source"], row["board_token"], active)

# ============================================================
# SUMMARY
# ============================================================

def print_summary(cur):
    try:
        cur.execute("""
            SELECT ats_source,
                   COUNT(*) as total,
                   COUNT(*) FILTER (WHERE active_roles > 0) as with_roles,
                   SUM(active_roles) as open_roles,
                   COUNT(*) FILTER (WHERE discovery_source='lever_sitemap') as from_sitemap,
                   COUNT(*) FILTER (WHERE discovery_source='manual') as from_manual
            FROM discovered_companies WHERE enabled=true
            GROUP BY ats_source ORDER BY ats_source
        """)
        log.info("=== discovered_companies summary ===")
        for r in cur.fetchall():
            log.info(
                f"  [{r['ats_source']}] {r['total']} companies | "
                f"{r['with_roles']} with roles | {r['open_roles']} open | "
                f"sitemap={r['from_sitemap']} manual={r['from_manual']}"
            )
    except Exception as e:
        log.warning(f"Summary error: {e}")

# ============================================================
# DB CONNECTION
# ============================================================

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", choices=["lever","greenhouse","seed","refresh","all"], default="all")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=0, help="Max new companies to probe per tier (0=all)")
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)
    ensure_schema(cur)
    conn.commit()

    if args.source in ("seed", "all"):
        log.info("=== Tier 3: Seeding from ingest_jobs.py ===")
        n = seed_from_ingest_lists(cur, args.apply)
        log.info(f"  Seeded {n} companies")
        if args.apply: conn.commit()

    if args.source in ("lever", "all"):
        log.info("=== Tier 2: Lever sitemap discovery ===")
        n = discover_via_lever_sitemap(cur, args.apply, args.limit)
        log.info(f"  {n} new Lever companies with roles")
        if args.apply: conn.commit()

    if args.source in ("refresh", "all"):
        log.info("=== Refreshing existing company role counts ===")
        refresh_existing(cur, args.apply)
        if args.apply: conn.commit()

    print_summary(cur)

    if not args.apply:
        conn.rollback()
        log.info("Dry run — add --apply to write")
    else:
        conn.commit()
        log.info("✅ Done")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
