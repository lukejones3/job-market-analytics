#!/usr/bin/env python3
"""Employer-first career-host discovery, routing, and direct ingestion.

Search and archive data are evidence only. A host remains in shadow until its
leaf pages prove employer identity, target-role scope, explicit US eligibility,
content quality, and lifecycle completeness. Shadow jobs are ingested for the
normal enrichment pipeline but cannot cross the publication boundary.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import time
from typing import Any, Iterable, Iterator, Optional
import unicodedata
from urllib.parse import urljoin, urlparse
import xml.etree.ElementTree as ET

from bs4 import BeautifulSoup
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import Json, RealDictCursor
import requests

from company_blocklist import is_company_blocked
from ingest_jobs import RawJob, ensure_schema_columns, ingest_job
from role_taxonomy import is_target_role


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
log = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

USER_AGENT = os.getenv("LANDER_CRAWLER_USER_AGENT", "LanderJobBot/1.0 contact: luke@landerjob.com")
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json,application/xml;q=0.9,*/*;q=0.7",
}
SERPER_URL = "https://google.serper.dev/search"
REQUEST_TIMEOUT = 25
MAX_RESPONSE_BYTES = 12_000_000
US_COUNTRIES = {"us", "usa", "united states", "united states of america", "u.s.", "u.s.a."}
US_STATES = {
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA",
    "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ",
    "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT",
    "VA", "WA", "WV", "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
}
AGGREGATOR_HOSTS = {
    "indeed.com", "linkedin.com", "glassdoor.com", "ziprecruiter.com", "monster.com", "careerbuilder.com",
    "builtin.com", "builtinsf.com", "builtinboston.com", "levels.fyi", "talent.com", "jooble.org",
    "simplyhired.com", "lensa.com", "adzuna.com", "jobcase.com", "learn4good.com", "grabjobs.co",
    "clearancejobs.com", "sciencecareers.org", "healthecareers.com", "consider.com", "jobaaj.com",
    "dejobs.org", "nexxt.com", "virtualvocations.com", "haystackapp.io", "productmanagerjobboard.com",
    "governmentresource.com",
}
NON_HTML_RESULT_SUFFIXES = (
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip",
)
COMPANY_STOPWORDS = {
    "inc", "incorporated", "corp", "corporation", "llc", "ltd", "limited", "plc", "company", "companies",
    "co", "group", "holdings", "holding", "technologies", "technology", "services", "solutions", "global",
    "international", "partners", "enterprises", "the", "and",
}


@dataclass
class Fingerprint:
    platform: str
    url: str
    tenant_token: Optional[str]
    strategy: str
    server: Optional[str] = None


@dataclass
class CrawlStats:
    pages_discovered: int = 0
    pages_fetched: int = 0
    postings_parsed: int = 0
    target_jobs: int = 0
    explicit_us_jobs: int = 0
    accepted_jobs: int = 0
    written_jobs: int = 0
    duplicate_jobs: int = 0
    identity_mismatches: int = 0
    foreign_rejections: int = 0
    quality_rejections: int = 0
    errors: int = 0
    rejection_reasons: Counter[str] = field(default_factory=Counter)


def connection():
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def migrate() -> None:
    with connection() as conn, conn.cursor() as cur:
        cur.execute((ROOT / "sql/career_host_engine.sql").read_text(encoding="utf-8"))
        cur.execute((ROOT / "sql/publication_boundary.sql").read_text(encoding="utf-8"))


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value or "").encode("ascii", "ignore").decode("ascii")


def company_key(name: str) -> str:
    words = re.findall(r"[a-z0-9]+", _ascii(name).lower())
    meaningful = [word for word in words if word not in COMPANY_STOPWORDS]
    return "-".join(meaningful or words)[:160]


def _company_tokens(name: str) -> list[str]:
    return [token for token in company_key(name).split("-") if len(token) >= 3]


def _hostname(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _registeredish_domain(host: str) -> str:
    parts = host.lower().split(".")
    if len(parts) <= 2:
        return host.lower()
    if ".".join(parts[-2:]) in {"co.uk", "com.au", "co.nz", "com.br", "co.ca"} and len(parts) >= 3:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def is_aggregator(url: str) -> bool:
    domain = _registeredish_domain(_hostname(url))
    return domain in AGGREGATOR_HOSTS or any(domain.endswith(f".{blocked}") for blocked in AGGREGATOR_HOSTS)


def is_blocked_result(url: str) -> bool:
    path = urlparse(url).path.lower().rstrip("/")
    return is_aggregator(url) or path.endswith(NON_HTML_RESULT_SUFFIXES)


def quarantine_blocked_hosts(cur, *, apply: bool) -> int:
    cur.execute(
        """SELECT host_id,careers_url FROM career_hosts
           WHERE status IN ('shadow','active')"""
    )
    blocked_ids = [row["host_id"] for row in cur.fetchall() if is_blocked_result(row["careers_url"])]
    if apply and blocked_ids:
        cur.execute(
            """UPDATE career_hosts SET status='quarantined',identity_status='needs_review',
                      evidence=evidence || '{"resolver_blocked": true}'::jsonb,updated_at=now()
               WHERE host_id=ANY(%s)""",
            (blocked_ids,),
        )
        cur.execute(
            """UPDATE job_postings SET source_quality_status='quarantine',is_public=false
               WHERE crawl_tenant=ANY(%s)
                 AND ingestion_source IN ('career_site','oracle_cloud')""",
            (blocked_ids,),
        )
    return len(blocked_ids)


def _safe_get(session: requests.Session, url: str, **kwargs) -> requests.Response:
    response = session.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True, **kwargs)
    response.raise_for_status()
    content_length = int(response.headers.get("Content-Length") or 0)
    if content_length > MAX_RESPONSE_BYTES or len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError(f"response too large: {url}")
    return response


def _text(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("value") or value.get("addressCountry") or "")
    if isinstance(value, list):
        return ", ".join(filter(None, (_text(item) for item in value)))
    return str(value or "")


def _jsonld_objects(value: Any) -> Iterator[dict]:
    if isinstance(value, list):
        for item in value:
            yield from _jsonld_objects(item)
    elif isinstance(value, dict):
        types = value.get("@type") or []
        types = [types] if isinstance(types, str) else types
        if "JobPosting" in types:
            yield value
        yield from _jsonld_objects(value.get("@graph") or [])


def jobposting_objects(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    postings: list[dict] = []
    for node in soup.select("script[type='application/ld+json']"):
        try:
            postings.extend(_jsonld_objects(json.loads(node.string or node.get_text() or "{}")))
        except (TypeError, json.JSONDecodeError):
            continue
    return postings


def _all_urls(base_url: str, html: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    return [urljoin(base_url, href) for node in soup.select("a[href]") if (href := node.get("href"))]


ROUTED_PLATFORMS = {
    "greenhouse", "lever", "ashby", "workday", "smartrecruiters", "workable", "icims", "taleo",
    "eightfold", "jobvite", "bamboohr",
}


def fingerprint_url(url: str) -> Optional[Fingerprint]:
    patterns: list[tuple[str, re.Pattern[str], str]] = [
        ("workday", re.compile(r"https?://([a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", re.I), "ats_router"),
        ("greenhouse", re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/([^/?#]+)", re.I), "ats_router"),
        ("lever", re.compile(r"https?://jobs\.lever\.co/([^/?#]+)", re.I), "ats_router"),
        ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([^/?#]+)", re.I), "ats_router"),
        ("smartrecruiters", re.compile(r"https?://(?:careers|jobs)\.smartrecruiters\.com/([^/?#]+)", re.I), "ats_router"),
        ("workable", re.compile(r"https?://apply\.workable\.com/([^/?#]+)", re.I), "ats_router"),
        ("icims", re.compile(r"https?://([a-z0-9_-]+)\.icims\.com", re.I), "ats_router"),
        ("taleo", re.compile(r"https?://([a-z0-9_-]+)\.taleo\.net(?:/careersection/([^/?#]+))?", re.I), "ats_router"),
        ("eightfold", re.compile(r"https?://([a-z0-9_-]+)\.eightfold\.ai", re.I), "ats_router"),
        ("jobvite", re.compile(r"https?://jobs\.jobvite\.com/([^/?#]+)", re.I), "ats_router"),
        ("bamboohr", re.compile(r"https?://([a-z0-9_-]+)\.bamboohr\.com/careers", re.I), "ats_router"),
        ("oracle_cloud", re.compile(r"https?://[^/]+/hcmUI/CandidateExperience/(?:[^/]+/)?sites/([^/?#]+)", re.I), "oracle_cloud"),
    ]
    for platform, pattern, strategy in patterns:
        match = pattern.search(url)
        if not match:
            continue
        token = match.group(1).lower()
        server = None
        if platform == "workday":
            server = f"{match.group(2).lower()}/{match.group(3)}"
        elif platform == "taleo" and match.lastindex and match.lastindex > 1 and match.group(2):
            token = f"{token}/{match.group(2)}"
        return Fingerprint(platform, match.group(0), token, strategy, server)
    host = _hostname(url)
    lower = url.lower()
    if "avature" in host or "avature" in lower:
        return Fingerprint("avature", url, host, "sitemap_jsonld")
    if "phenom" in host or "phenompeople" in lower:
        return Fingerprint("phenom", url, host, "sitemap_jsonld")
    if "radancy" in host or "tmpworldwide" in host:
        return Fingerprint("radancy", url, host, "sitemap_jsonld")
    if "successfactors" in host:
        return Fingerprint("successfactors", url, host.split(".", 1)[0], "sitemap_jsonld")
    if "ukg" in host or "ultipro" in host:
        return Fingerprint("ukg", url, host, "sitemap_jsonld")
    if "paylocity" in host:
        return Fingerprint("paylocity", url, host, "sitemap_jsonld")
    if "pageuppeople" in host or "pageup" in host:
        return Fingerprint("pageup", url, host, "sitemap_jsonld")
    return None


def fingerprint_page(page_url: str, html: str) -> Fingerprint:
    candidates = [page_url, *_all_urls(page_url, html)]
    fingerprints = [fingerprint for candidate in candidates if (fingerprint := fingerprint_url(candidate))]
    if fingerprints:
        fingerprints.sort(key=lambda fp: (fp.strategy != "ats_router", fp.strategy != "oracle_cloud"))
        return fingerprints[0]
    return Fingerprint("custom", page_url, _hostname(page_url), "sitemap_jsonld")


def _score_search_result(company_name: str, result: dict, rank: int) -> float:
    link = str(result.get("link") or "")
    if not link.startswith("http") or is_blocked_result(link):
        return -1
    host = _hostname(link)
    path = urlparse(link).path.lower()
    combined = " ".join(str(result.get(key) or "") for key in ("title", "snippet")).lower()
    tokens = _company_tokens(company_name)
    compact_host = re.sub(r"[^a-z0-9]", "", host)
    compact_name = "".join(tokens)
    mentions = sum(token in combined for token in tokens)
    score = 0.08 + max(0, 0.10 - rank * 0.01)
    if any(term in path for term in ("career", "jobs", "job-search", "opportunities")):
        score += 0.18
    if tokens and mentions:
        score += min(0.24, 0.10 + mentions * 0.05)
    if compact_name and len(compact_name) >= 4 and compact_name in compact_host:
        score += 0.32
    elif any(token in compact_host for token in tokens if len(token) >= 5):
        score += 0.20
    if fingerprint_url(link):
        score += 0.14
    if not mentions and not any(token in compact_host for token in tokens):
        score -= 0.18
    return max(-1, min(1, score))


def seed_database(*, apply: bool, limit: int, include_sec: bool = False) -> dict[str, int]:
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """WITH leads AS (
                   SELECT c.company_id, c.company_name,
                       COUNT(*) FILTER (WHERE jp.data_tier=2 AND jp.status='raw')::int AS tier2_jobs,
                       COUNT(*) FILTER (WHERE jp.data_tier=1)::int AS historical_tier1,
                       COUNT(*) FILTER (WHERE jp.data_tier=1 AND jp.is_public=true)::int AS current_public,
                       MAX(jp.last_seen_at) AS last_seen
                   FROM companies c
                   JOIN job_postings jp ON jp.company_id=c.company_id
                   WHERE jp.domain IS NOT NULL
                      OR jp.scope_status IN ('accepted_core','accepted_evidence')
                   GROUP BY c.company_id, c.company_name
               )
               SELECT * FROM leads
               WHERE (tier2_jobs > 0 OR (historical_tier1 > 0 AND current_public=0))
               ORDER BY
                   CASE WHEN tier2_jobs > 0 AND historical_tier1=0 THEN 0 ELSE 1 END,
                   GREATEST(tier2_jobs, historical_tier1) DESC,
                   last_seen DESC NULLS LAST
               LIMIT %s""",
            (limit,),
        )
        leads = list(cur.fetchall())
        inserted = 0
        for lead in leads:
            name = lead["company_name"]
            if not name or is_company_blocked(name):
                continue
            key = company_key(name)
            source = "tier2_lead" if lead["tier2_jobs"] else "dormant_archive"
            count = max(lead["tier2_jobs"], lead["historical_tier1"])
            if apply:
                cur.execute(
                    """INSERT INTO career_host_candidates
                        (company_id,company_name,company_key,discovery_source,lead_job_count,evidence)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (company_key) DO UPDATE SET
                         lead_job_count=GREATEST(career_host_candidates.lead_job_count,EXCLUDED.lead_job_count),
                         evidence=career_host_candidates.evidence || EXCLUDED.evidence""",
                    (lead["company_id"], name, key, source, count, Json({
                        "tier2_jobs": lead["tier2_jobs"],
                        "historical_tier1": lead["historical_tier1"],
                        "current_public": lead["current_public"],
                    })),
                )
                inserted += int(cur.rowcount > 0)

        sec_added = 0
        if include_sec:
            try:
                response = requests.get(
                    "https://www.sec.gov/files/company_tickers.json",
                    headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip, deflate"},
                    timeout=30,
                )
                response.raise_for_status()
                for item in response.json().values():
                    name = str(item.get("title") or "").strip()
                    if not name or is_company_blocked(name):
                        continue
                    key = company_key(name)
                    if apply:
                        cur.execute(
                            """INSERT INTO career_host_candidates
                                (company_name,company_key,discovery_source,evidence)
                               VALUES (%s,%s,'sec_ticker',%s)
                               ON CONFLICT (company_key) DO NOTHING""",
                            (name, key, Json({"ticker": item.get("ticker"), "cik": item.get("cik_str")})),
                        )
                        sec_added += int(cur.rowcount > 0)
            except (requests.RequestException, ValueError) as exc:
                # Database/archive seeds still have value when SEC is briefly
                # unavailable; do not discard them or fail the daily DAG.
                log.warning("SEC employer-universe seed unavailable: %s", exc)
        if not apply:
            conn.rollback()
        return {"database_leads": len(leads), "upserted": inserted, "sec_added": sec_added}


def _discover_sitemaps(session: requests.Session, page_url: str, html: str = "") -> list[str]:
    origin = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}"
    found: list[str] = []
    try:
        robots = _safe_get(session, f"{origin}/robots.txt").text
        found.extend(re.findall(r"(?im)^\s*Sitemap:\s*(\S+)", robots))
    except Exception:
        pass
    if html:
        soup = BeautifulSoup(html, "html.parser")
        for node in soup.select("link[rel='sitemap'][href]"):
            found.append(urljoin(page_url, node.get("href")))
    found.extend((f"{origin}/sitemap.xml", f"{origin}/sitemap_index.xml"))
    return list(dict.fromkeys(found))


def resolve_candidates(*, apply: bool, limit: int, min_score: float = 0.58) -> dict[str, int]:
    api_key = os.getenv("SERPER_API_KEY")
    if not api_key:
        raise RuntimeError("SERPER_API_KEY is required")
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT * FROM career_host_candidates
               WHERE status='pending'
                 AND (last_attempted_at IS NULL OR last_attempted_at < now()-interval '7 days')
               ORDER BY lead_job_count DESC, discovered_at
               LIMIT %s""",
            (limit,),
        )
        candidates = list(cur.fetchall())
        resolved = review = rejected = 0
        session = requests.Session()
        for candidate in candidates:
            name = candidate["company_name"]
            query = f'"{name}" official careers jobs'
            try:
                search = session.post(
                    SERPER_URL,
                    headers={"X-API-KEY": api_key, "Content-Type": "application/json"},
                    json={"q": query, "gl": "us", "hl": "en", "num": 10},
                    timeout=20,
                )
                search.raise_for_status()
                results = search.json().get("organic") or []
                if candidate.get("discovered_url"):
                    results = [{
                        "link": candidate["discovered_url"],
                        "title": f"{name} careers",
                        "snippet": "Previously observed career-platform URL",
                    }, *results]
            except Exception as exc:
                log.warning("Serper resolve failed for %s: %s", name, exc)
                if apply:
                    cur.execute(
                        "UPDATE career_host_candidates SET last_attempted_at=now(), evidence=evidence || %s WHERE candidate_id=%s",
                        (Json({"last_error": str(exc)[:500]}), candidate["candidate_id"]),
                    )
                    conn.commit()
                continue

            ranked = sorted(
                ((_score_search_result(name, result, rank), result) for rank, result in enumerate(results)),
                key=lambda pair: pair[0],
                reverse=True,
            )
            best: Optional[dict[str, Any]] = None
            for search_score, result in ranked[:4]:
                if search_score < 0:
                    continue
                link = str(result.get("link") or "")
                try:
                    response = _safe_get(session, link)
                    html = response.text
                    final_url = response.url
                except Exception:
                    continue
                if is_blocked_result(final_url):
                    continue
                fingerprint = fingerprint_page(final_url, html)
                resolved_url = fingerprint.url if fingerprint.platform != "custom" else final_url
                if is_blocked_result(resolved_url):
                    continue
                sitemaps = _discover_sitemaps(session, final_url, html)
                page_text = BeautifulSoup(html, "html.parser").get_text(" ", strip=True).lower()[:100_000]
                mentions = sum(token in page_text for token in _company_tokens(name))
                confidence = min(1.0, search_score + (0.10 if mentions else 0))
                best = {
                    "result": result,
                    "final_url": final_url,
                    "resolved_url": resolved_url,
                    "fingerprint": fingerprint,
                    "sitemaps": sitemaps,
                    "confidence": confidence,
                }
                break

            if best is None:
                status = "needs_review" if results else "rejected"
                review += status == "needs_review"
                rejected += status == "rejected"
                if apply:
                    cur.execute(
                        "UPDATE career_host_candidates SET status=%s,last_attempted_at=now(),evidence=evidence || %s WHERE candidate_id=%s",
                        (status, Json({"query": query, "result_count": len(results)}), candidate["candidate_id"]),
                    )
                    cur.execute(
                        """INSERT INTO career_discovery_queries
                            (query_text,query_kind,company_key,result_count,response_metadata)
                           VALUES (%s,'company_resolve',%s,%s,%s)""",
                        (query, candidate["company_key"], len(results), Json({"status": status})),
                    )
                    conn.commit()
                continue

            fingerprint: Fingerprint = best["fingerprint"]
            confidence = float(best["confidence"])
            status = "resolved" if confidence >= min_score else "needs_review"
            host_status = "shadow" if status == "resolved" else "quarantined"
            identity_status = "pending" if status == "resolved" else "needs_review"
            jobs_url = best["resolved_url"]
            host_id = "CH" + hashlib.md5(f"{candidate['company_key']}|{jobs_url}".encode()).hexdigest()[:16]
            official_domain = _registeredish_domain(_hostname(best["final_url"]))
            evidence = {
                "query": query,
                "result": best["result"],
                "landing_url": best["final_url"],
                "sitemaps": best["sitemaps"],
            }
            if apply:
                cur.execute(
                    """INSERT INTO career_hosts
                        (host_id,company_id,company_name,company_key,official_domain,careers_url,jobs_host,
                         platform,tenant_token,extraction_strategy,discovery_source,resolver_confidence,
                         identity_status,status,evidence)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (company_key,careers_url) DO UPDATE SET
                         platform=EXCLUDED.platform,tenant_token=EXCLUDED.tenant_token,
                         extraction_strategy=EXCLUDED.extraction_strategy,
                         resolver_confidence=GREATEST(career_hosts.resolver_confidence,EXCLUDED.resolver_confidence),
                         evidence=career_hosts.evidence || EXCLUDED.evidence,updated_at=now()
                       RETURNING host_id""",
                    (host_id, candidate["company_id"], name, candidate["company_key"], official_domain,
                     jobs_url, _hostname(jobs_url), fingerprint.platform, fingerprint.tenant_token,
                     fingerprint.strategy, candidate["discovery_source"], confidence, identity_status,
                     host_status, Json(evidence)),
                )
                stored_host_id = cur.fetchone()["host_id"]
                cur.execute(
                    """UPDATE career_host_candidates SET status=%s,official_domain=%s,careers_url=%s,
                       discovered_url=%s,resolver_confidence=%s,last_attempted_at=now(),resolved_at=now(),
                       evidence=evidence || %s WHERE candidate_id=%s""",
                    (status, official_domain, jobs_url, best["final_url"], confidence, Json(evidence),
                     candidate["candidate_id"]),
                )
                cur.execute(
                    """INSERT INTO career_discovery_queries
                        (query_text,query_kind,company_key,result_count,candidate_url,candidate_score,resolved_host_id,response_metadata)
                       VALUES (%s,'company_resolve',%s,%s,%s,%s,%s,%s)""",
                    (query, candidate["company_key"], len(results), jobs_url, confidence, stored_host_id,
                     Json({"platform": fingerprint.platform, "strategy": fingerprint.strategy})),
                )
                conn.commit()
            if status == "resolved":
                resolved += 1
            else:
                review += 1
            log.info("Resolved %s -> %s (%s, %.2f)", name, jobs_url, fingerprint.platform, confidence)
            time.sleep(0.15)
        if not apply:
            conn.rollback()
        return {"attempted": len(candidates), "resolved": resolved, "needs_review": review, "rejected": rejected}


def route_supported_ats(*, apply: bool, limit: int) -> dict[str, int]:
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT * FROM career_hosts
               WHERE status='shadow' AND extraction_strategy='ats_router'
                 AND resolver_confidence >= 0.58
               ORDER BY resolver_confidence DESC,last_job_count DESC
               LIMIT %s""",
            (limit,),
        )
        hosts = list(cur.fetchall())
        routed = skipped = 0
        for host in hosts:
            fingerprint = fingerprint_url(host["careers_url"])
            if not fingerprint or fingerprint.platform not in ROUTED_PLATFORMS or not fingerprint.tenant_token:
                skipped += 1
                continue
            tenant = fingerprint.tenant_token.split("/", 1)[0]
            server = fingerprint.server
            if fingerprint.platform == "eightfold":
                server = host["official_domain"]
                if not server:
                    skipped += 1
                    continue
            if apply:
                cur.execute(
                    """INSERT INTO ats_tenants_candidates (ats,tenant,server,source,company_name,status)
                       VALUES (%s,%s,%s,'career_host_engine',%s,'pending')
                       ON CONFLICT (ats,tenant) DO UPDATE SET
                         server=COALESCE(EXCLUDED.server,ats_tenants_candidates.server),
                         company_name=COALESCE(EXCLUDED.company_name,ats_tenants_candidates.company_name),
                         source='career_host_engine',
                         status=CASE WHEN ats_tenants_candidates.status='integrated' THEN 'integrated'
                                     ELSE 'pending' END""",
                    (fingerprint.platform, tenant, server, host["company_name"]),
                )
                cur.execute("UPDATE career_hosts SET status='routed',updated_at=now() WHERE host_id=%s", (host["host_id"],))
                cur.execute("UPDATE career_host_candidates SET status='routed' WHERE company_key=%s", (host["company_key"],))
            routed += 1
        if not apply:
            conn.rollback()
        return {"examined": len(hosts), "routed": routed, "skipped": skipped}


def _parse_sitemap(content: bytes, url: str) -> tuple[list[str], list[str]]:
    # requests may already decode a Content-Encoding gzip response. Trust the
    # wire signature instead of the filename so an already-decoded *.xml.gz
    # sitemap is not decompressed twice.
    if content[:2] == b"\x1f\x8b":
        content = gzip.decompress(content)
    root = ET.fromstring(content)
    locations = [node.text.strip() for node in root.iter() if node.tag.endswith("loc") and node.text]
    if root.tag.endswith("sitemapindex"):
        return locations, []
    return [], locations


def _job_shaped_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if path.rstrip("/").endswith(("/jobs", "/careers", "/opportunities", "/search-jobs")):
        return False
    return bool(re.search(r"/(job|jobs|career|careers|position|positions|opening|openings|vacanc)[^?#]*/", path + "/"))


def enumerate_job_pages(host: dict, session: requests.Session, max_pages: int) -> tuple[list[str], dict]:
    evidence = host.get("evidence") or {}
    sitemap_urls = list(evidence.get("sitemaps") or [])
    try:
        page = _safe_get(session, host["careers_url"])
        html = page.text
        sitemap_urls.extend(_discover_sitemaps(session, page.url, html))
    except Exception:
        html = ""
    sitemap_urls = list(dict.fromkeys(sitemap_urls))
    queue = sitemap_urls[:]
    visited: set[str] = set()
    pages: list[str] = []
    sitemap_errors = 0
    while queue and len(visited) < 60 and len(pages) < max_pages:
        sitemap_url = queue.pop(0)
        if sitemap_url in visited:
            continue
        visited.add(sitemap_url)
        try:
            response = _safe_get(session, sitemap_url)
            indexes, urls = _parse_sitemap(response.content, sitemap_url)
            queue.extend(index for index in indexes if index not in visited)
            pages.extend(url for url in urls if _job_shaped_url(url))
        except Exception:
            sitemap_errors += 1
    if not pages and html:
        pages = [url for url in _all_urls(host["careers_url"], html) if _job_shaped_url(url)]
    unique = list(dict.fromkeys(pages))[:max_pages]
    return unique, {"sitemaps_attempted": len(visited), "sitemap_errors": sitemap_errors}


def _country_name(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get("name") or value.get("value") or value.get("addressCountry")
    return str(value or "").strip()


def _location_evidence(posting: dict) -> Optional[tuple[str, dict]]:
    remote = str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    requirements = posting.get("applicantLocationRequirements") or []
    requirements = requirements if isinstance(requirements, list) else [requirements]
    requirement_names = {_country_name(item).lower() for item in requirements}
    remote_us = remote and bool(requirement_names & US_COUNTRIES)
    locations = posting.get("jobLocation") or []
    locations = locations if isinstance(locations, list) else [locations]
    for place in locations:
        address = place.get("address") or {} if isinstance(place, dict) else {}
        country = _country_name(address.get("addressCountry")).lower()
        state = str(address.get("addressRegion") or "").upper().strip()
        if country in US_COUNTRIES or state in US_STATES:
            text = ", ".join(filter(None, [
                str(address.get("addressLocality") or ""), state,
                "United States" if country in US_COUNTRIES else "",
            ]))
            if remote:
                text = f"Remote, {text or 'United States'}"
            return text, {"country": "US", "state": state or None, "kind": "jobLocation"}
    if remote_us:
        return "Remote, United States", {"country": "US", "kind": "applicantLocationRequirements"}
    return None


def organization_matches(company_name: str, organization_name: str) -> bool:
    expected = set(_company_tokens(company_name))
    actual = set(_company_tokens(organization_name))
    if not expected or not actual:
        return False
    if expected <= actual or actual <= expected:
        return True
    return len(expected & actual) / max(1, min(len(expected), len(actual))) >= 0.6


def _parse_timestamp(value: Any) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def posting_to_job(host: dict, posting: dict, page_url: str, stats: CrawlStats) -> Optional[RawJob]:
    stats.postings_parsed += 1
    title = _text(posting.get("title")).strip()
    if not title or not is_target_role(title):
        stats.rejection_reasons["non_target_role"] += 1
        return None
    stats.target_jobs += 1
    location = _location_evidence(posting)
    if not location:
        stats.foreign_rejections += 1
        stats.rejection_reasons["no_explicit_us_evidence"] += 1
        return None
    stats.explicit_us_jobs += 1
    organization = _text(posting.get("hiringOrganization")).strip()
    if not organization or not organization_matches(host["company_name"], organization):
        stats.identity_mismatches += 1
        stats.rejection_reasons["hiring_organization_mismatch"] += 1
        return None
    description = BeautifulSoup(str(posting.get("description") or ""), "html.parser").get_text("\n", strip=True)
    if len(description) < 100:
        stats.quality_rejections += 1
        stats.rejection_reasons["short_description"] += 1
        return None
    valid_through = _parse_timestamp(posting.get("validThrough"))
    if valid_through and valid_through < datetime.now(timezone.utc):
        stats.quality_rejections += 1
        stats.rejection_reasons["expired_valid_through"] += 1
        return None
    identifier = posting.get("identifier") or {}
    source_id = _text(identifier.get("value") if isinstance(identifier, dict) else identifier).strip()
    canonical_url = str(posting.get("url") or page_url).strip()
    source_id = source_id or hashlib.sha256(canonical_url.encode()).hexdigest()[:24]
    employment = posting.get("employmentType")
    employment = employment[0] if isinstance(employment, list) and employment else employment
    stats.accepted_jobs += 1
    return RawJob(
        source="career_site",
        source_id=f"{host['host_id']}|{source_id}",
        title=title,
        company=host["company_name"],
        location=location[0],
        description=description,
        job_url=canonical_url,
        posted_date=str(posting.get("datePosted") or "")[:10] or None,
        employment_type=_text(employment) or None,
        workplace_type="remote" if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE" else None,
        remote=str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE",
        metadata={
            "tenant": host["host_id"],
            "location_evidence": location[1],
            "hiring_organization": organization,
            "valid_through": valid_through,
            "direct_apply": posting.get("directApply"),
            "source_quality_status": "active" if host["status"] == "active" else "quarantine",
        },
    )


def _fetch_jobposting_page(url: str) -> tuple[str, list[dict], Optional[str]]:
    try:
        response = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        response.raise_for_status()
        if len(response.content) > MAX_RESPONSE_BYTES:
            return url, [], "response_too_large"
        return response.url, jobposting_objects(response.text), None
    except Exception as exc:
        return url, [], str(exc)[:300]


def crawl_jsonld_host(host: dict, max_pages: int, workers: int) -> tuple[list[RawJob], CrawlStats, dict]:
    stats = CrawlStats()
    session = requests.Session()
    pages, detail = enumerate_job_pages(host, session, max_pages)
    stats.pages_discovered = len(pages)
    jobs: dict[tuple[str, str], RawJob] = {}
    with ThreadPoolExecutor(max_workers=max(1, workers)) as executor:
        futures = {executor.submit(_fetch_jobposting_page, url): url for url in pages}
        for future in as_completed(futures):
            page_url, postings, error = future.result()
            stats.pages_fetched += 1
            if error:
                stats.errors += 1
                stats.rejection_reasons["fetch_error"] += 1
                continue
            if not postings:
                stats.rejection_reasons["no_jobposting"] += 1
            for posting in postings:
                job = posting_to_job(host, posting, page_url, stats)
                if job:
                    jobs[(job.source, job.source_id)] = job
    stats.duplicate_jobs = stats.accepted_jobs - len(jobs)
    return list(jobs.values()), stats, detail


def _oracle_locator(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    match = re.search(r"/sites/([^/?#]+)", parsed.path, re.I)
    if not parsed.hostname or not match:
        raise ValueError(f"invalid Oracle Candidate Experience URL: {url}")
    return f"{parsed.scheme}://{parsed.netloc}", match.group(1)


def crawl_oracle_host(host: dict, max_pages: int) -> tuple[list[RawJob], CrawlStats, dict]:
    stats = CrawlStats()
    origin, site = _oracle_locator(host["careers_url"])
    endpoint = f"{origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    session = requests.Session()
    offset = 0
    page_size = min(200, max_pages)
    listings: list[dict] = []
    total = 0
    while len(listings) < max_pages:
        response = _safe_get(session, endpoint, params={
            "onlyData": "true",
            "expand": "requisitionList",
            "finder": f"findReqs;siteNumber={site},limit={page_size},offset={offset}",
        })
        stats.pages_fetched += 1
        item = (response.json().get("items") or [{}])[0]
        batch = item.get("requisitionList") or []
        total = int(item.get("TotalJobsCount") or len(batch))
        listings.extend(batch)
        if not batch or len(listings) >= total:
            break
        offset += page_size
    stats.pages_discovered = min(total, max_pages)
    jobs: list[RawJob] = []
    detail_endpoint = f"{origin}/hcmRestApi/resources/latest/recruitingCEJobRequisitionDetails"
    for listing in listings[:max_pages]:
        title = str(listing.get("Title") or "")
        if not is_target_role(title):
            stats.rejection_reasons["non_target_role"] += 1
            continue
        stats.target_jobs += 1
        country = str(listing.get("PrimaryLocationCountry") or "").lower()
        location = str(listing.get("PrimaryLocation") or "")
        if country not in US_COUNTRIES and "united states" not in location.lower():
            stats.foreign_rejections += 1
            stats.rejection_reasons["no_explicit_us_evidence"] += 1
            continue
        stats.explicit_us_jobs += 1
        requisition_id = str(listing.get("Id") or "")
        try:
            response = _safe_get(session, detail_endpoint, params={
                "expand": "all",
                "onlyData": "true",
                "finder": f'ById;Id="{requisition_id}",siteNumber={site}',
            })
            detail = (response.json().get("items") or [{}])[0]
        except Exception:
            stats.errors += 1
            stats.rejection_reasons["detail_fetch_error"] += 1
            continue
        description_html = "\n".join(str(detail.get(key) or "") for key in (
            "ExternalDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr",
        ))
        description = BeautifulSoup(description_html, "html.parser").get_text("\n", strip=True)
        if len(description) < 100:
            stats.quality_rejections += 1
            stats.rejection_reasons["short_description"] += 1
            continue
        valid_through = _parse_timestamp(detail.get("PostingEndDate") or listing.get("PostingEndDate"))
        if valid_through and valid_through < datetime.now(timezone.utc):
            stats.quality_rejections += 1
            stats.rejection_reasons["expired_valid_through"] += 1
            continue
        workplace = str(detail.get("WorkplaceType") or listing.get("WorkplaceType") or "").lower()
        stats.accepted_jobs += 1
        jobs.append(RawJob(
            source="oracle_cloud",
            source_id=f"{host['host_id']}|{site}|{requisition_id}",
            title=title,
            company=host["company_name"],
            location=location,
            description=description,
            job_url=f"{origin}/hcmUI/CandidateExperience/en/sites/{site}/job/{requisition_id}",
            posted_date=str(listing.get("PostedDate") or detail.get("PostedDate") or "")[:10] or None,
            workplace_type="remote" if "remote" in workplace else ("hybrid" if "hybrid" in workplace else None),
            metadata={
                "tenant": host["host_id"],
                "location_evidence": {"country": "US", "kind": "PrimaryLocationCountry"},
                "hiring_organization": host["company_name"],
                "valid_through": valid_through,
                "direct_apply": True,
                "source_quality_status": "active" if host["status"] == "active" else "quarantine",
            },
        ))
    return jobs, stats, {"oracle_site": site, "reported_total": total}


def _write_host_jobs(cur, jobs: Iterable[RawJob], stats: CrawlStats) -> None:
    for job in jobs:
        cur.execute("SAVEPOINT career_host_job")
        try:
            if ingest_job(cur, job):
                stats.written_jobs += 1
        except Exception as exc:
            cur.execute("ROLLBACK TO SAVEPOINT career_host_job")
            stats.errors += 1
            stats.rejection_reasons["database_error"] += 1
            log.warning("Career job write failed for %s: %s", job.job_url, exc)
        finally:
            cur.execute("RELEASE SAVEPOINT career_host_job")


def _classify_host_run(jobs: list[RawJob], stats: CrawlStats, detail: dict) -> tuple[str, bool]:
    mismatch_denominator = max(1, stats.target_jobs)
    identity_rate = stats.identity_mismatches / mismatch_denominator
    sitemap_errors = int(detail.get("sitemap_errors") or 0)
    complete = stats.errors == 0 and sitemap_errors == 0
    clean = bool(jobs) and complete and identity_rate <= 0.01
    if not complete:
        return "partial_failure", clean
    if stats.target_jobs and identity_rate > 0.01:
        return "quality_rejected", clean
    return ("complete_nonzero" if jobs else "complete_zero"), clean


def _expire_host_jobs(cur, host: dict, run_started: datetime) -> int:
    source = "oracle_cloud" if host["extraction_strategy"] == "oracle_cloud" else "career_site"
    cur.execute(
        """INSERT INTO job_posting_events (job_id,event_type,observed_at,source,posted_date)
           SELECT job_id,'disappeared',now(),ingestion_source,posted_date
           FROM job_postings
           WHERE ingestion_source=%s AND crawl_tenant=%s AND status='raw'
             AND last_seen_at < %s
           ON CONFLICT DO NOTHING""",
        (source, host["host_id"], run_started),
    )
    cur.execute(
        """UPDATE job_postings SET status='expired',expired_reason='career_host_closed',is_public=false
           WHERE ingestion_source=%s AND crawl_tenant=%s AND status='raw'
             AND last_seen_at < %s""",
        (source, host["host_id"], run_started),
    )
    return cur.rowcount


def crawl_hosts(
    *, apply: bool, limit: int, max_pages: int, workers: int, activate_mature: bool,
) -> dict[str, int]:
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        blocked_hosts = quarantine_blocked_hosts(cur, apply=apply)
        if apply and blocked_hosts:
            conn.commit()
        cur.execute(
            """SELECT * FROM career_hosts
               WHERE status IN ('shadow','active')
                 AND extraction_strategy IN ('sitemap_jsonld','oracle_cloud')
                 AND (next_crawl_at IS NULL OR next_crawl_at <= now())
               ORDER BY CASE status WHEN 'active' THEN 0 ELSE 1 END,
                        last_crawled_at NULLS FIRST,resolver_confidence DESC
               LIMIT %s""",
            (limit,),
        )
        hosts = [dict(row) for row in cur.fetchall()]
        successful = failed = activated = total_jobs = 0
        for host in hosts:
            run_started = datetime.now(timezone.utc)
            run_id = f"career_{host['host_id']}_{run_started.strftime('%Y%m%dT%H%M%S%f')}"
            if apply:
                cur.execute("INSERT INTO career_host_runs(run_id,host_id) VALUES(%s,%s)", (run_id, host["host_id"]))
                conn.commit()
            try:
                if host["extraction_strategy"] == "oracle_cloud":
                    jobs, stats, detail = crawl_oracle_host(host, max_pages)
                else:
                    jobs, stats, detail = crawl_jsonld_host(host, max_pages, workers)
                total_jobs += len(jobs)
                run_status, clean = _classify_host_run(jobs, stats, detail)
                if apply:
                    ensure_schema_columns(cur)
                    _write_host_jobs(cur, jobs, stats)
                    # A per-row database failure is part of crawl completeness;
                    # reclassify after writes before any lifecycle mutation.
                    run_status, clean = _classify_host_run(jobs, stats, detail)
                    expired = _expire_host_jobs(cur, host, run_started) if (
                        host["status"] == "active" and run_status in ("complete_nonzero", "complete_zero")
                    ) else 0
                    cur.execute(
                        """UPDATE career_host_runs SET finished_at=now(),status=%s,
                           pages_discovered=%s,pages_fetched=%s,postings_parsed=%s,target_jobs=%s,
                           explicit_us_jobs=%s,accepted_jobs=%s,written_jobs=%s,duplicate_jobs=%s,
                           identity_mismatches=%s,foreign_rejections=%s,quality_rejections=%s,errors=%s,
                           rejection_reasons=%s,detail=%s WHERE run_id=%s""",
                        (run_status, stats.pages_discovered, stats.pages_fetched, stats.postings_parsed,
                         stats.target_jobs, stats.explicit_us_jobs, stats.accepted_jobs, stats.written_jobs,
                         stats.duplicate_jobs, stats.identity_mismatches, stats.foreign_rejections,
                         stats.quality_rejections, stats.errors, Json(dict(stats.rejection_reasons)),
                         Json({**detail, "expired_jobs": expired}), run_id),
                    )
                    if run_status in ("complete_nonzero", "complete_zero"):
                        cur.execute(
                            """UPDATE career_hosts SET last_crawled_at=now(),last_success_at=now(),
                               last_nonempty_at=CASE WHEN %s>0 THEN now() ELSE last_nonempty_at END,
                               last_job_count=%s,failure_streak=0,next_crawl_at=now()+interval '1 day',
                               first_clean_crawl_at=CASE WHEN %s AND first_clean_crawl_at IS NULL THEN now()
                                                         ELSE first_clean_crawl_at END,
                               identity_status=CASE WHEN %s THEN 'verified' ELSE identity_status END,
                               updated_at=now() WHERE host_id=%s""",
                            (len(jobs), len(jobs), clean, clean, host["host_id"]),
                        )
                    else:
                        cur.execute(
                            """UPDATE career_hosts SET last_crawled_at=now(),failure_streak=failure_streak+1,
                               next_crawl_at=now()+interval '3 days',
                               status=CASE WHEN %s='quality_rejected' THEN 'quarantined' ELSE status END,
                               updated_at=now() WHERE host_id=%s""",
                            (run_status, host["host_id"]),
                        )
                    if activate_mature and clean and host["status"] == "shadow":
                        cur.execute(
                            """UPDATE career_hosts SET status='active',activated_at=now(),updated_at=now()
                               WHERE host_id=%s AND first_clean_crawl_at <= now()-interval '7 days'
                               RETURNING host_id""",
                            (host["host_id"],),
                        )
                        if cur.fetchone():
                            source = "oracle_cloud" if host["extraction_strategy"] == "oracle_cloud" else "career_site"
                            cur.execute(
                                """UPDATE job_postings SET source_quality_status='active'
                                   WHERE ingestion_source=%s AND crawl_tenant=%s AND status='raw'""",
                                (source, host["host_id"]),
                            )
                            activated += 1
                    conn.commit()
                if run_status in ("complete_nonzero", "complete_zero"):
                    successful += 1
                else:
                    failed += 1
                log.info(
                    "Career host %s: pages=%d target=%d US=%d accepted=%d status=%s",
                    host["company_name"], stats.pages_fetched, stats.target_jobs,
                    stats.explicit_us_jobs, len(jobs), run_status,
                )
            except Exception as exc:
                conn.rollback()
                failed += 1
                log.warning("Career host failed %s: %s", host["company_name"], exc)
                if apply:
                    cur.execute(
                        """UPDATE career_host_runs SET finished_at=now(),status='failed',errors=errors+1,
                           detail=detail || %s WHERE run_id=%s""",
                        (Json({"error": str(exc)[:1000]}), run_id),
                    )
                    cur.execute(
                        """UPDATE career_hosts SET last_crawled_at=now(),failure_streak=failure_streak+1,
                           next_crawl_at=now()+LEAST(interval '7 days',interval '1 hour' * power(2,failure_streak+1)),
                           updated_at=now() WHERE host_id=%s""",
                        (host["host_id"],),
                    )
                    conn.commit()
        if not apply:
            conn.rollback()
        return {"hosts": len(hosts), "successful": successful, "failed": failed,
                "accepted_jobs": total_jobs, "activated": activated,
                "blocked_hosts": blocked_hosts}


def report() -> dict[str, Any]:
    with connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT COUNT(*)::int count FROM public.vw_lander_visible_opportunities")
        visible_jobs = cur.fetchone()["count"]
        cur.execute("SELECT COUNT(*)::int count FROM public.vw_lander_publication_candidates")
        publication_candidates = cur.fetchone()["count"]
        cur.execute("SELECT status,COUNT(*)::int count FROM career_host_candidates GROUP BY status ORDER BY status")
        candidates = {row["status"]: row["count"] for row in cur.fetchall()}
        cur.execute(
            """SELECT platform,status,COUNT(*)::int hosts,SUM(last_job_count)::int jobs
               FROM career_hosts GROUP BY platform,status ORDER BY platform,status"""
        )
        hosts = [dict(row) for row in cur.fetchall()]
        cur.execute(
            """SELECT COUNT(*)::int runs,COALESCE(SUM(accepted_jobs),0)::int accepted,
                      COALESCE(SUM(identity_mismatches),0)::int identity_mismatches,
                      COALESCE(SUM(foreign_rejections),0)::int foreign_rejections
               FROM career_host_runs WHERE started_at >= now()-interval '7 days'"""
        )
        weekly = dict(cur.fetchone())
        target = 100_000
        return {
            "inventory": {
                "visible_jobs": visible_jobs,
                "publication_candidates": publication_candidates,
                "target": target,
                "gap_to_target": max(0, target - visible_jobs),
            },
            "candidates": candidates,
            "hosts": hosts,
            "last_7_days": weekly,
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("migrate", "seed", "resolve", "route", "crawl", "run", "report"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--max-pages-per-host", type=int, default=2000)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--include-sec", action="store_true")
    parser.add_argument("--activate-mature", action="store_true")
    args = parser.parse_args()
    if args.action == "migrate":
        migrate()
        print({"migrated": True})
    elif args.action == "seed":
        print(seed_database(apply=args.apply, limit=args.limit, include_sec=args.include_sec))
    elif args.action == "resolve":
        print(resolve_candidates(apply=args.apply, limit=args.limit))
    elif args.action == "route":
        print(route_supported_ats(apply=args.apply, limit=args.limit))
    elif args.action == "crawl":
        print(crawl_hosts(apply=args.apply, limit=args.limit, max_pages=args.max_pages_per_host,
                          workers=args.workers, activate_mature=args.activate_mature))
    elif args.action == "run":
        print({
            "seed": seed_database(apply=args.apply, limit=args.limit, include_sec=args.include_sec),
            "resolve": resolve_candidates(apply=args.apply, limit=args.limit),
            "route": route_supported_ats(apply=args.apply, limit=args.limit),
            "crawl": crawl_hosts(apply=args.apply, limit=args.limit, max_pages=args.max_pages_per_host,
                                 workers=args.workers, activate_mature=args.activate_mature),
        })
    else:
        print(json.dumps(report(), indent=2, default=str))


if __name__ == "__main__":
    main()
