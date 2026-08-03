#!/usr/bin/env python3
"""Opt-in direct coverage: USAJOBS, employer feeds, and JobPosting JSON-LD."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from ingest_jobs import RawJob, get_conn, ingest_job, ensure_schema_columns
from location_normalizer import normalize_location
from role_taxonomy import SEARCH_TERMS, is_target_role

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
HEADERS = {"User-Agent": "LanderJobBot/1.0 contact: jones31luke@gmail.com"}


def _text(value) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, (_text(item) for item in value)))
    return str(value or "")


def _location(data: dict) -> str:
    if data.get("jobLocationType") == "TELECOMMUTE":
        return "Remote"
    addresses = []
    for place in data.get("jobLocation", []) if isinstance(data.get("jobLocation"), list) else [data.get("jobLocation")]:
        address = (place or {}).get("address", {}) if isinstance(place, dict) else {}
        addresses.append(", ".join(filter(None, [address.get("addressLocality"),
            address.get("addressRegion"), address.get("addressCountry")])))
    return next((address for address in addresses if address), "")


def _jsonld_objects(value):
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)
    elif isinstance(value, dict):
        if value.get("@type") == "JobPosting" or "JobPosting" in (value.get("@type") or []):
            yield value
        yield from _jsonld_objects(value.get("@graph", []))


def jsonld_jobs(page_urls: Iterable[str]) -> list[RawJob]:
    jobs = []
    for url in page_urls:
        response = requests.get(url, headers=HEADERS, timeout=20)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, "html.parser")
        for node in soup.select("script[type='application/ld+json']"):
            try:
                values = list(_jsonld_objects(json.loads(node.string or "{}")))
            except (TypeError, json.JSONDecodeError):
                continue
            for data in values:
                title = _text(data.get("title"))
                if not is_target_role(title):
                    continue
                identifier = data.get("identifier") or {}
                source_id = _text(identifier.get("value") if isinstance(identifier, dict) else identifier)
                source_id = source_id or hashlib.sha256(url.encode()).hexdigest()[:24]
                company = _text((data.get("hiringOrganization") or {}).get("name")) or "Unknown"
                jobs.append(RawJob(source="jsonld", source_id=source_id, title=title,
                    company=company, location=_location(data), description=_text(data.get("description")),
                    job_url=data.get("url") or url, posted_date=_text(data.get("datePosted"))[:10] or None,
                    employment_type=_text(data.get("employmentType")) or None,
                    workplace_type="remote" if data.get("jobLocationType") == "TELECOMMUTE" else None))
    return jobs


def sitemap_pages(urls: Iterable[str]) -> list[str]:
    pages = []
    for url in urls:
        response = requests.get(url, headers=HEADERS, timeout=30)
        response.raise_for_status()
        root = ET.fromstring(response.content)
        pages.extend(el.text.strip() for el in root.iter() if el.tag.endswith("loc") and el.text)
    return pages


def usajobs() -> list[RawJob]:
    key, email = os.getenv("USAJOBS_API_KEY"), os.getenv("USAJOBS_EMAIL")
    if not key or not email:
        raise RuntimeError("USAJOBS_API_KEY and USAJOBS_EMAIL are required")
    headers = {**HEADERS, "Authorization-Key": key, "User-Agent": email}
    jobs, seen = [], set()
    for term in SEARCH_TERMS:
        page = 1
        while True:
            response = requests.get("https://data.usajobs.gov/api/search",
                params={"Keyword": term, "Page": page, "ResultsPerPage": 500},
                headers=headers, timeout=30)
            response.raise_for_status()
            result = response.json().get("SearchResult", {})
            items = result.get("SearchResultItems", [])
            for item in items:
                descriptor = item.get("MatchedObjectDescriptor", {})
                source_id = str(descriptor.get("PositionID") or "")
                title = descriptor.get("PositionTitle") or ""
                if not source_id or source_id in seen or not is_target_role(title):
                    continue
                seen.add(source_id)
                details = descriptor.get("UserArea", {}).get("Details", {})
                jobs.append(RawJob(source="usajobs", source_id=source_id, title=title,
                    company=descriptor.get("OrganizationName") or descriptor.get("DepartmentName") or "US Government",
                    location=_text(descriptor.get("PositionLocationDisplay")),
                    description=_text(details.get("JobSummary") or descriptor.get("QualificationSummary")),
                    job_url=descriptor.get("PositionURI"),
                    posted_date=_text(descriptor.get("PublicationStartDate"))[:10] or None,
                    workplace_type="remote" if details.get("RemoteIndicator") else None))
            total_pages = int(result.get("UserArea", {}).get("NumberOfPages") or 1)
            if page >= total_pages:
                break
            page += 1
    return jobs


def adzuna() -> list[RawJob]:
    """Licensed aggregator backstop; ingest_job keeps these records in Tier 2."""
    app_id, app_key = os.getenv("ADZUNA_APP_ID"), os.getenv("ADZUNA_APP_KEY")
    if not app_id or not app_key:
        raise RuntimeError("ADZUNA_APP_ID and ADZUNA_APP_KEY are required")
    jobs, seen = [], set()
    for term in SEARCH_TERMS:
        page = 1
        while True:
            response = requests.get(f"https://api.adzuna.com/v1/api/jobs/us/search/{page}",
                params={"app_id": app_id, "app_key": app_key, "what": term,
                        "results_per_page": 50, "content-type": "application/json"},
                headers=HEADERS, timeout=30)
            response.raise_for_status()
            payload = response.json()
            items = payload.get("results", [])
            for item in items:
                source_id, title = str(item.get("id") or ""), item.get("title") or ""
                if not source_id or source_id in seen or not is_target_role(title):
                    continue
                seen.add(source_id)
                location = _text((item.get("location") or {}).get("display_name"))
                jobs.append(RawJob(source="adzuna", source_id=source_id, title=title,
                    company=_text((item.get("company") or {}).get("display_name")) or "Unknown",
                    location=location, description=item.get("description"),
                    job_url=item.get("redirect_url"), posted_date=_text(item.get("created"))[:10] or None,
                    salary_min=item.get("salary_min"), salary_max=item.get("salary_max"),
                    salary_period="year" if item.get("salary_is_predicted") is not None else None,
                    workplace_type="remote" if "remote" in (title + " " + location).lower() else None))
            if not items or page * 50 >= int(payload.get("count") or 0):
                break
            page += 1
    return jobs


def employer_feed(url: str) -> list[RawJob]:
    headers = dict(HEADERS)
    if os.getenv("EMPLOYER_FEED_TOKEN"):
        headers["Authorization"] = f"Bearer {os.environ['EMPLOYER_FEED_TOKEN']}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    if "csv" in response.headers.get("content-type", "") or url.lower().endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(response.text)))
    else:
        payload = response.json()
        rows = payload.get("jobs", payload) if isinstance(payload, dict) else payload
    jobs = []
    for row in rows:
        title = row.get("title", "")
        if not is_target_role(title):
            continue
        source_id = str(row.get("id") or row.get("requisition_id") or "")
        if not source_id:
            raise ValueError("Every employer-feed row needs id or requisition_id")
        company = row.get("company") or "Unknown"
        jobs.append(RawJob(source="employer_feed", source_id=f"{company}|{source_id}", title=title,
            company=company, location=row.get("location"),
            description=row.get("description"), job_url=row.get("url"),
            posted_date=(row.get("posted_date") or "")[:10] or None,
            workplace_type=row.get("workplace_type"), employment_type=row.get("employment_type")))
    return jobs


def write(jobs: list[RawJob], apply: bool):
    unique = {(job.source, job.source_id): job for job in jobs
              if not normalize_location(job.location, job.workplace_type).should_drop}
    if not apply:
        print(f"Would ingest {len(unique)} unique target postings")
        return
    with get_conn() as conn, conn.cursor() as cur:
        ensure_schema_columns(cur)
        inserted = sum(bool(ingest_job(cur, job)) for job in unique.values())
    print(f"Processed {len(unique)} postings; inserted {inserted}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=("usajobs", "adzuna", "jsonld", "feed"))
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--sitemap", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.source == "usajobs":
        jobs = usajobs()
    elif args.source == "adzuna":
        jobs = adzuna()
    elif args.source == "feed":
        jobs = [job for url in args.url for job in employer_feed(url)]
    else:
        jobs = jsonld_jobs([*args.url, *sitemap_pages(args.sitemap)])
    write(jobs, args.apply)


if __name__ == "__main__":
    main()
