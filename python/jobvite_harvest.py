#!/usr/bin/env python3
"""Harvest public Jobvite boards discovered by the ATS tenant pipeline."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional
import json
import logging
import os
import re

import psycopg2
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from role_taxonomy import is_target_role
from crawl_observability import record_failure, record_success

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
log = logging.getLogger(__name__)
HEADERS = {"User-Agent": "LanderJobBot/1.0 contact: jones31luke@gmail.com"}
US_STATES = {"AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD","MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC","SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC"}


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


def _jobposting(soup: BeautifulSoup) -> dict:
    for node in soup.select("script[type='application/ld+json']"):
        try:
            data = json.loads(node.string or "{}")
        except (TypeError, json.JSONDecodeError):
            continue
        values = data if isinstance(data, list) else [data]
        for value in values:
            if isinstance(value, dict) and value.get("@type") == "JobPosting":
                return value
    return {}


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    return str(value or "")


def _remote_allows_us(data: dict) -> bool:
    requirements = data.get("applicantLocationRequirements") or []
    if not isinstance(requirements, list):
        requirements = [requirements]
    names = {_text(item).strip().lower() for item in requirements}
    return bool(names & {"united states", "united states of america", "usa", "us", "u.s.", "u.s.a."})


def fetch_company(company: str, tenant: str) -> List[RawJob]:
    listing_url = f"https://jobs.jobvite.com/{tenant}/jobs"
    response = requests.get(listing_url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    links: Dict[str, str] = {}
    for anchor in soup.select("a[href*='/job/']"):
        title = anchor.get_text(" ", strip=True)
        href = anchor.get("href") or ""
        if title and href and is_target_role(title):
            url = requests.compat.urljoin(listing_url, href)
            links[url] = title

    jobs: List[RawJob] = []
    detail_errors = 0
    for url, fallback_title in links.items():
        try:
            detail = requests.get(url, headers=HEADERS, timeout=20)
            if detail.status_code != 200:
                detail_errors += 1
                continue
            detail_soup = BeautifulSoup(detail.text, "html.parser")
            data = _jobposting(detail_soup)
            title = _text(data.get("title")) or fallback_title
            if not is_target_role(title):
                continue
            identifier = data.get("identifier") or {}
            source_id = _text(identifier.get("value") if isinstance(identifier, dict) else identifier)
            source_id = source_id or url.rstrip("/").split("/")[-1]
            org = data.get("hiringOrganization") or {}
            location = data.get("jobLocation") or {}
            if isinstance(location, list):
                location = location[0] if location else {}
            address = location.get("address", {}) if isinstance(location, dict) else {}
            loc = ", ".join(filter(None, [address.get("addressLocality"), address.get("addressRegion"), address.get("addressCountry")]))
            remote = data.get("jobLocationType") == "TELECOMMUTE"
            country = str(address.get("addressCountry") or "").lower()
            region = str(address.get("addressRegion") or "").upper()
            remote_us = remote and _remote_allows_us(data)
            physical_us = country in {"us", "usa", "united states"} or region in US_STATES
            if not remote_us and not physical_us:
                continue
            location_text = "Remote, United States" if remote_us else loc
            jobs.append(RawJob(
                source="jobvite", source_id=f"{tenant}|{source_id}", title=title,
                company=_text(org.get("name")) or company, location=location_text,
                description=BeautifulSoup(str(data.get("description") or ""), "html.parser").get_text("\n", strip=True),
                job_url=url, posted_date=_text(data.get("datePosted"))[:10] or None,
                employment_type=_text(data.get("employmentType")) or None,
                workplace_type="remote" if remote else None,
                metadata={
                    "tenant": tenant,
                    "hiring_organization": _text(org.get("name")) or company,
                    "valid_through": _text(data.get("validThrough")) or None,
                    "direct_apply": data.get("directApply"),
                    "location_evidence": {
                        "country": "US",
                        "kind": "applicantLocationRequirements" if remote_us else "jobLocation",
                    },
                },
            ))
        except requests.RequestException:
            detail_errors += 1
            continue
    if detail_errors:
        record_failure(
            "jobvite", tenant,
            f"{detail_errors}/{len(links)} target detail pages failed",
            partial=detail_errors < len(links),
        )
    return jobs


def fetch_all_jobvite() -> List[RawJob]:
    with _connection() as conn, conn.cursor() as cur:
        cur.execute("""SELECT company_name, board_token FROM discovered_companies
            WHERE ats_source='jobvite' AND enabled=true ORDER BY active_roles DESC""")
        companies = cur.fetchall()
    jobs: Dict[str, RawJob] = {}
    for company, tenant in companies:
        try:
            fetched = fetch_company(company, tenant)
            record_success("jobvite", tenant, len(fetched))
            for job in fetched:
                jobs[job.source_id] = job
        except Exception as exc:
            log.warning("Jobvite [%s] failed: %s", tenant, exc)
            record_failure("jobvite", tenant, str(exc))
    log.info("Jobvite total: %d unique US target roles from %d tenants", len(jobs), len(companies))
    return list(jobs.values())
