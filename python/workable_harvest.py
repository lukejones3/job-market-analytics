#!/usr/bin/env python3
"""
workable_harvest.py

Harvests data/ML job postings from the global Workable job board.

Uses the jobs.workable.com search API which covers ALL ~50k companies
on Workable — no curated company list needed.

API:  GET https://jobs.workable.com/api/v1/jobs?query={keyword}&limit=50
Pagination via nextPageToken.

Usage (standalone):
    python python/workable_harvest.py --dry-run
    python python/workable_harvest.py --apply

Cron:
    30 1 * * * cd ~/github/job-market-analytics && python python/workable_harvest.py --apply >> logs/workable.log 2>&1
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
from typing import Dict, List, Optional

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

REQUEST_DELAY = 0.4
REQUEST_TIMEOUT = 15
BASE_URL = "https://jobs.workable.com/api/v1/jobs"
PAGE_SIZE = 20           # Workable global API hard limit is 20 per page
MAX_PAGES_PER_TERM = 250  # loop guard only; pagination normally ends on nextPageToken

USER_AGENT = "LanderJobBot/1.0 contact: jones31luke@gmail.com"

SEARCH_TERMS = list(SEARCH_TERMS)

US_COUNTRY_NAMES = {
    "united states", "usa", "us", "u.s.", "u.s.a.",
    "united states of america",
}

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

# ============================================================
# WORKABLE GLOBAL SEARCH
# ============================================================

def _is_us_job(job: dict) -> bool:
    """Return True if job is US-based or fully remote with no country restriction."""
    wp = (job.get("workplace", "") or "").lower()
    loc = job.get("location") or {}
    country = (loc.get("countryName", "") or "").lower().strip()
    locations = job.get("locations", [])

    # Fully remote with no specific country → allow (assumed US remote by Workable US-hosted companies)
    if wp == "remote" and not country:
        return True

    # Country explicitly US
    if country in US_COUNTRY_NAMES:
        return True

    # Check locations list for TELECOMMUTE + no foreign country
    if "TELECOMMUTE" in locations and not country:
        return True

    return False


def _parse_workplace(job: dict) -> Optional[str]:
    wp = (job.get("workplace", "") or "").lower()
    if wp == "remote":
        return "remote"
    if wp == "hybrid":
        return "hybrid"
    if wp in ("on_site", "onsite", "on-site"):
        return "onsite"
    return None


def _parse_employment_type(job: dict) -> Optional[str]:
    et = (job.get("employmentType", "") or "").lower()
    if "full" in et:
        return "full-time"
    if "part" in et:
        return "part-time"
    if "contract" in et:
        return "contract"
    return None


def fetch_workable_for_term(search_term: str) -> List[RawJob]:
    """
    Fetch all US data/ML jobs for a single search term via global Workable search.
    """
    jobs: List[RawJob] = []
    seen_ids: set = set()
    next_token: Optional[str] = None
    seen_tokens: set = set()
    page = 0

    while page < MAX_PAGES_PER_TERM:
        params: Dict = {"query": search_term, "limit": PAGE_SIZE}
        if next_token:
            params["nextPageToken"] = next_token

        try:
            r = requests.get(
                BASE_URL, params=params, timeout=REQUEST_TIMEOUT,
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
            )
        except requests.RequestException as e:
            log.warning(f"  Workable [{search_term}] request error: {e}")
            break

        time.sleep(REQUEST_DELAY)

        if r.status_code != 200:
            log.debug(f"  Workable [{search_term}] HTTP {r.status_code}")
            break

        data = r.json()
        batch = data.get("jobs", [])
        if not batch:
            break

        for j in batch:
            job_id = j.get("id", "")
            if not job_id or job_id in seen_ids:
                continue
            seen_ids.add(job_id)

            title = _clean(j.get("title", ""))
            if not title or not _is_target_role(title):
                continue

            if not _is_us_job(j):
                continue

            # Company
            company_obj = j.get("company") or {}
            company_name = _clean(company_obj.get("title", "") or "Unknown")

            # Location
            loc_obj = j.get("location") or {}
            city    = _clean(loc_obj.get("city", "") or "")
            subregion = _clean(loc_obj.get("subregion", "") or "")
            country = loc_obj.get("countryName", "")
            if city and subregion:
                location = f"{city}, {subregion}"
            elif city:
                location = city
            elif (j.get("workplace", "") or "").lower() == "remote":
                location = "Remote"
            else:
                location = ""

            workplace_type  = _parse_workplace(j)
            employment_type = _parse_employment_type(j)

            if normalize_location(location, workplace_type).should_drop:
                continue

            # Description — HTML from API
            desc_html = j.get("description", "") or ""
            desc_parts = [_strip_html(desc_html) if desc_html else ""]
            for section_key in ("requirementsSection", "benefitsSection"):
                sec = j.get(section_key, "") or ""
                if sec:
                    desc_parts.append(_strip_html(sec))
            description = "\n\n".join(p for p in desc_parts if p).strip()

            # URL — direct job listing
            job_url = j.get("url", "") or ""

            # Posted date from `created`
            posted_date = None
            created = j.get("created", "") or ""
            if created and len(created) >= 10:
                posted_date = created[:10]

            jobs.append(RawJob(
                source="workable",
                source_id=job_id,
                title=title,
                company=company_name,
                location=location,
                description=description,
                job_url=job_url,
                workplace_type=workplace_type,
                employment_type=employment_type,
                posted_date=posted_date,
                metadata={"search_term": search_term, "workable_company_id": company_obj.get("id", "")},
            ))

        next_token = data.get("nextPageToken")
        if not next_token:
            break
        if next_token in seen_tokens:
            log.warning(f"  Workable [{search_term}] repeated a pagination token; stopping safely")
            break
        seen_tokens.add(next_token)
        page += 1

    if page >= MAX_PAGES_PER_TERM:
        log.warning(f"  Workable [{search_term}] hit the {MAX_PAGES_PER_TERM}-page safety limit")

    log.debug(f"  Workable term='{search_term}': {len(jobs)} US target roles")
    return jobs


def fetch_all_workable() -> List[RawJob]:
    """Fetch all data/ML jobs from Workable across all search terms, deduplicated."""
    all_jobs: List[RawJob] = []
    seen_ids: set = set()

    for term in SEARCH_TERMS:
        log.info(f"  Workable searching: '{term}'")
        try:
            jobs = fetch_workable_for_term(term)
            for j in jobs:
                if j.source_id not in seen_ids:
                    seen_ids.add(j.source_id)
                    all_jobs.append(j)
        except Exception as e:
            log.warning(f"  Workable [{term}] failed: {e}")

    log.info(f"Workable total: {len(all_jobs)} unique US target roles")
    return all_jobs

# ============================================================
# DB WRITE (standalone mode)
# ============================================================

def ingest_jobs_to_db(jobs: List[RawJob], apply: bool) -> None:
    if not apply:
        log.info(f"DRY RUN — {len(jobs)} jobs would be inserted. Sample:")
        for j in jobs[:8]:
            log.info(f"  [{j.source}] {j.company} — {j.title} | {j.location} | {j.workplace_type}")
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
    log.info(f"Workable DB write: inserted={inserted} skipped={skipped} errors={errors}")

# ============================================================
# ENTRY POINT
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Harvest Workable jobs via global search API.")
    ap.add_argument("--apply", action="store_true", help="Write to DB")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", help="Preview only (default)")
    ap.add_argument("--term", default=None, help="Only search for this term")
    args = ap.parse_args()

    if args.term:
        all_jobs = fetch_workable_for_term(args.term)
    else:
        all_jobs = fetch_all_workable()

    ingest_jobs_to_db(all_jobs, apply=args.apply)


if __name__ == "__main__":
    main()
