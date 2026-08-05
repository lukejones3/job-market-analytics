#!/usr/bin/env python3
"""Harvest public BambooHR boards discovered by the ATS tenant pipeline."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import logging
import os

import psycopg2
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from role_taxonomy import is_target_role

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "LanderJobBot/1.0 contact: jones31luke@gmail.com", "Accept": "application/json"}
US_MARKERS = {"united states", "us", "usa"}


@dataclass
class RawJob:
    source: str
    source_id: str
    title: str
    company: str
    location: Optional[str] = None
    description: Optional[str] = None
    job_url: Optional[str] = None
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    salary_period: Optional[str] = None
    workplace_type: Optional[str] = None
    employment_type: Optional[str] = None
    posted_date: Optional[str] = None
    remote: bool = False
    metadata: Dict = field(default_factory=dict)


def _connection():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def _is_us(opening: dict, remote: bool) -> bool:
    location = opening.get("location") or {}
    ats_location = opening.get("atsLocation") or {}
    country = str(ats_location.get("country") or location.get("addressCountry") or "").lower()
    state = str(ats_location.get("state") or ats_location.get("province") or location.get("state") or "").upper()
    return remote or country in US_MARKERS or (len(state) == 2 and state not in {"ON", "BC", "AB", "QC", "MB", "SK", "NS", "NB", "NL", "PE", "NT", "NU", "YT"})


def fetch_company(company: str, tenant: str) -> List[RawJob]:
    base = f"https://{tenant}.bamboohr.com/careers"
    response = requests.get(f"{base}/list", headers=HEADERS, timeout=25)
    response.raise_for_status()
    listings = response.json().get("result", [])
    jobs: List[RawJob] = []
    for listing in listings:
        title = str(listing.get("jobOpeningName") or "")
        if not title or not is_target_role(title):
            continue
        job_id = str(listing.get("id") or "")
        if not job_id:
            continue
        try:
            detail_response = requests.get(f"{base}/{job_id}/detail", headers=HEADERS, timeout=20)
            detail_response.raise_for_status()
            opening = detail_response.json().get("result", {}).get("jobOpening", {})
        except (requests.RequestException, ValueError):
            opening = listing
        remote = bool(opening.get("isRemote")) or str(opening.get("locationType")) == "1"
        if not _is_us(opening, remote):
            continue
        location = opening.get("location") or {}
        location_text = "Remote" if remote else ", ".join(filter(None, [
            location.get("city"), location.get("state"), location.get("addressCountry")]))
        description = BeautifulSoup(str(opening.get("description") or ""), "html.parser").get_text("\n", strip=True)
        jobs.append(RawJob(
            source="bamboohr", source_id=f"{tenant}|{job_id}", title=title,
            company=company or tenant, location=location_text, description=description,
            job_url=opening.get("jobOpeningShareUrl") or f"{base}/{job_id}",
            posted_date=str(opening.get("datePosted") or "")[:10] or None,
            employment_type=opening.get("employmentStatusLabel") or opening.get("employmentType"),
            workplace_type="remote" if remote else None, remote=remote,
            metadata={"tenant": tenant, "compensation_text": opening.get("compensation")},
        ))
    return jobs


def fetch_all_bamboohr() -> List[RawJob]:
    with _connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT company_name, board_token FROM discovered_companies
            WHERE ats_source='bamboohr' AND enabled=true ORDER BY active_roles DESC""")
        companies = cur.fetchall()
    jobs: Dict[str, RawJob] = {}
    for company, tenant in companies:
        try:
            for job in fetch_company(company, tenant):
                jobs[job.source_id] = job
        except Exception as exc:
            log.warning("BambooHR [%s] failed: %s", tenant, exc)
    log.info("BambooHR total: %d unique US target roles from %d tenants", len(jobs), len(companies))
    return list(jobs.values())
