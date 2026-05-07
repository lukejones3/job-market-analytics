#!/usr/bin/env python3
"""
discover_workday_tenants.py

Multi-source Workday tenant discovery. Writes candidates to the
workday_tenants_candidates table for later validation.

Sources:
  1. Common Crawl CDX API — extract tenants from crawled myworkdayjobs.com URLs
  2. SEC EDGAR company probe — name-to-slug guessing + redirect sniffing

Safe to re-run (idempotent — uses ON CONFLICT DO NOTHING).

Usage:
    python python/discover_workday_tenants.py                    # all sources, dry-run
    python python/discover_workday_tenants.py --apply            # write to DB
    python python/discover_workday_tenants.py --source commoncrawl --apply
    python python/discover_workday_tenants.py --source edgar --apply
    python python/discover_workday_tenants.py --apply --edgar-limit 2000
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import psycopg2
import requests
from dotenv import load_dotenv

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

EDGAR_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
EDGAR_PROBE_WORKERS = 15          # concurrent HEAD requests
EDGAR_PROBE_TIMEOUT = 6           # seconds per request

# Workday URL parser: tenant, server, first path segment
WD_URL_RE = re.compile(
    r"https?://([a-zA-Z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com/([^/?#\s]*)",
    re.IGNORECASE,
)

# Locale codes — not board names
LOCALE_CODES = {
    "en-us", "en-gb", "en-ca", "en-au", "fr-ca", "fr-fr",
    "de-de", "es-es", "ja-jp", "zh-cn", "pt-br", "nl-nl",
    "ko-kr", "it-it", "sv-se", "pl-pl", "ru-ru",
}

# Company-name suffix words to strip before slug generation
NAME_SUFFIXES = re.compile(
    r"\b(inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|plc\.?|"
    r"group|holdings|holdco|technologies|technology|tech|"
    r"solutions|services|international|company|companies|"
    r"incorporated|limited|enterprises|partners|industries|"
    r"global|systems|associates|ventures|capital)\b",
    re.IGNORECASE,
)

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
    Return the set of tenant slugs already tracked — either in the hardcoded
    WORKDAY_COMPANIES list inside ingest_jobs.py or in discovered_companies.
    Also loads any tenants already in workday_tenants_candidates so we skip duplicates.
    """
    known: Set[str] = set()

    # Parse hardcoded list from ingest_jobs.py via regex (avoid import side-effects)
    ij_path = Path(__file__).parent / "ingest_jobs.py"
    if ij_path.exists():
        text = ij_path.read_text()
        for m in re.finditer(r'"([a-z0-9_-]+)",\s+"(wd\d+)"', text):
            known.add(m.group(1).lower())

    # Load from DB tables
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT LOWER(split_part(board_token, '/', 1))
            FROM discovered_companies
            WHERE ats_source = 'workday'
        """)
        for (t,) in cur.fetchall():
            if t:
                known.add(t)
        cur.execute("SELECT tenant FROM workday_tenants_candidates")
        for (t,) in cur.fetchall():
            known.add(t.lower())
        cur.close()
        conn.close()
    except Exception as e:
        log.warning(f"Could not load known tenants from DB: {e}")

    log.info(f"Known/existing tenants: {len(known)}")
    return known


def save_candidates(
    candidates: List[Dict],
    apply: bool,
) -> int:
    """
    Upsert candidates into workday_tenants_candidates.
    Only inserts new rows (ON CONFLICT DO NOTHING).
    Returns count of rows inserted.
    """
    if not candidates:
        return 0

    if not apply:
        for c in candidates:
            log.info(
                f"  [DRY RUN] {c['tenant']} | server={c.get('server','?')} "
                f"board={c.get('board','?')} | source={c['discovery_source']}"
            )
        return 0

    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for c in candidates:
        try:
            cur.execute(
                """
                INSERT INTO workday_tenants_candidates
                    (tenant, server, board, company_name, discovery_source)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (tenant) DO NOTHING
                """,
                (
                    c["tenant"],
                    c.get("server"),
                    c.get("board"),
                    c.get("company_name"),
                    c["discovery_source"],
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            log.warning(f"  Insert failed for {c.get('tenant')}: {e}")
            conn.rollback()
            continue
    conn.commit()
    cur.close()
    conn.close()
    return inserted


# ============================================================
# SOURCE 1: COMMON CRAWL
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
        # Fallback to known recent indexes
        return ["CC-MAIN-2025-18", "CC-MAIN-2025-13", "CC-MAIN-2025-08"]


def _parse_board_from_path(first_segment: str, url: str) -> Optional[str]:
    """
    Extract the board name from a Workday URL path.
    Handles URLs with and without a locale prefix (en-US/Board vs Board directly).
    """
    seg = first_segment.strip("/")
    if not seg:
        return None

    # If this segment is a locale code, try to extract the next path segment
    if seg.lower() in LOCALE_CODES:
        # Find what comes after the locale in the full URL
        m = re.search(
            r"myworkdayjobs\.com/[^/]+/([a-zA-Z0-9_-]{3,})",
            url,
            re.IGNORECASE,
        )
        if m:
            seg = m.group(1)
        else:
            return None

    # Reject path segments that are clearly not board names
    if seg.lower() in {"job", "jobs", "apply", "wday", "cxs", "en"}:
        return None
    if len(seg) < 2 or len(seg) > 80:
        return None

    return seg


def fetch_commoncrawl_tenants(known: Set[str]) -> List[Dict]:
    """
    Query Common Crawl CDX indexes for *.myworkdayjobs.com URLs.
    Returns list of candidate dicts with tenant, server, board (where extractable).
    """
    crawl_ids = _get_latest_crawls(n=3)
    log.info(f"Common Crawl: using indexes {crawl_ids}")

    found: Dict[str, Dict] = {}  # tenant -> candidate dict

    for crawl_id in crawl_ids:
        base_url = COMMONCRAWL_CDX_BASE.format(crawl=crawl_id)
        offset = 0
        pages_fetched = 0

        log.info(f"  Querying {crawl_id}...")

        while pages_fetched < CDX_MAX_PAGES:
            params = {
                "url": "*.myworkdayjobs.com/*",
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

                m = WD_URL_RE.search(url)
                if not m:
                    continue

                tenant = m.group(1).lower()
                server = m.group(2).lower()
                first_seg = m.group(3) if m.lastindex >= 3 else ""

                # Skip bad slugs
                if len(tenant) < 2 or tenant in {"www", "app", "api", "jobs", "careers"}:
                    continue
                # Skip tenants already tracked
                if tenant in known or tenant in found:
                    continue

                board = _parse_board_from_path(first_seg, url)
                found[tenant] = {
                    "tenant": tenant,
                    "server": server,
                    "board": board,
                    "company_name": None,
                    "discovery_source": "commoncrawl",
                }
                new_this_page += 1

            log.info(
                f"    offset={offset}: {len(lines)} records, {new_this_page} new tenants"
            )

            if len(lines) < CDX_PAGE_SIZE:
                break  # last page

            offset += CDX_PAGE_SIZE
            pages_fetched += 1
            time.sleep(0.5)  # be polite to Common Crawl

    candidates = list(found.values())
    log.info(f"Common Crawl: {len(candidates)} new tenant candidates")
    return candidates


# ============================================================
# SOURCE 2: SEC EDGAR COMPANY PROBE
# ============================================================

def _name_to_slugs(name: str, ticker: str) -> List[str]:
    """
    Generate Workday tenant slug candidates from a company name + ticker.
    Returns 3 candidates in priority order.
    """
    slugs = []

    # Clean name: strip suffixes, lowercase, remove non-alphanumeric
    clean = NAME_SUFFIXES.sub("", name)
    clean = re.sub(r"[^a-z0-9\s]", "", clean.lower()).strip()
    clean_nospace = re.sub(r"\s+", "", clean)  # "generalelectric"
    first_word = clean.split()[0] if clean.split() else clean_nospace

    # Slug 1: full clean name, no spaces
    if clean_nospace and len(clean_nospace) >= 2:
        slugs.append(clean_nospace)

    # Slug 2: first word only
    if first_word and first_word != clean_nospace and len(first_word) >= 2:
        slugs.append(first_word)

    # Slug 3: ticker (lowercase) — Workday sometimes uses ticker as slug
    t = ticker.lower().strip()
    if t and t not in slugs and len(t) >= 2:
        slugs.append(t)

    return slugs[:3]


def _probe_slug(slug: str) -> Optional[Dict]:
    """
    Probe https://{slug}.myworkdayjobs.com/ and follow redirects.
    Returns a candidate dict if the redirect lands on a wd{N} host, else None.
    """
    url = f"https://{slug}.myworkdayjobs.com/"
    try:
        r = requests.get(
            url,
            allow_redirects=True,
            timeout=EDGAR_PROBE_TIMEOUT,
            headers={"User-Agent": "Mozilla/5.0 (compatible; TenantDiscovery/1.0)"},
        )
        final = r.url
        # Valid redirect: lands on {tenant}.wd{N}.myworkdayjobs.com
        m = re.search(
            r"https?://([a-zA-Z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com",
            final,
            re.IGNORECASE,
        )
        if m and m.group(1).lower() == slug.lower():
            return {
                "tenant": slug.lower(),
                "server": m.group(2).lower(),
                "board": None,
                "company_name": None,
                "discovery_source": "edgar_probe",
            }
    except Exception:
        pass
    return None


def fetch_edgar_tenants(known: Set[str], limit: Optional[int] = None) -> List[Dict]:
    """
    Download SEC EDGAR company tickers, generate slug candidates, probe each.
    Returns list of confirmed tenant candidates.
    """
    log.info("Downloading SEC EDGAR company tickers...")
    try:
        r = requests.get(
            EDGAR_TICKERS_URL,
            timeout=30,
            headers={"User-Agent": "TenantDiscovery/1.0 jones31luke@gmail.com"},
        )
        r.raise_for_status()
        tickers_data = r.json()
    except Exception as e:
        log.error(f"Failed to download EDGAR tickers: {e}")
        return []

    # Build (name, ticker) pairs — EDGAR returns {"0": {"cik_str":..., "ticker":"AAPL", "title":"Apple Inc."}, ...}
    companies = [
        (v["title"], v["ticker"])
        for v in tickers_data.values()
        if isinstance(v, dict) and "title" in v and "ticker" in v
    ]
    log.info(f"EDGAR: {len(companies)} companies")

    if limit:
        companies = companies[:limit]

    # Generate all slug probes — deduplicate against known
    probes: List[Tuple[str, str]] = []  # (slug, company_name)
    seen_slugs: Set[str] = set(known)

    for name, ticker in companies:
        for slug in _name_to_slugs(name, ticker):
            if slug not in seen_slugs:
                probes.append((slug, name))
                seen_slugs.add(slug)

    log.info(f"EDGAR: {len(probes)} unique slug probes to run")

    found: Dict[str, Dict] = {}
    done = 0

    with ThreadPoolExecutor(max_workers=EDGAR_PROBE_WORKERS) as pool:
        future_to_slug = {
            pool.submit(_probe_slug, slug): (slug, name)
            for slug, name in probes
        }
        for future in as_completed(future_to_slug):
            slug, company_name = future_to_slug[future]
            done += 1
            if done % 500 == 0:
                log.info(f"  EDGAR probe: {done}/{len(probes)} done, {len(found)} confirmed")
            try:
                result = future.result()
                if result:
                    result["company_name"] = company_name
                    found[result["tenant"]] = result
                    log.info(f"  ✅ EDGAR: {company_name} → {result['tenant']}.{result['server']}")
            except Exception as e:
                log.debug(f"  Probe error for {slug}: {e}")

    candidates = list(found.values())
    log.info(f"EDGAR probe: {len(candidates)} confirmed tenants from {len(probes)} slugs")
    return candidates


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        description="Discover Workday tenants from Common Crawl and SEC EDGAR."
    )
    ap.add_argument(
        "--source",
        choices=["all", "commoncrawl", "edgar"],
        default="all",
        help="Which discovery source to run (default: all)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write candidates to workday_tenants_candidates table",
    )
    ap.add_argument(
        "--edgar-limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit EDGAR company list to first N (useful for testing)",
    )
    args = ap.parse_args()

    if not args.apply:
        log.info("DRY RUN — use --apply to write to DB")

    known = load_known_tenants()
    all_candidates: List[Dict] = []

    # Source 1: Common Crawl
    if args.source in ("all", "commoncrawl"):
        log.info("=" * 60)
        log.info("SOURCE 1: Common Crawl CDX")
        log.info("=" * 60)
        cc_candidates = fetch_commoncrawl_tenants(known)
        all_candidates.extend(cc_candidates)
        # Add newly found tenants to known set so EDGAR doesn't re-add them
        for c in cc_candidates:
            known.add(c["tenant"])

    # Source 2: SEC EDGAR
    if args.source in ("all", "edgar"):
        log.info("=" * 60)
        log.info("SOURCE 2: SEC EDGAR company slug probe")
        log.info("=" * 60)
        edgar_candidates = fetch_edgar_tenants(known, limit=args.edgar_limit)
        all_candidates.extend(edgar_candidates)

    # Dedup by tenant within this run
    by_tenant: Dict[str, Dict] = {}
    for c in all_candidates:
        t = c["tenant"]
        if t not in by_tenant:
            by_tenant[t] = c

    final_candidates = list(by_tenant.values())

    # Save to DB
    if args.apply:
        inserted = save_candidates(final_candidates, apply=True)
    else:
        inserted = 0
        save_candidates(final_candidates, apply=False)

    # Summary
    log.info("")
    log.info("=" * 60)
    log.info("DISCOVERY SUMMARY")
    log.info("=" * 60)
    by_source: Dict[str, int] = {}
    for c in final_candidates:
        src = c["discovery_source"]
        by_source[src] = by_source.get(src, 0) + 1
    for src, count in sorted(by_source.items()):
        log.info(f"  {src}: {count} candidates")
    log.info(f"  Total unique new candidates: {len(final_candidates)}")
    if args.apply:
        log.info(f"  Inserted into DB: {inserted}")
    log.info("")
    log.info("Next step: python python/validate_workday_tenants.py --apply")


if __name__ == "__main__":
    main()
