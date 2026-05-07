#!/usr/bin/env python3
"""
taleo_harvest.py

Harvests data/ML job postings from Oracle Taleo-powered career boards.
Taleo is widely used by consulting firms, pharma, CPG, and large retailers.

Approach:
  1. POST keyword search to the Taleo careersection search endpoint
  2. Parse HTML response with BeautifulSoup to extract job IDs + titles
  3. Fetch per-job detail pages for full descriptions

URL patterns (varies by tenant version):
  Search:  https://{slug}.taleo.net/careersection/{section}/jobsearch.ftl
  Detail:  https://{slug}.taleo.net/careersection/{section}/jobdetail.ftl?job={id}

Note: Taleo section names and IDs vary by tenant. This harvester probes
common section names ("2", "10", "externalsite", "External") and uses
whichever responds with job listings.

Usage (standalone):
    python python/taleo_harvest.py --dry-run
    python python/taleo_harvest.py --apply
    python python/taleo_harvest.py --apply --company kpmg

Cron:
    50 1 * * * cd ~/github/job-market-analytics && python python/taleo_harvest.py --apply >> logs/taleo.log 2>&1
"""

import hashlib
import logging
import os
import re
import sys
import time
import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

sys.path.insert(0, str(Path(__file__).parent))
from location_normalizer import normalize_location

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REQUEST_DELAY = 0.7
REQUEST_TIMEOUT = 15
USER_AGENT = "LanderJobBot/1.0 contact: jones31luke@gmail.com"

SEARCH_KEYWORDS = [
    "data analyst",
    "data engineer",
    "data scientist",
    "machine learning",
    "analytics",
    "business intelligence",
]

# Taleo section names to probe in order of frequency
TALEO_SECTIONS = ["2", "10", "externalsite", "External", "careersection"]

# ============================================================
# COMPANY LIST
# ============================================================
# Format: (display_name, slug, section)
# section = known Taleo careersection ID. Set to None to auto-probe.

TALEO_COMPANIES: List[Tuple[str, str, Optional[str]]] = [
    # Consulting / professional services
    ("KPMG",              "kpmg",          "2"),
    ("Deloitte",          "deloitte",      "2"),
    ("EY",                "ey",            "2"),
    ("Accenture",         "accenture",     "2"),
    # Pharma / life sciences
    ("Johnson & Johnson", "jnj",           "2"),
    ("Merck",             "merck",         "2"),
    ("AbbVie",            "abbvie",        "2"),
    ("Bristol Myers Squibb", "bms",        "2"),
    # Retail / consumer
    ("Home Depot",        "homedepot",     "2"),
    ("Kroger",            "kroger",        "2"),
    ("Dollar Tree",       "dollartree",    "2"),
    ("AutoNation",        "autonation",    "2"),
    # Industrial / energy
    ("Honeywell",         "honeywell",     "2"),
    ("Baker Hughes",      "bakerhughes",   "2"),
    ("Halliburton",       "halliburton",   "2"),
    ("Schlumberger",      "slb",           "2"),
    # Logistics
    ("UPS",               "upsjobs",       "2"),
    ("FedEx",             "fedex",         "2"),
    # Financial
    ("Lincoln Financial", "lincolnfinancial", "2"),
    ("Unum",              "unum",          "2"),
]

# ============================================================
# SHARED UTILITIES
# ============================================================

@dataclass
class RawJob:
    source:          str
    source_id:       str
    title:           str
    company:         str
    location:        Optional[str] = None
    description:     Optional[str] = None
    job_url:         Optional[str] = None
    salary_min:      Optional[float] = None
    salary_max:      Optional[float] = None
    salary_period:   Optional[str] = None
    workplace_type:  Optional[str] = None
    employment_type: Optional[str] = None
    posted_date:     Optional[str] = None
    remote:          bool = False
    metadata:        Dict = field(default_factory=dict)


def _clean(s: str) -> str:
    s = (s or "").replace(" ", " ")
    return re.sub(r"[ \t]+", " ", s).strip()


def _strip_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "lxml")
    return re.sub(r"\n{3,}", "\n\n", soup.get_text(separator="\n")).strip()


_TARGET_RE = re.compile(
    r"\b(data\s+(analyst|engineer|scientist|architect|steward|governance|quality|platform|ops|modell?er)|"
    r"analytics\s+engineer|analytics\s+(analyst|manager|director|lead)|"
    r"machine\s+learning\s+engineer|ml\s+engineer|ai\s+engineer|mlops|"
    r"applied\s+scientist|research\s+scientist|data\s+science|"
    r"business\s+intelligence|bi\s+(analyst|engineer|developer)|"
    r"revenue\s+operations|revops|quantitative\s+(analyst|researcher)|"
    r"data\s+infrastructure|llm\s+engineer|decision\s+scientist|"
    r"nlp\s+engineer|computer\s+vision\s+engineer|"
    r"marketing\s+analyst|product\s+analyst|financial\s+analyst|"
    r"pricing\s+analyst|fraud\s+analyst|risk\s+analyst|people\s+analyst)\b",
    re.IGNORECASE,
)
_BLOCK_RE = re.compile(
    r"\b(software\s+engineer|frontend|front[\s-]end|backend|back[\s-]end|"
    r"devops|site\s+reliability|mobile\s+engineer|android|ios\b|"
    r"account\s+executive|account\s+manager|sales\s+development|"
    r"business\s+development|recruiter|recruiting|customer\s+success|"
    r"customer\s+support|product\s+designer|ux\s+(designer|researcher)|"
    r"legal\s+counsel|compliance\s+officer|payroll|accounting|"
    r"hardware|firmware|embedded\b)\b",
    re.IGNORECASE,
)


def _is_target_role(title: str) -> bool:
    if not title:
        return False
    if _BLOCK_RE.search(title):
        return False
    return bool(_TARGET_RE.search(title))


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def _md5_id(prefix: str, s: str, n: int = 10) -> str:
    return prefix + hashlib.md5(s.encode()).hexdigest()[:n]


def _get_html(url: str, params: dict = None, data: dict = None) -> Optional[str]:
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    try:
        if data:
            r = requests.post(url, data=data, headers=headers, timeout=REQUEST_TIMEOUT)
        else:
            r = requests.get(url, params=params, headers=headers, timeout=REQUEST_TIMEOUT)
        if r.status_code == 200:
            return r.text
        if r.status_code in (404, 410):
            return None
        log.debug(f"  HTTP {r.status_code} for {url}")
        return None
    except requests.RequestException as e:
        log.debug(f"  Request failed {url}: {e}")
        return None

# ============================================================
# TALEO PARSER
# ============================================================

# Taleo job IDs appear in URLs like:
# jobdetail.ftl?job=12345  or  jobdetail.ftl?job=AB1234CD
_TALEO_JOB_ID_RE = re.compile(r'jobdetail\.ftl\?job=([A-Za-z0-9_-]+)', re.IGNORECASE)


def _probe_section(slug: str, known_section: Optional[str]) -> Optional[str]:
    """
    Find the working Taleo careersection for this tenant.
    Returns section string (e.g. "2") or None if none respond.
    """
    sections = [known_section] if known_section else TALEO_SECTIONS
    for section in sections:
        if not section:
            continue
        url = f"https://{slug}.taleo.net/careersection/{section}/jobsearch.ftl"
        html = _get_html(url, params={"lang": "en"})
        time.sleep(0.3)
        if html and "taleo" in html.lower() and ("jobdetail" in html.lower() or "jobsearch" in html.lower()):
            log.debug(f"  Taleo [{slug}] found section: {section}")
            return section
    return None


def _parse_taleo_listings(html: str) -> List[Tuple[str, str, str]]:
    """
    Parse a Taleo job search results page.
    Returns list of (job_id, title, location).
    """
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen_ids = set()

    # Find all links with jobdetail.ftl?job=... pattern
    for a in soup.find_all("a", href=_TALEO_JOB_ID_RE):
        href = a.get("href", "")
        m = _TALEO_JOB_ID_RE.search(href)
        if not m:
            continue
        job_id = m.group(1)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        # Title: from link text or nearest heading
        title = _clean(a.get_text())
        if not title or len(title) < 3:
            parent = a.find_parent(["tr", "li", "div"])
            if parent:
                for tag in ["span", "td", "h2", "h3"]:
                    el = parent.find(tag)
                    if el and len(_clean(el.get_text())) > 3:
                        title = _clean(el.get_text())
                        break

        # Location: look in the same row or nearby element
        location = ""
        parent = a.find_parent(["tr", "li", "div"])
        if parent:
            # Taleo often has city/state in a sibling <td> or span
            tds = parent.find_all("td")
            for td in tds:
                text = _clean(td.get_text())
                # Match "City, ST" or "State Name" patterns
                if re.search(r'[A-Z]{2}$|,\s*[A-Z]{2}', text) and len(text) < 80:
                    location = text
                    break
            if not location:
                loc_el = parent.find(class_=re.compile(r"location|city", re.I))
                if loc_el:
                    location = _clean(loc_el.get_text())

        results.append((job_id, title, location))

    return results


def _fetch_taleo_description(slug: str, section: str, job_id: str) -> Tuple[str, str, Optional[str]]:
    """
    Fetch a Taleo job detail page.
    Returns (description, location, posted_date).
    """
    url = f"https://{slug}.taleo.net/careersection/{section}/jobdetail.ftl"
    html = _get_html(url, params={"job": job_id, "lang": "en"})
    if not html:
        return "", "", None

    soup = BeautifulSoup(html, "lxml")
    desc = ""
    location = ""
    posted_date = None

    # Description: Taleo puts it in a div with id/class containing "description"
    for sel in [
        {"class_": re.compile(r"job.?description|description.?text|jobDescriptionText", re.I)},
        {"id": re.compile(r"description|jobDesc|mainContent", re.I)},
    ]:
        el = soup.find(**sel)
        if el:
            desc = _strip_html(str(el))
            if len(desc) > 100:
                break

    # Fallback: largest content div
    if not desc:
        divs = [(len(el.get_text()), el) for el in soup.find_all(["div", "section"])
                if len(el.get_text()) > 150]
        if divs:
            desc = _strip_html(str(max(divs, key=lambda x: x[0])[1]))

    # Location from meta or structured data
    meta_loc = soup.find("meta", {"property": "og:locality"}) or \
               soup.find("meta", {"name": "twitter:data1"})
    if meta_loc and meta_loc.get("content"):
        location = _clean(meta_loc["content"])

    if not location:
        for sel in [
            {"class_": re.compile(r"location|city|jobLocation", re.I)},
            {"itemprop": "addressLocality"},
        ]:
            el = soup.find(**sel)
            if el:
                location = _clean(el.get_text())
                break

    # Posted date
    date_el = soup.find(class_=re.compile(r"date|posted|publish", re.I))
    if date_el:
        date_text = _clean(date_el.get_text())
        dm = re.search(r'(\d{4}-\d{2}-\d{2})', date_text)
        if dm:
            posted_date = dm.group(1)

    return desc[:8000], location, posted_date


def fetch_taleo(company_name: str, slug: str, section: Optional[str] = None) -> List[RawJob]:
    """
    Pull all data/ML jobs from a Taleo career board.
    Auto-probes section if not specified.
    """
    # Find working section
    working_section = _probe_section(slug, section)
    if not working_section:
        log.debug(f"  Taleo [{company_name}] ({slug}): no accessible section found")
        return []

    search_url = f"https://{slug}.taleo.net/careersection/{working_section}/jobsearch.ftl"
    jobs: List[RawJob] = []
    seen_ids: set = set()

    for keyword in SEARCH_KEYWORDS:
        # Try GET first (most Taleo versions support keyword param)
        params = {"lang": "en", "keyword": keyword, "searchExpanded": "true"}
        html = _get_html(search_url, params=params)
        time.sleep(REQUEST_DELAY)

        if not html:
            continue

        listings = _parse_taleo_listings(html)
        log.debug(f"  Taleo [{company_name}] keyword='{keyword}': {len(listings)} candidates")

        for job_id, title, location in listings:
            if job_id in seen_ids:
                continue
            if not title or not _is_target_role(title):
                continue
            seen_ids.add(job_id)

            # Fetch full description
            desc, detail_location, posted_date = _fetch_taleo_description(slug, working_section, job_id)
            time.sleep(REQUEST_DELAY)

            if not location and detail_location:
                location = detail_location

            # US filter
            loc_lower = (location or "").lower()
            if "remote" in loc_lower or "virtual" in loc_lower:
                workplace_type = "remote"
            elif "hybrid" in loc_lower:
                workplace_type = "hybrid"
            else:
                workplace_type = None

            if normalize_location(location, workplace_type).should_drop:
                continue

            job_url = (
                f"https://{slug}.taleo.net/careersection/{working_section}"
                f"/jobdetail.ftl?job={job_id}&lang=en"
            )

            jobs.append(RawJob(
                source="taleo",
                source_id=f"{slug}_{job_id}",
                title=title,
                company=company_name,
                location=location,
                description=desc,
                job_url=job_url,
                workplace_type=workplace_type,
                posted_date=posted_date,
                metadata={"slug": slug, "section": working_section, "taleo_job_id": job_id},
            ))

    log.info(f"  Taleo [{company_name}]: {len(jobs)} target roles found")
    return jobs


def fetch_all_taleo() -> List[RawJob]:
    """Fetch all Taleo companies, DB-first then hardcoded fallback."""
    companies_with_section: List[Tuple[str, str, Optional[str]]] = []
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT company_name, board_token FROM discovered_companies "
            "WHERE ats_source = 'taleo' AND enabled = true ORDER BY active_roles DESC"
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if rows:
            # board_token format: "slug/section" or just "slug"
            for name, token in rows:
                parts = token.split("/", 1)
                slug = parts[0]
                sect = parts[1] if len(parts) > 1 else None
                companies_with_section.append((name, slug, sect))
            log.info(f"Taleo: loaded {len(companies_with_section)} companies from DB")
    except Exception as e:
        log.debug(f"Could not load Taleo companies from DB: {e}")

    if not companies_with_section:
        companies_with_section = TALEO_COMPANIES
        log.info(f"Taleo: using hardcoded list ({len(companies_with_section)} companies)")

    all_jobs: List[RawJob] = []
    for company_name, slug, section in companies_with_section:
        try:
            jobs = fetch_taleo(company_name, slug, section)
            all_jobs.extend(jobs)
            time.sleep(0.5)
        except Exception as e:
            log.warning(f"  Taleo [{company_name}] failed: {e}")

    log.info(f"Taleo total: {len(all_jobs)} jobs")
    return all_jobs

# ============================================================
# DB WRITE (standalone mode)
# ============================================================

def ingest_jobs_to_db(jobs: List[RawJob], apply: bool) -> None:
    if not apply:
        log.info(f"DRY RUN — {len(jobs)} jobs would be inserted. Sample:")
        for j in jobs[:5]:
            log.info(f"  [{j.source}] {j.company} — {j.title} | {j.location}")
        return

    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        ALTER TABLE job_postings
          ADD COLUMN IF NOT EXISTS ingestion_source text,
          ADD COLUMN IF NOT EXISTS source_id        text,
          ADD COLUMN IF NOT EXISTS description_quality text DEFAULT 'full',
          ADD COLUMN IF NOT EXISTS job_url          text
    """)
    conn.commit()

    inserted = skipped = errors = 0
    for job in jobs:
        try:
            job_id    = _md5_id("J", f"{job.source}|{job.source_id}")
            desc      = job.description or ""
            desc_hash = hashlib.md5(desc.encode()).hexdigest()
            _loc      = normalize_location(job.location, job.workplace_type)

            cur.execute("""
                INSERT INTO job_postings (
                    job_id, source, description_text, desc_hash,
                    date_found, ingested_at, posted_date,
                    workplace_type, employment_type, job_url,
                    status, ingestion_source, source_id,
                    description_quality, data_tier, last_seen_at,
                    loc_city, loc_state, loc_country
                ) VALUES (
                    %s,%s,%s,%s, now(),now(),%s,
                    %s,%s,%s,
                    'raw',%s,%s,'full',1,now(),
                    %s,%s,%s
                )
                ON CONFLICT (job_id) DO UPDATE SET last_seen_at = now()
            """, (
                job_id, job.source, desc, desc_hash,
                job.posted_date,
                job.workplace_type, job.employment_type, job.job_url,
                job.source, job.source_id,
                _loc.city, _loc.state, _loc.country,
            ))

            if cur.rowcount > 0:
                if job.company:
                    cid = _md5_id("C", job.company)
                    cur.execute("INSERT INTO companies (company_id, company_name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (cid, job.company))
                    cur.execute("UPDATE job_postings SET company_id=%s WHERE job_id=%s AND company_id IS NULL", (cid, job_id))
                if job.title:
                    rid = _md5_id("R", job.title)
                    cur.execute("INSERT INTO roles (role_id, role_name) VALUES (%s,%s) ON CONFLICT DO NOTHING", (rid, job.title))
                    cur.execute("UPDATE job_postings SET role_id=%s WHERE job_id=%s AND role_id IS NULL", (rid, job_id))
                inserted += 1
            else:
                skipped += 1

        except Exception as e:
            log.error(f"  Failed [{job.company}] {job.title}: {e}")
            conn.rollback()
            errors += 1
            continue

    conn.commit()
    cur.close()
    conn.close()
    log.info(f"Taleo DB write: inserted={inserted} skipped={skipped} errors={errors}")

# ============================================================
# ENTRY POINT
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Harvest Taleo job boards.")
    ap.add_argument("--apply", action="store_true", help="Write to DB")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true")
    ap.add_argument("--company", default=None, help="Only run for this slug")
    args = ap.parse_args()

    if args.company:
        match = [(n, s, sect) for n, s, sect in TALEO_COMPANIES if s == args.company]
        companies = match or [(args.company.title(), args.company, None)]
        all_jobs = []
        for name, slug, section in companies:
            all_jobs.extend(fetch_taleo(name, slug, section))
    else:
        all_jobs = fetch_all_taleo()

    ingest_jobs_to_db(all_jobs, apply=args.apply)


if __name__ == "__main__":
    main()
