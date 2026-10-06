#!/usr/bin/env python3
"""
discover_icims_commoncrawl.py

Common Crawl tenant discovery for iCIMS. Mirrors the Workday miner
(discover_workday_tenants.py): query CDX indexes for *.icims.com/jobs/*
URLs, extract tenant slugs, and stage them in ats_tenants_candidates as
'pending' so the existing validator (validate_ats_candidates.py --ats icims)
and integrator decide activation. This script NEVER writes
discovered_companies — a crawled URL is evidence of a board, not proof the
board is alive, US-relevant, or worth harvesting.

Dedup is against three sources: ats_tenants_candidates (ats='icims'),
discovered_companies (ats_source='icims'), and the hardcoded
ICIMS_COMPANIES fallback list in icims_harvest.py.

Safe to re-run (idempotent — ON CONFLICT DO NOTHING).

Usage:
    python python/discover_icims_commoncrawl.py                  # dry-run
    python python/discover_icims_commoncrawl.py --apply          # stage to DB
    python python/discover_icims_commoncrawl.py --apply --limit 200
    python python/discover_icims_commoncrawl.py --crawls 5 --max-pages 10
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import psycopg2
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================

COMMONCRAWL_COLLINFO = "https://index.commoncrawl.org/collinfo.json"
COMMONCRAWL_CDX_BASE = "https://index.commoncrawl.org/{crawl}-index"
CDX_PAGE_SIZE        = 5000
CDX_MAX_PAGES        = 20          # 100,000 records max per crawl index

# {slug}.icims.com — mirrors ATS_URL_RE["icims"] in discover_ats_aggressive.py
ICIMS_URL_RE = re.compile(r"https?://([a-z0-9][a-z0-9_-]+)\.icims\.com(/[^?#\s]*)?", re.IGNORECASE)

# Platform/infrastructure subdomains that are not employer boards.
# Mirrors DOMAIN_NOISE + ICIMS_INFRA_RE in discover_ats_aggressive.py
# (duplicated because that module is async/aiohttp-based; this one is not).
DOMAIN_NOISE = {"www", "api", "jobs", "careers", "app", "help", "support",
                "dev", "staging", "test", "mail", "blog", "static", "cdn",
                "us1", "us2", "eu1", "resources", "admin", "login", "auth"}
ICIMS_INFRA_RE = re.compile(
    r"^(?:api(?:-[\w-]+)?|login(?:-[\w-]+)?|analytics(?:-[\w-]+)?|"
    r"statuspage|trust|teams|talent|agents|marketplace|engage|developers|"
    r"design-system|mta-sts|notacustomer[\w-]*|"
    r"us\d+|eu\d+|ca\d+)$",
    re.IGNORECASE,
)


# ============================================================
# URL PARSING (pure, testable)
# ============================================================

def parse_icims_tenant(url: str) -> Optional[str]:
    """
    Extract the tenant slug from an iCIMS URL, or None.

    Only career-board URLs (path under /jobs/) count as board evidence;
    a tenant's marketing/root pages prove nothing about a live board.
    Infrastructure subdomains (api-*, login, us123, ...) are not employers.
    """
    m = ICIMS_URL_RE.search(url or "")
    if not m:
        return None
    tenant = m.group(1).lower()
    path = (m.group(2) or "").lower()
    if len(tenant) < 2 or tenant in DOMAIN_NOISE or ICIMS_INFRA_RE.match(tenant):
        return None
    if not path.startswith("/jobs/"):
        return None
    return tenant


def humanize_company_name(slug: str) -> str:
    """Best-effort display name from a tenant slug (validator data wins later)."""
    cleaned = re.sub(r"^(careers?|jobs?)[-_]", "", slug, flags=re.IGNORECASE)
    cleaned = re.sub(r"[-_]?(careers?|jobs?)$", "", cleaned, flags=re.IGNORECASE)
    return re.sub(r"[-_]+", " ", cleaned).strip().title() or slug.title()


# ============================================================
# DB
# ============================================================

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def load_known_tenants() -> Set[str]:
    """
    Tenant slugs already tracked: staged candidates, the live registry,
    and the hardcoded harvester fallback list (imported, not duplicated).
    """
    known: Set[str] = set()
    try:
        from icims_harvest import ICIMS_COMPANIES
        known.update(slug.lower() for _name, slug in ICIMS_COMPANIES)
    except Exception as e:
        log.warning(f"Could not import hardcoded iCIMS list: {e}")

    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT tenant FROM ats_tenants_candidates WHERE ats = 'icims'")
        known.update(t.lower() for (t,) in cur.fetchall() if t)
        cur.execute("""
            SELECT LOWER(split_part(board_token, '/', 1))
            FROM discovered_companies
            WHERE ats_source = 'icims'
        """)
        known.update(t for (t,) in cur.fetchall() if t)
        cur.close()
        conn.close()
    except Exception as e:
        log.warning(f"Could not load known tenants from DB: {e}")

    log.info(f"Known/existing iCIMS tenants: {len(known)}")
    return known


def save_candidates(candidates: List[Dict], apply: bool) -> int:
    """
    Stage candidates in ats_tenants_candidates as 'pending'.
    Returns count of rows inserted. Never touches discovered_companies.
    """
    if not candidates:
        return 0

    if not apply:
        for c in candidates[:50]:
            log.info(f"  [DRY RUN] icims | tenant={c['tenant']:30} | source=commoncrawl")
        if len(candidates) > 50:
            log.info(f"  [DRY RUN] ... and {len(candidates) - 50} more")
        return 0

    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for c in candidates:
        try:
            cur.execute(
                """
                INSERT INTO ats_tenants_candidates
                    (ats, tenant, server, source, company_name, status)
                VALUES ('icims', %s, NULL, 'commoncrawl', %s, 'pending')
                ON CONFLICT (ats, tenant) DO NOTHING
                """,
                (c["tenant"], c.get("company_name")),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            log.warning(f"  Insert failed for icims/{c.get('tenant')}: {e}")
            conn.rollback()
            continue
    conn.commit()
    cur.close()
    conn.close()
    return inserted


# ============================================================
# COMMON CRAWL
# ============================================================

def _get_latest_crawls(n: int = 3) -> List[str]:
    """Fetch collinfo.json and return the N most-recent crawl IDs."""
    try:
        r = requests.get(COMMONCRAWL_COLLINFO, timeout=15)
        r.raise_for_status()
        crawls = r.json()  # list of dicts, newest first
        return [c["id"] for c in crawls[:n]]
    except Exception as e:
        log.warning(f"Could not fetch Common Crawl collinfo: {e}")
        return ["CC-MAIN-2025-18", "CC-MAIN-2025-13", "CC-MAIN-2025-08"]


def fetch_commoncrawl_tenants(
    known: Set[str],
    crawls: int = 3,
    max_pages: int = CDX_MAX_PAGES,
    limit: Optional[int] = None,
) -> List[Dict]:
    """
    Query Common Crawl CDX indexes for *.icims.com/jobs/* URLs.
    Returns one candidate per distinct new tenant slug.
    """
    crawl_ids = _get_latest_crawls(n=crawls)
    log.info(f"Common Crawl: using indexes {crawl_ids}")

    seen: Set[str] = set(known)
    candidates: List[Dict] = []

    for crawl_id in crawl_ids:
        base_url = COMMONCRAWL_CDX_BASE.format(crawl=crawl_id)
        offset = 0
        pages_fetched = 0

        log.info(f"  Querying {crawl_id}...")

        while pages_fetched < max_pages:
            params = {
                "url": "*.icims.com/jobs/*",
                "output": "json",
                "limit": CDX_PAGE_SIZE,
                "offset": offset,
                "fl": "url",           # only return the url field
            }
            try:
                r = requests.get(base_url, params=params, timeout=30)
                if r.status_code == 404:
                    log.info(f"    {crawl_id}: no more records at offset {offset}")
                    break
                r.raise_for_status()
                text = r.text.strip()
            except Exception as e:
                log.warning(f"    CDX request failed at offset {offset}: {e}")
                break

            if not text:
                break

            lines = text.splitlines()
            new_this_page = 0

            for line in lines:
                try:
                    obj = json.loads(line)
                    url = obj.get("url", "")
                except (json.JSONDecodeError, AttributeError):
                    url = line  # plain text fallback

                tenant = parse_icims_tenant(url)
                if not tenant or tenant in seen:
                    continue
                seen.add(tenant)
                candidates.append({
                    "tenant": tenant,
                    "company_name": humanize_company_name(tenant),
                    "discovery_source": "commoncrawl",
                })
                new_this_page += 1
                if limit and len(candidates) >= limit:
                    log.info(f"Common Crawl: reached --limit {limit}")
                    return candidates

            log.info(f"    offset={offset}: {len(lines)} records, {new_this_page} new tenants")

            if len(lines) < CDX_PAGE_SIZE:
                break  # last page

            offset += CDX_PAGE_SIZE
            pages_fetched += 1
            time.sleep(0.5)  # be polite to Common Crawl

    log.info(f"Common Crawl: {len(candidates)} new iCIMS tenant candidates")
    return candidates


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        description="Discover iCIMS tenants from Common Crawl CDX indexes."
    )
    ap.add_argument("--apply", action="store_true",
                    help="Stage candidates to ats_tenants_candidates")
    ap.add_argument("--crawls", type=int, default=3, metavar="N",
                    help="Number of recent crawl indexes to query (default: 3)")
    ap.add_argument("--max-pages", type=int, default=CDX_MAX_PAGES, metavar="N",
                    help="Max CDX pages per index (default: 20 = 100k records)")
    ap.add_argument("--limit", type=int, default=None, metavar="N",
                    help="Cap new candidates staged (recommended 200 for the first run)")
    args = ap.parse_args()

    if not args.apply:
        log.info("DRY RUN — use --apply to stage candidates")

    known = load_known_tenants()
    candidates = fetch_commoncrawl_tenants(
        known, crawls=args.crawls, max_pages=args.max_pages, limit=args.limit,
    )

    inserted = save_candidates(candidates, apply=args.apply)

    log.info("")
    log.info("=" * 60)
    log.info("DISCOVERY SUMMARY")
    log.info("=" * 60)
    log.info(f"  Total new candidates: {len(candidates)}")
    if args.apply:
        log.info(f"  Staged into ats_tenants_candidates: {inserted}")
        log.info("")
        log.info("Next steps:")
        log.info("  1. python python/validate_ats_candidates.py --apply --ats icims")
        log.info("  2. python python/integrate_ats_candidates.py --apply --ats icims")


if __name__ == "__main__":
    main()
