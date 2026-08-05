#!/usr/bin/env python3
"""
icims_harvest.py

Harvests data/ML job postings from iCIMS-powered career boards.
iCIMS is widely used by healthcare systems, defense, logistics,
and enterprise companies.

Approach:
  1. POST/GET keyword search to the iCIMS search endpoint
  2. Parse HTML response with BeautifulSoup to extract job IDs + titles
  3. Fetch per-job detail pages for full descriptions

URL patterns (varies by tenant version):
  Search:  https://{slug}.icims.com/jobs/search
  Detail:  https://{slug}.icims.com/jobs/{id}/job

Usage (standalone):
    python python/icims_harvest.py --dry-run
    python python/icims_harvest.py --apply
    python python/icims_harvest.py --apply --company kp

Cron:
    45 1 * * * cd ~/github/job-market-analytics && python python/icims_harvest.py --apply >> logs/icims.log 2>&1
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
from role_taxonomy import SEARCH_TERMS, is_target_role

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REQUEST_DELAY = 0.6
REQUEST_TIMEOUT = 15
USER_AGENT = "LanderJobBot/1.0 contact: jones31luke@gmail.com"

SEARCH_KEYWORDS = list(SEARCH_TERMS)

# ============================================================
# COMPANY LIST
# ============================================================
# Format: (display_name, slug)
# Probe URL: https://{slug}.icims.com/jobs/search
# If 404 → company not on iCIMS, harvester returns [] gracefully.

ICIMS_COMPANIES: List[Tuple[str, str]] = [
    # Healthcare
    ("Kaiser Permanente",     "kp"),
    ("CommonSpirit Health",   "commonspirit"),
    ("Providence Health",     "providence"),
    ("Dignity Health",        "dignityhealth"),
    ("Evolent Health",        "evolenthealth"),
    ("Kindred Healthcare",    "kindred"),
    ("Optum",                 "optum"),
    # Defense / aerospace
    ("BAE Systems",           "baesystems"),
    ("Textron",               "textron"),
    ("L3Harris",              "l3harris"),
    # Financial / insurance
    ("Aon",                   "aon"),
    ("Navient",               "navient"),
    ("AmeriHealth Caritas",   "amerihealthcaritas"),
    # Logistics / supply chain
    ("RXO",                   "rxo"),
    ("US Foods",              "usfoods"),
    ("Penske Logistics",      "penske"),
    # Consumer / retail
    ("Dollar General",        "dollargeneral"),
    ("Rite Aid",              "riteaid"),
    ("Advance Auto Parts",    "advanceauto"),
    ("Yum! Brands",           "yum"),
    # Tech / enterprise software
    ("OpenText",              "opentext"),
    ("Sabre Corporation",     "sabre"),
    ("Vericel Corporation",   "vericel"),
    # Staffing / services
    ("ManpowerGroup",         "manpowergroup"),
    ("Fortive",               "fortive"),
    ("Parker Hannifin",       "parker"),
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
    s = (s or "").replace(" ", " ")
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
    r"experimentation\s+(analyst|scientist)|marketing\s+analyst|"
    r"product\s+analyst|financial\s+analyst|pricing\s+analyst|"
    r"fraud\s+analyst|risk\s+analyst|people\s+analyst|"
    r"clinical\s+data\s+scientist|health\s+data)\b",
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
    return is_target_role(title)


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
    """Fetch a URL, return raw HTML text or None."""
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
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
# ICIMS PARSER
# ============================================================

# Current iCIMS portals usually include an SEO title slug between the numeric
# identifier and the final /job segment. Older portals omit it. Accept both:
#   /jobs/1234/job
#   /jobs/1234/data-engineer/job?in_iframe=1
_ICIMS_JOB_ID_RE = re.compile(r'/jobs/(\d+)/(?:[^/?#]+/)?job(?:[/?#]|$)', re.IGNORECASE)


def _parse_icims_job_ids(html: str, slug: str) -> List[Tuple[str, str, str]]:
    """
    Parse an iCIMS search results page.
    Returns list of (job_id, title, location).

    iCIMS HTML varies by tenant version but job IDs always appear in
    href="/jobs/{id}/job" links. We find those links and extract titles
    from the nearest heading or anchor text.
    """
    soup = BeautifulSoup(html, "lxml")
    results = []
    seen_ids = set()

    # Strategy 1: find all links matching /jobs/{id}/job pattern
    for a in soup.find_all("a", href=_ICIMS_JOB_ID_RE):
        href = a.get("href", "")
        m = _ICIMS_JOB_ID_RE.search(href)
        if not m:
            continue
        job_id = m.group(1)
        if job_id in seen_ids:
            continue
        seen_ids.add(job_id)

        # Current cards include screen-reader labels such as "Requisition
        # Title" inside the anchor. Prefer the visible heading so those labels
        # do not become part of the normalized role name.
        heading = a.find(re.compile(r"h[1-6]"))
        title = _clean(heading.get_text()) if heading else _clean(a.get_text())
        if not title or len(title) < 3:
            parent = a.find_parent(["li", "tr", "div", "article"])
            if parent:
                h = parent.find(re.compile(r"h[1-6]|span\..*title|\.jobtitle", re.I))
                if h:
                    title = _clean(h.get_text())

        # Location: look for location-like content near the job link
        location = ""
        parent = a.find_parent(["li", "tr", "article"]) or a.find_parent("div")
        if parent:
            # Try common iCIMS location patterns
            loc_el = parent.find(class_=re.compile(r"location|city|address", re.I))
            if loc_el:
                location = _clean(loc_el.get_text())
            if not location:
                # Job cards label the location for screen readers, followed by
                # a sibling span containing the actual value.
                label = parent.find(
                    class_=re.compile(r"field-label", re.I),
                    string=re.compile(r"job locations?", re.I),
                )
                if label:
                    value = label.find_next_sibling()
                    if value:
                        location = _clean(value.get_text())
            if not location:
                # Fallback: look for text matching "City, ST" pattern in sibling text
                text = parent.get_text()
                loc_m = re.search(r'([A-Za-z ]+,\s*[A-Z]{2})', text)
                if loc_m:
                    location = _clean(loc_m.group(1))

        results.append((job_id, title, location))

    return results


def _fetch_icims_description(base_url: str, job_id: str) -> Tuple[str, str]:
    """
    Fetch a single iCIMS job detail page.
    Returns (description, location).
    """
    url = f"{base_url}/jobs/{job_id}/job"
    html = _get_html(url)
    if not html:
        return "", ""

    soup = BeautifulSoup(html, "lxml")
    desc = ""
    location = ""

    # Try common iCIMS job description containers
    for sel in [
        {"class_": re.compile(r"icims_content_description|job.?description|jobDescription", re.I)},
        {"id": re.compile(r"jobDescriptionText|job_description|description", re.I)},
        {"itemprop": "description"},
    ]:
        el = soup.find(**sel)
        if el:
            desc = _strip_html(str(el))
            break

    # Fallback: largest text block
    if not desc:
        candidates = [(len(el.get_text()), el) for el in soup.find_all(["div", "section"])
                      if len(el.get_text()) > 200]
        if candidates:
            desc = _strip_html(str(max(candidates, key=lambda x: x[0])[1]))

    # Location from detail page
    for sel in [
        {"class_": re.compile(r"location|city|address", re.I)},
        {"itemprop": "addressLocality"},
    ]:
        el = soup.find(**sel)
        if el:
            location = _clean(el.get_text())
            break

    return desc[:8000], location  # cap description length


def fetch_icims(company_name: str, slug: str) -> List[RawJob]:
    """
    Pull all admitted jobs from an iCIMS career board.

    Current iCIMS search endpoints frequently ignore searchKeyword and return
    the same paginated board for every term. Crawl the board exactly once and
    apply the shared role admission taxonomy locally; repeating 48 nominal
    searches made one small tenant take nearly an hour with no added coverage.
    """
    base_url = f"https://{slug}.icims.com"
    search_url = f"{base_url}/jobs/search"
    jobs: List[RawJob] = []
    seen_ids: set = set()

    page = 0
    while True:
        params = {
            "in_iframe": "1",
            "pr": str(page),
            "searchRelation": "keyword_all",
        }
        html = _get_html(search_url, params=params)
        time.sleep(REQUEST_DELAY)
        if not html:
            break

        listings = _parse_icims_job_ids(html, slug)
        new_listings = [listing for listing in listings if listing[0] not in seen_ids]
        log.debug(f"  iCIMS [{company_name}] page={page + 1}: {len(listings)} candidates")
        if page and not new_listings:
            break
        for job_id, title, location in new_listings:
            seen_ids.add(job_id)
            if not title or not _is_target_role(title):
                continue
            desc, detail_location = _fetch_icims_description(base_url, job_id)
            time.sleep(REQUEST_DELAY)

            if not location and detail_location:
                location = detail_location

            loc_lower = (location or "").lower()
            if "remote" in loc_lower or "virtual" in loc_lower:
                workplace_type = "remote"
            elif "hybrid" in loc_lower:
                workplace_type = "hybrid"
            else:
                workplace_type = None

            if normalize_location(location, workplace_type).should_drop:
                continue

            jobs.append(RawJob(
                source="icims", source_id=f"{slug}_{job_id}", title=title,
                company=company_name, location=location, description=desc,
                job_url=f"{base_url}/jobs/{job_id}/job",
                workplace_type=workplace_type,
                metadata={"slug": slug, "icims_job_id": job_id},
            ))

        if len(listings) < 50:
            break
        page += 1

    log.info(f"  iCIMS [{company_name}]: {len(jobs)} target roles found")
    return jobs


def fetch_all_icims() -> List[RawJob]:
    """Fetch all iCIMS companies, DB-first then hardcoded fallback."""
    companies = []
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            "SELECT company_name, board_token FROM discovered_companies "
            "WHERE ats_source = 'icims' AND enabled = true ORDER BY active_roles DESC"
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if rows:
            companies = [(r[0], r[1]) for r in rows]
            log.info(f"iCIMS: loaded {len(companies)} companies from DB")
    except Exception as e:
        log.debug(f"Could not load iCIMS companies from DB: {e}")

    if not companies:
        companies = ICIMS_COMPANIES
        log.info(f"iCIMS: using hardcoded list ({len(companies)} companies)")

    all_jobs: List[RawJob] = []
    for company_name, slug in companies:
        try:
            jobs = fetch_icims(company_name, slug)
            all_jobs.extend(jobs)
            time.sleep(0.5)
        except Exception as e:
            log.warning(f"  iCIMS [{company_name}] failed: {e}")

    log.info(f"iCIMS total: {len(all_jobs)} jobs")
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
            job_id   = _md5_id("J", f"{job.source}|{job.source_id}")
            desc     = job.description or ""
            desc_hash = hashlib.md5(desc.encode()).hexdigest()
            _loc     = normalize_location(job.location, job.workplace_type)

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
    log.info(f"iCIMS DB write: inserted={inserted} skipped={skipped} errors={errors}")

# ============================================================
# ENTRY POINT
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Harvest iCIMS job boards.")
    ap.add_argument("--apply", action="store_true", help="Write to DB")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true")
    ap.add_argument("--company", default=None, help="Only run for this slug")
    args = ap.parse_args()

    if args.company:
        match = [(n, s) for n, s in ICIMS_COMPANIES if s == args.company]
        companies = match or [(args.company.title(), args.company)]
        all_jobs = []
        for name, slug in companies:
            all_jobs.extend(fetch_icims(name, slug))
    else:
        all_jobs = fetch_all_icims()

    ingest_jobs_to_db(all_jobs, apply=args.apply)


if __name__ == "__main__":
    main()
