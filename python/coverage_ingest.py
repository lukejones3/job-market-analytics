#!/usr/bin/env python3
"""Opt-in direct coverage: USAJOBS, employer feeds, and JobPosting JSON-LD."""
from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import logging
import os
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

from ingest_jobs import RawJob, _parse_source_posted_date, get_conn, ingest_job, ensure_schema_columns
from location_normalizer import normalize_location
from role_taxonomy import SEARCH_TERMS, is_target_role

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
HEADERS = {"User-Agent": "LanderJobBot/1.0 contact: jones31luke@gmail.com"}
log = logging.getLogger(__name__)


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


def usajobs(term_delay_seconds: float | None = None) -> list[RawJob]:
    # Polite sweep: SEARCH_TERMS holds ~65 terms and each can page several
    # times. Sleep between terms (and lightly between pages) so a full sweep
    # never bursts the official API. Override with USAJOBS_TERM_DELAY_SECONDS
    # or --term-delay-seconds; 0 disables.
    import time

    if term_delay_seconds is None:
        term_delay_seconds = float(os.getenv("USAJOBS_TERM_DELAY_SECONDS", "1.0"))
    key, email = os.getenv("USAJOBS_API_KEY"), os.getenv("USAJOBS_EMAIL")
    if not key or not email:
        raise RuntimeError("USAJOBS_API_KEY and USAJOBS_EMAIL are required")
    headers = {**HEADERS, "Authorization-Key": key, "User-Agent": email}
    jobs, seen = [], set()
    for term in SEARCH_TERMS:
        if term_delay_seconds > 0:
            time.sleep(term_delay_seconds)
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
            if term_delay_seconds > 0:
                time.sleep(min(term_delay_seconds, 0.5))
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


@dataclass(frozen=True)
class EmployerFeedRowError:
    """One malformed feed row, retained for operator reporting."""

    row_number: int
    reason: str
    source_id: str | None = None


@dataclass
class EmployerFeedParseResult:
    """Employer-feed jobs plus row accounting.

    ``rows_ok`` counts rows converted to RawJobs before cross-row duplicate
    removal. ``rows_bad`` is deliberately separate from ``rows_non_target``:
    a non-target role is expected product filtering, not partner data damage.
    """

    jobs: list[RawJob] = field(default_factory=list)
    rows_seen: int = 0
    rows_ok: int = 0
    rows_bad: int = 0
    rows_non_target: int = 0
    errors: list[EmployerFeedRowError] = field(default_factory=list)


def _feed_rows(response, url: str, feed_format: str) -> list:
    requested = (feed_format or "auto").strip().lower()
    if requested not in {"auto", "json", "csv"}:
        raise ValueError(f"Unsupported employer-feed format: {feed_format}")
    content_type = response.headers.get("content-type", "").lower()
    url_path = url.split("?", 1)[0].lower()
    is_csv = requested == "csv" or (
        requested == "auto" and ("csv" in content_type or url_path.endswith(".csv"))
    )
    if is_csv:
        return list(csv.DictReader(io.StringIO(response.text)))
    payload = response.json()
    rows = payload.get("jobs", payload) if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("Employer-feed JSON must be a list or an object with a jobs list")
    return rows


def _parse_employer_feed_rows(
    rows: list,
    *,
    employer_name: str | None = None,
    tenant: str | None = None,
) -> EmployerFeedParseResult:
    result = EmployerFeedParseResult(rows_seen=len(rows))
    for row_number, row in enumerate(rows, start=1):
        source_id = ""
        try:
            if not isinstance(row, dict):
                raise ValueError("row is not a JSON/CSV object")
            title = _text(row.get("title")).strip()
            if not title:
                raise ValueError("missing title")
            if not is_target_role(title):
                result.rows_non_target += 1
                continue

            raw_source_id = row.get("id") or row.get("requisition_id")
            if isinstance(raw_source_id, (dict, list)):
                raise ValueError("id/requisition_id must be a scalar")
            source_id = str(raw_source_id or "").strip()
            if not source_id:
                raise ValueError("missing id/requisition_id")

            row_company = _text(row.get("company")).strip()
            company = (employer_name or row_company or "Unknown").strip()
            posted_raw = _text(row.get("posted_date") or row.get("date_posted")).strip()
            posted_date = _parse_source_posted_date(posted_raw) if posted_raw else None
            if posted_raw and not posted_date:
                raise ValueError(f"unparseable posted_date: {posted_raw[:40]!r}")

            metadata = {}
            if tenant:
                metadata["tenant"] = tenant
            if employer_name:
                metadata["hiring_organization"] = employer_name
                metadata["employer_feed_employer"] = employer_name
            # Manual feeds retain the historical company|requisition identity.
            # Registered feeds also namespace by tenant/partner because two
            # systems owned by one employer can reuse requisition numbers.
            identity_prefix = f"{company}|{tenant}" if tenant else company
            result.jobs.append(RawJob(
                source="employer_feed",
                source_id=f"{identity_prefix}|{source_id}",
                title=title,
                company=company,
                location=_text(row.get("location")) or None,
                description=_text(row.get("description")) or None,
                job_url=_text(row.get("url") or row.get("job_url") or row.get("apply_url")) or None,
                posted_date=posted_date,
                workplace_type=_text(row.get("workplace_type")) or None,
                employment_type=_text(row.get("employment_type")) or None,
                metadata=metadata,
            ))
            result.rows_ok += 1
        except Exception as exc:  # one partner row must never kill the feed
            result.rows_bad += 1
            error = EmployerFeedRowError(
                row_number=row_number,
                reason=str(exc)[:300],
                source_id=source_id or None,
            )
            result.errors.append(error)
            log.warning(
                "Skipping malformed employer-feed row %s (%s): %s",
                row_number,
                source_id or "no source id",
                error.reason,
            )
    return result


def employer_feed_with_report(
    url: str,
    *,
    employer_name: str | None = None,
    tenant: str | None = None,
    feed_format: str = "auto",
    auth_env_var: str = "EMPLOYER_FEED_TOKEN",
) -> EmployerFeedParseResult:
    """Fetch one feed, skipping malformed rows instead of failing the run."""
    headers = dict(HEADERS)
    if auth_env_var and os.getenv(auth_env_var):
        headers["Authorization"] = f"Bearer {os.environ[auth_env_var]}"
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()
    rows = _feed_rows(response, url, feed_format)
    return _parse_employer_feed_rows(
        rows,
        employer_name=employer_name,
        tenant=tenant,
    )


def employer_feed(url: str, **kwargs) -> list[RawJob]:
    """Backward-compatible list-only employer-feed fetch."""
    return employer_feed_with_report(url, **kwargs).jobs


def write(jobs: list[RawJob], apply: bool) -> dict[str, int]:
    unique = {(job.source, job.source_id): job for job in jobs
              if not normalize_location(job.location, job.workplace_type).should_drop}
    if not apply:
        print(f"Would ingest {len(unique)} unique target postings")
        return {"unique": len(unique), "written": 0}
    with get_conn() as conn, conn.cursor() as cur:
        ensure_schema_columns(cur)
        inserted = sum(bool(ingest_job(cur, job)) for job in unique.values())
    print(f"Processed {len(unique)} postings; inserted {inserted}")
    return {"unique": len(unique), "written": inserted}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("source", choices=("usajobs", "adzuna", "jsonld", "feed"))
    parser.add_argument("--url", action="append", default=[])
    parser.add_argument("--sitemap", action="append", default=[])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--term-delay-seconds", type=float, default=None,
                        help="Polite delay between USAJobs search terms (default: env USAJOBS_TERM_DELAY_SECONDS or 1.0)")
    args = parser.parse_args()
    if args.source == "usajobs":
        jobs = usajobs(term_delay_seconds=args.term_delay_seconds)
    elif args.source == "adzuna":
        jobs = adzuna()
    elif args.source == "feed":
        jobs = []
        for url in args.url:
            report = employer_feed_with_report(url)
            jobs.extend(report.jobs)
            print(
                f"Employer feed {url}: rows_seen={report.rows_seen} "
                f"rows_ok={report.rows_ok} rows_bad={report.rows_bad} "
                f"rows_non_target={report.rows_non_target}"
            )
    else:
        jobs = jsonld_jobs([*args.url, *sitemap_pages(args.sitemap)])
    write(jobs, args.apply)


if __name__ == "__main__":
    main()
