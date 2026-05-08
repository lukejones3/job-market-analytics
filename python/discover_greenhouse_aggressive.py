#!/usr/bin/env python3
"""
discover_greenhouse_aggressive.py

Multi-source aggressive discovery of new Greenhouse job board tenants.
Validates each candidate (1+ US jobs) before writing to DB.
Writes confirmed tenants to ats_tenants_candidates with status='active'.

Sources:
  serper    — 102 query terms × 2 domains (boards + job-boards), 100 results each
  embed     — External careers pages embedding Greenhouse iframes (Serper + HTML fetch)
  builtwith — trends.builtwith.com public list (likely paywalled, graceful skip)
  ct_logs   — crt.sh stub (GH is path-based, not subdomain-based — always empty)

Slug extraction for Serper uses LINK field only (not snippet/title) to prevent
false positives from job descriptions that mention other companies by name.

Usage:
    # Test Source 1 with 100-slug cap
    python python/discover_greenhouse_aggressive.py --source serper --dry-run --limit 100

    # Full per-source production runs (run each separately, review between)
    python python/discover_greenhouse_aggressive.py --source serper    --apply
    python python/discover_greenhouse_aggressive.py --source embed     --apply
    python python/discover_greenhouse_aggressive.py --source builtwith --apply

    # All at once
    python python/discover_greenhouse_aggressive.py --apply
"""

import argparse
import asyncio
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import aiohttp
import psycopg2
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ── Config ────────────────────────────────────────────────────────────────────

SERPER_API_KEY       = os.getenv("SERPER_API_KEY", "")
SERPER_URL           = "https://google.serper.dev/search"
SERPER_DELAY         = 0.3     # seconds between Serper calls (polite pacing)

GLOBAL_CONCURRENCY   = 100    # GH API validation semaphore
PER_HOST_LIMIT       = 8      # aiohttp per-host
SERPER_CONCURRENCY   = 3      # Serper concurrency (conservative)
EMBED_CONCURRENCY    = 100    # external career page fetches
FLUSH_EVERY_N        = 25
FLUSH_EVERY_S        = 30.0
HTTP_TIMEOUT         = aiohttp.ClientTimeout(total=20)

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# ── URL patterns ──────────────────────────────────────────────────────────────

# Extracts slug from Greenhouse board URL link fields (e.g. boards.greenhouse.io/shopify)
_GH_LINK_RE = re.compile(
    r'(?:boards|job-boards)\.greenhouse\.io/([a-z0-9][a-z0-9_-]*)',
    re.I,
)

# Extracts slug from embed iframe/script patterns in HTML
_GH_EMBED_RE = re.compile(
    r'greenhouse\.io/embed/(?:job_app|job_board)\?for=([a-z0-9][a-z0-9_-]*)',
    re.I,
)

# Path segments that are NOT slugs
_SLUG_NOISE = {
    "embed", "v1", "boards", "jobs", "applications", "apply",
    "job", "api", "careers", "about", "help", "support", "info",
}

# ── US location detection (mirrors validate_and_clean_greenhouse_rows.py) ─────

_US_STATE_RE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|"
    r"MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|"
    r"VT|VA|WA|WV|WI|WY|DC)\b"
)
_US_EXPLICIT_RE = re.compile(r"United States|USA\b|U\.S\.A\.|U\.S\.", re.I)


def _is_us_location(loc: str) -> bool:
    return bool(_US_STATE_RE.search(loc) or _US_EXPLICIT_RE.search(loc))


def _ua() -> str:
    return random.choice(USER_AGENTS)


# ── Serper query lists ────────────────────────────────────────────────────────

# Both Greenhouse board domains are searched for every term.
GH_DOMAINS = [
    "site:boards.greenhouse.io",
    "site:job-boards.greenhouse.io",
]

# Query terms (without domain prefix) — each runs against both GH_DOMAINS.
# 102 terms × 2 domains = 204 total Serper queries.
SERPER_QUERY_TERMS = [
    # ── Data / ML / AI ──────────────────────────────────────────────────────
    '"data engineer"',
    '"senior data engineer"',
    '"data scientist"',
    '"senior data scientist"',
    '"machine learning engineer"',
    '"ml engineer"',
    '"analytics engineer"',
    '"applied scientist"',
    '"research scientist"',
    '"data analyst"',
    '"business analyst"',
    '"quantitative analyst"',
    '"AI engineer"',
    '"business intelligence"',
    '"data engineer" "remote"',
    '"data scientist" "remote"',
    '"machine learning" "remote"',
    '"data engineer" "San Francisco"',
    '"data engineer" "New York"',
    '"data scientist" "fintech"',
    '"data engineer" "healthtech"',
    '"data scientist" "biotech"',
    '"analytics" "SaaS"',
    # ── Software Engineering ─────────────────────────────────────────────────
    '"software engineer"',
    '"senior software engineer"',
    '"staff software engineer"',
    '"principal software engineer"',
    '"backend engineer"',
    '"frontend engineer"',
    '"full stack engineer"',
    '"platform engineer"',
    '"infrastructure engineer"',
    '"devops engineer"',
    '"site reliability engineer"',
    '"security engineer"',
    '"mobile engineer"',
    '"ios engineer"',
    '"android engineer"',
    '"solutions architect"',
    '"engineering manager"',
    '"director of engineering"',
    '"software engineer" "remote"',
    '"software engineer" "New York"',
    '"software engineer" "Seattle"',
    '"software engineer" "Austin"',
    '"software engineer" "Boston"',
    '"software engineer" "Chicago"',
    '"software engineer" "Los Angeles"',
    '"software engineer" "Denver"',
    '"software engineer" "Atlanta"',
    '"engineer" "fintech"',
    '"engineer" "cybersecurity"',
    '"engineer" "climate"',
    '"engineer" "defense"',
    '"engineer" "aerospace"',
    '"engineer" "gaming"',
    '"engineer" "edtech"',
    '"engineer" "SaaS"',
    '"engineer" "AI"',
    # ── Product & Design ──────────────────────────────────────────────────────
    '"product manager"',
    '"senior product manager"',
    '"technical program manager"',
    '"product designer"',
    '"ux designer"',
    '"ux researcher"',
    '"graphic designer"',
    '"product manager" "remote"',
    '"product manager" "fintech"',
    '"product manager" "healthtech"',
    # ── Marketing & Growth ────────────────────────────────────────────────────
    '"marketing manager"',
    '"growth marketer"',
    '"content strategist"',
    '"demand generation"',
    '"performance marketing"',
    '"brand manager"',
    '"social media manager"',
    '"content writer"',
    '"copywriter"',
    '"seo manager"',
    # ── Sales & Business Development ─────────────────────────────────────────
    '"account executive"',
    '"enterprise account executive"',
    '"sales development representative"',
    '"business development"',
    '"partnerships manager"',
    '"revenue operations"',
    '"sales operations"',
    '"sales manager"',
    '"account manager"',
    '"account executive" "remote"',
    '"account executive" "New York"',
    '"account executive" "San Francisco"',
    # ── Finance & Accounting ──────────────────────────────────────────────────
    '"financial analyst"',
    '"finance manager"',
    '"controller"',
    '"fp&a"',
    '"accounting manager"',
    '"treasury analyst"',
    # ── Operations & Program Management ──────────────────────────────────────
    '"operations manager"',
    '"operations analyst"',
    '"chief of staff"',
    '"program manager"',
    '"project manager"',
    '"supply chain"',
    '"logistics manager"',
    # ── People & HR ───────────────────────────────────────────────────────────
    '"talent acquisition"',
    '"recruiter"',
    '"hr manager"',
    '"people operations"',
    '"compensation analyst"',
    '"hr business partner"',
    # ── Customer Success & Support ────────────────────────────────────────────
    '"customer success manager"',
    '"customer success"',
    '"support engineer"',
    '"technical support"',
    '"customer experience"',
    '"implementation manager"',
    # ── Specialized / Industry ────────────────────────────────────────────────
    '"solutions engineer"',
    '"registered nurse" "United States"',
    '"clinical" "United States"',
    '"nurse practitioner" "United States"',
    '"policy analyst"',
    '"general counsel"',
    '"attorney"',
    '"research scientist" "United States"',
    '"pharmaceutical" "scientist"',
    '"editor"',
    # ── Broad sweeps ─────────────────────────────────────────────────────────
    '"United States" "full time"',
    '"remote" "United States" "engineer"',
    '"equity" "benefits" "engineer"',
    '"series b" "engineer"',
    '"series c" "engineer"',
]  # 102 terms × 2 domains = 204 total queries

# Serper queries to find external careers pages that embed Greenhouse.
# 20 queries × 100 results = up to 2,000 external career page URLs to scan.
EMBED_SERPER_QUERIES = [
    # Official attribution text — most reliable signal
    '"powered by Greenhouse" apply -site:greenhouse.io',
    '"powered by Greenhouse" jobs -site:greenhouse.io',
    '"powered by Greenhouse" careers -site:greenhouse.io',
    '"powered by Greenhouse" "open roles" -site:greenhouse.io',
    '"powered by Greenhouse" engineering -site:greenhouse.io',
    '"powered by Greenhouse" "view all jobs" -site:greenhouse.io',
    # Direct embed URL fragments in page source
    '"boards.greenhouse.io/embed" -site:greenhouse.io',
    '"greenhouse.io/embed/job_board" -site:greenhouse.io',
    '"greenhouse.io/embed/job_app" -site:greenhouse.io',
    '"greenhouse.io/embed" careers -site:greenhouse.io',
    # Greenhouse JS SDK token variables
    '"Grnhse.Settings.Token" -site:greenhouse.io',
    '"data-gh-token" -site:greenhouse.io',
    '"GH_TOKEN" careers -site:greenhouse.io',
    # inurl career pages mentioning Greenhouse
    'inurl:careers "greenhouse" -site:greenhouse.io site:.com',
    'inurl:jobs "greenhouse" -site:greenhouse.io site:.com',
    # Apply-flow copy
    '"apply with greenhouse" -site:greenhouse.io',
    '"apply via greenhouse" -site:greenhouse.io',
    '"jobs powered by greenhouse" -site:greenhouse.io',
    # Broader open-positions pages
    '"greenhouse" "open positions" apply -site:greenhouse.io -site:linkedin.com',
    '"greenhouse" "we are hiring" careers -site:greenhouse.io',
]

# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def load_known() -> Set[str]:
    """All Greenhouse slugs we already know about (candidates table + discovered_companies)."""
    known: Set[str] = set()
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT tenant FROM ats_tenants_candidates WHERE ats = 'greenhouse'")
        for (t,) in cur.fetchall():
            known.add(t.lower())
        cur.execute(
            "SELECT board_token FROM discovered_companies "
            "WHERE ats_source = 'greenhouse' AND board_token IS NOT NULL"
        )
        for (bt,) in cur.fetchall():
            slug = bt.strip().split("/")[0].lower()
            if slug:
                known.add(slug)
        cur.close()
        conn.close()
    except Exception as e:
        log.warning(f"Could not load known tenants: {e}")
    log.info(f"Loaded {len(known)} known Greenhouse tenants (will skip these)")
    return known


def _save_sync(candidates: List[Dict]) -> int:
    """Upsert validated tenants. Returns rows inserted."""
    if not candidates:
        return 0
    conn = get_conn()
    cur = conn.cursor()
    inserted = 0
    for c in candidates:
        try:
            cur.execute(
                """
                INSERT INTO ats_tenants_candidates
                    (ats, tenant, server, source, us_jobs_count, status, last_validated_at)
                VALUES ('greenhouse', %s, NULL, %s, %s, 'active', now())
                ON CONFLICT (ats, tenant) DO NOTHING
                """,
                (c["tenant"], c["source"], c["us_jobs_count"]),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            log.warning(f"  Insert failed {c.get('tenant')}: {e}")
            try:
                conn.rollback()
            except Exception:
                pass
            continue
    conn.commit()
    cur.close()
    conn.close()
    return inserted


# ── Incremental saver ─────────────────────────────────────────────────────────

class IncrementalSaver:
    def __init__(self, apply: bool, source: str):
        self.apply  = apply
        self.source = source
        self._pending:       List[Dict] = []
        self._last_flush:    float      = time.monotonic()
        self._total_inserted: int       = 0
        self._lock = asyncio.Lock()

    async def add_batch(self, batch: List[Dict]) -> None:
        async with self._lock:
            self._pending.extend(batch)
            if self._should_flush():
                await self._flush()

    async def flush_final(self) -> int:
        async with self._lock:
            await self._flush()
        return self._total_inserted

    def _should_flush(self) -> bool:
        return (
            len(self._pending) >= FLUSH_EVERY_N
            or (time.monotonic() - self._last_flush) >= FLUSH_EVERY_S
        )

    async def _flush(self) -> None:
        if not self._pending:
            self._last_flush = time.monotonic()
            return
        batch = self._pending[:]
        self._pending.clear()
        self._last_flush = time.monotonic()
        if self.apply:
            loop = asyncio.get_running_loop()
            n = await loop.run_in_executor(None, _save_sync, batch)
            self._total_inserted += n
            log.info(f"  [flush] +{n} saved (running total: {self._total_inserted})")
        else:
            # Dry-run: just count, don't spam — sample shown in final report
            self._total_inserted += len(batch)
            log.debug(f"  [DRY flush] +{len(batch)} (total dry: {self._total_inserted})")


# ── Greenhouse API probe ──────────────────────────────────────────────────────

async def probe_slug(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    slug: str,
) -> int:
    """Returns US job count for slug (0 = invalid or no US jobs)."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
    backoff = 1.0
    for attempt in range(4):
        wait_secs: Optional[float] = None
        try:
            async with semaphore:
                async with session.get(
                    url,
                    headers={"Accept": "application/json", "User-Agent": _ua()},
                    timeout=HTTP_TIMEOUT,
                ) as resp:
                    status = resp.status
                    if status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_secs = min(float(retry_after) if retry_after else backoff, 60.0)
                    elif status == 200:
                        data = await resp.json(content_type=None)
                        return sum(
                            1 for j in data.get("jobs", [])
                            if _is_us_location(j.get("location", {}).get("name", ""))
                        )
                    else:
                        return 0
            if wait_secs is not None and attempt < 3:
                await asyncio.sleep(wait_secs)
                backoff *= 2
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < 3:
                await asyncio.sleep(backoff)
                backoff *= 2
    return 0


async def validate_and_save(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    raw_slugs: List[str],
    known: Set[str],
    source: str,
    saver: IncrementalSaver,
) -> Tuple[int, int, int, List[Dict]]:
    """
    Validate raw_slugs concurrently against the GH API.
    Saves candidates with 1+ US jobs via saver.
    Returns (n_valid, n_no_us, n_skipped_known, sample_of_valid).
    Mutates `known` so subsequent sources don't re-probe the same slug.
    """
    new_slugs = [s for s in raw_slugs if s.lower() not in known]
    skipped   = len(raw_slugs) - len(new_slugs)

    if not new_slugs:
        log.info(f"  All {skipped} slugs already known — nothing to validate")
        return 0, 0, skipped, []

    log.info(f"  Validating {len(new_slugs)} slugs ({skipped} already known)…")
    t0 = time.monotonic()

    # Chunk into batches of 500 to give progress updates on large runs
    CHUNK = 500
    valid_batch: List[Dict] = []
    n_valid = n_no_us = 0

    for start in range(0, len(new_slugs), CHUNK):
        chunk = new_slugs[start:start + CHUNK]
        results = await asyncio.gather(
            *[probe_slug(session, semaphore, s) for s in chunk],
            return_exceptions=True,
        )
        for slug, us_jobs in zip(chunk, results):
            known.add(slug.lower())
            if isinstance(us_jobs, Exception) or not isinstance(us_jobs, int):
                us_jobs = 0
            if us_jobs >= 1:
                n_valid += 1
                valid_batch.append({"tenant": slug, "us_jobs_count": us_jobs, "source": source})
            else:
                n_no_us += 1
        elapsed_so_far = time.monotonic() - t0
        log.info(
            f"  … validated {min(start + CHUNK, len(new_slugs))}/{len(new_slugs)}  "
            f"valid_so_far={n_valid}  [{elapsed_so_far:.0f}s]"
        )

    if valid_batch:
        await saver.add_batch(valid_batch)

    elapsed = time.monotonic() - t0
    log.info(
        f"  Validation done: {n_valid} valid  {n_no_us} no-US-jobs  "
        f"{skipped} already-known  [{elapsed:.0f}s]"
    )
    # Return a sample sorted by us_jobs_count descending for the final report
    sample = sorted(valid_batch, key=lambda x: x["us_jobs_count"], reverse=True)[:20]
    return n_valid, n_no_us, skipped, sample


# ── Source 1: Serper ──────────────────────────────────────────────────────────

def _slug_from_link(url: str) -> Optional[str]:
    """Extract GH slug from a URL's link field. Link-only (no snippet/title)."""
    m = _GH_LINK_RE.search(url)
    if not m:
        return None
    slug = m.group(1).lower().split("/")[0].split("?")[0]
    return slug if slug not in _SLUG_NOISE and len(slug) >= 2 else None


async def collect_serper_slugs(
    session: aiohttp.ClientSession,
    serper_sem: asyncio.Semaphore,
    known: Set[str],
    limit: Optional[int],
) -> List[str]:
    """
    Run each SERPER_QUERY_TERMS term against both GH_DOMAINS (204 queries total).
    Uses num=100 per query. Extracts slugs from result link fields only.
    """
    raw: Set[str] = set()
    total_queries = len(SERPER_QUERY_TERMS) * len(GH_DOMAINS)
    i = 0

    for term in SERPER_QUERY_TERMS:
        for domain in GH_DOMAINS:
            if limit and len(raw) >= limit:
                log.info(f"  --limit {limit} reached after {i} queries")
                return list(raw)

            query = f"{domain} {term}"
            i += 1
            results = []

            async with serper_sem:
                try:
                    async with session.post(
                        SERPER_URL,
                        headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                        json={"q": query, "num": 100, "gl": "us", "hl": "en"},
                        timeout=aiohttp.ClientTimeout(total=20),
                    ) as r:
                        if r.status == 429:
                            log.warning("  Serper 429 — waiting 30s…")
                            await asyncio.sleep(30)
                        elif r.status == 200:
                            data = await r.json(content_type=None)
                            results = data.get("organic", [])
                        else:
                            log.warning(f"  Serper HTTP {r.status}: {query[:65]}")
                except Exception as e:
                    log.warning(f"  Serper error ({query[:65]}): {e}")

            new_this = 0
            for result in results:
                slug = _slug_from_link(result.get("link", ""))
                if slug and slug not in raw and slug not in known:
                    raw.add(slug)
                    new_this += 1

            log.info(
                f"  [{i:3d}/{total_queries}] +{new_this:3d} new  "
                f"total_raw={len(raw):5d}  '{query[:60]}'"
            )
            await asyncio.sleep(SERPER_DELAY)

    log.info(f"Serper: {len(raw)} unique raw slugs from {i} queries")
    return list(raw)


# ── Source 4: External embed pages ───────────────────────────────────────────

# Additional slug patterns for external HTML pages (more permissive than link-only)
_GH_HTML_PATTERNS: List[re.Pattern] = [
    # iframe / script: ?for=slug or ?for=slug&
    re.compile(r'greenhouse\.io/embed/(?:job_app|job_board)\?for=([a-z0-9][a-z0-9_-]*)', re.I),
    # script src: /embed/{slug}/jobs or /embed/{slug}/board
    re.compile(r'greenhouse\.io/embed/([a-z0-9][a-z0-9_-]*)/(?:jobs|board)', re.I),
    # any boards.greenhouse.io or job-boards.greenhouse.io href / src
    re.compile(r'(?:boards|job-boards)\.greenhouse\.io/([a-z0-9][a-z0-9_-]*)', re.I),
    # Greenhouse JS SDK: Grnhse.Settings.Token = 'slug' or Grnhse.Setup.Token = "slug"
    re.compile(r'Grnhse\.\w+\.Token\s*=\s*["\']([a-z0-9][a-z0-9_-]*)["\']', re.I),
    # Generic GH_TOKEN assignment
    re.compile(r'GH_TOKEN\s*=\s*["\']([a-z0-9][a-z0-9_-]*)["\']', re.I),
    # data-gh-token attribute
    re.compile(r'data-gh-token=["\']([a-z0-9][a-z0-9_-]*)["\']', re.I),
    # data-greenhouse-token or data-board-token attributes
    re.compile(r'data-(?:greenhouse-token|board-token)=["\']([a-z0-9][a-z0-9_-]*)["\']', re.I),
    # GreenhouseConfig / window.ghConfig etc: board: 'slug' or token: 'slug'
    re.compile(
        r'(?:GreenhouseConfig|ghConfig|gh_config)\s*[=:]\s*\{[^}]{0,200}?'
        r'(?:board|token)\s*[=:]\s*["\']([a-z0-9][a-z0-9_-]*)["\']',
        re.I | re.S,
    ),
]


def _slugs_from_html(html: str) -> Set[str]:
    """Extract GH board slugs from an external career page's raw HTML."""
    found: Set[str] = set()
    for pattern in _GH_HTML_PATTERNS:
        for m in pattern.finditer(html):
            slug = m.group(1).lower().split("/")[0].split("?")[0]
            if slug not in _SLUG_NOISE and len(slug) >= 2:
                found.add(slug)
    return found


async def _fetch_page_slugs(
    session: aiohttp.ClientSession,
    embed_sem: asyncio.Semaphore,
    url: str,
) -> Set[str]:
    try:
        async with embed_sem:
            async with session.get(
                url,
                headers={"User-Agent": _ua()},
                timeout=aiohttp.ClientTimeout(total=15),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    return set()
                raw = await resp.content.read(200_000)
                return _slugs_from_html(raw.decode("utf-8", errors="replace"))
    except Exception:
        return set()


async def collect_embed_slugs(
    session: aiohttp.ClientSession,
    serper_sem: asyncio.Semaphore,
    embed_sem: asyncio.Semaphore,
    known: Set[str],
    limit: Optional[int],
) -> List[str]:
    """
    Phase 1: Serper finds external career page URLs (not on greenhouse.io).
    Phase 2: Fetch each page, extract embedded Greenhouse board slugs.
    """
    career_urls: List[str] = []
    for query in EMBED_SERPER_QUERIES:
        async with serper_sem:
            try:
                async with session.post(
                    SERPER_URL,
                    headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                    json={"q": query, "num": 100, "gl": "us", "hl": "en"},
                    timeout=aiohttp.ClientTimeout(total=20),
                ) as r:
                    if r.status == 200:
                        data = await r.json(content_type=None)
                        for result in data.get("organic", []):
                            link = result.get("link", "")
                            if link and "greenhouse.io" not in link:
                                career_urls.append(link)
                    else:
                        log.warning(f"  Embed Serper HTTP {r.status}: {query[:60]}")
            except Exception as e:
                log.warning(f"  Embed Serper error: {e}")
        await asyncio.sleep(SERPER_DELAY)

    log.info(f"  Embed: {len(career_urls)} external career pages to scan")

    raw: Set[str] = set()
    page_results = await asyncio.gather(
        *[_fetch_page_slugs(session, embed_sem, url) for url in career_urls],
        return_exceptions=True,
    )
    for slug_set in page_results:
        if isinstance(slug_set, set):
            for slug in slug_set:
                if slug not in known:
                    raw.add(slug)
                    if limit and len(raw) >= limit:
                        break

    log.info(f"Embed: {len(raw)} unique new raw slugs from {len(career_urls)} pages")
    return list(raw)


# ── Source 2: BuiltWith ───────────────────────────────────────────────────────

async def collect_builtwith_slugs(
    session: aiohttp.ClientSession,
    known: Set[str],
) -> List[str]:
    """
    Attempt trends.builtwith.com/widgets/Greenhouse.
    Almost always paywalled or JS-rendered — gracefully returns [] on failure.
    """
    for url in [
        "https://trends.builtwith.com/widgets/Greenhouse",
        "https://builtwith.com/greenhouse",
    ]:
        try:
            async with session.get(
                url,
                headers={"User-Agent": _ua()},
                timeout=aiohttp.ClientTimeout(total=20),
                allow_redirects=True,
            ) as resp:
                if resp.status != 200:
                    log.info(f"  BuiltWith: HTTP {resp.status} at {url} — skipping")
                    continue
                html = (await resp.content.read(500_000)).decode("utf-8", errors="replace")
                slugs = {
                    m.group(1).lower()
                    for m in _GH_LINK_RE.finditer(html)
                    if m.group(1).lower() not in _SLUG_NOISE
                    and len(m.group(1)) >= 2
                    and m.group(1).lower() not in known
                }
                if slugs:
                    log.info(f"  BuiltWith: {len(slugs)} slugs found at {url}")
                    return list(slugs)
                log.info(f"  BuiltWith: page loaded but no GH slugs (likely JS-rendered) — skipping")
        except Exception as e:
            log.info(f"  BuiltWith: {url} failed ({e}) — skipping")

    log.info("BuiltWith: 0 slugs (paywalled or JS-rendered)")
    return []


# ── Source 3: CT logs (stub) ──────────────────────────────────────────────────

async def collect_ct_slugs(
    session: aiohttp.ClientSession,
    known: Set[str],
) -> List[str]:
    """
    Certificate Transparency logs via crt.sh.
    Greenhouse uses path-based tenant URLs (boards.greenhouse.io/{slug}),
    NOT per-tenant subdomains — so crt.sh likely has nothing useful.
    We try both query syntaxes with a 180s timeout and report what we get.
    """
    ct_timeout = aiohttp.ClientTimeout(total=180)
    urls = [
        "https://crt.sh/?q=%25.greenhouse.io&output=json",
        "https://crt.sh/?Identity=%25.greenhouse.io&output=json",
    ]
    _INFRA_RE = re.compile(
        r'^(?:www|api|boards|job-boards|app|status|mail|smtp|'
        r'autodiscover|help|support|cdn|static|\*)\.',
        re.I,
    )
    for url in urls:
        log.info(f"  CT logs: trying {url} …")
        try:
            async with session.get(
                url,
                headers={"User-Agent": _ua(), "Accept": "application/json"},
                timeout=ct_timeout,
            ) as r:
                if r.status != 200:
                    log.info(f"  CT logs: HTTP {r.status} — trying next")
                    continue
                entries = await r.json(content_type=None)
                if not entries:
                    log.info("  CT logs: empty response (confirmed: GH uses shared domains — nothing to extract)")
                    return []
                names = {
                    n.strip().lower()
                    for e in entries
                    for n in e.get("name_value", "").split("\n")
                    if n.strip() and "greenhouse.io" in n.lower()
                }
                log.info(f"  CT logs: {len(names)} raw name_value entries")
                tenants = [
                    n.split(".")[0]
                    for n in names
                    if not _INFRA_RE.match(n) and n.count(".") == 2
                ]
                new = [t for t in tenants if t and t not in known and len(t) >= 2]
                log.info(f"  CT logs: {len(new)} potential new slugs")
                return new
        except asyncio.TimeoutError:
            log.info("  CT logs: 180s timeout — GH cert set likely too large or crt.sh overloaded. Skipping.")
        except Exception as e:
            log.info(f"  CT logs: error ({e}) — skipping")

    log.info("CT logs: 0 slugs (GH uses path-based boards, no useful subdomain certs)")
    return []


# ── Main ──────────────────────────────────────────────────────────────────────

ALL_SOURCES = ["serper", "embed", "builtwith", "ct_logs"]


async def run(sources: List[str], apply: bool, limit: Optional[int]) -> None:
    known = load_known()

    semaphore  = asyncio.Semaphore(GLOBAL_CONCURRENCY)
    serper_sem = asyncio.Semaphore(SERPER_CONCURRENCY)
    embed_sem  = asyncio.Semaphore(EMBED_CONCURRENCY)

    connector = aiohttp.TCPConnector(
        limit=GLOBAL_CONCURRENCY,
        limit_per_host=PER_HOST_LIMIT,
        enable_cleanup_closed=True,
    )

    # stats: source → {raw, valid, no_us, skipped, inserted}
    stats: Dict[str, Dict[str, int]] = {}
    t_start = time.monotonic()

    async with aiohttp.ClientSession(connector=connector) as session:

        all_samples: List[Dict] = []

        if "serper" in sources:
            _hdr("SOURCE 1: Serper (boards.greenhouse.io / job-boards.greenhouse.io)")
            src = "greenhouse_aggressive_serper"
            saver = IncrementalSaver(apply, src)
            raw   = await collect_serper_slugs(session, serper_sem, known, limit)
            v, nu, sk, sample = await validate_and_save(session, semaphore, raw, known, src, saver)
            ins = await saver.flush_final()
            stats["serper"] = {"raw": len(raw), "valid": v, "no_us": nu, "skipped": sk, "inserted": ins}
            all_samples.extend(sample)

        if "embed" in sources:
            _hdr("SOURCE 4: External embed pages")
            src = "greenhouse_aggressive_embed"
            saver = IncrementalSaver(apply, src)
            raw   = await collect_embed_slugs(session, serper_sem, embed_sem, known, limit)
            v, nu, sk, sample = await validate_and_save(session, semaphore, raw, known, src, saver)
            ins = await saver.flush_final()
            stats["embed"] = {"raw": len(raw), "valid": v, "no_us": nu, "skipped": sk, "inserted": ins}
            all_samples.extend(sample)

        if "builtwith" in sources:
            _hdr("SOURCE 2: BuiltWith")
            src = "greenhouse_aggressive_builtwith"
            saver = IncrementalSaver(apply, src)
            raw   = await collect_builtwith_slugs(session, known)
            v, nu, sk, sample = await validate_and_save(session, semaphore, raw, known, src, saver)
            ins = await saver.flush_final()
            stats["builtwith"] = {"raw": len(raw), "valid": v, "no_us": nu, "skipped": sk, "inserted": ins}
            all_samples.extend(sample)

        if "ct_logs" in sources:
            _hdr("SOURCE 3: CT logs")
            src = "greenhouse_aggressive_ct"
            saver = IncrementalSaver(apply, src)
            raw   = await collect_ct_slugs(session, known)
            v, nu, sk, sample = await validate_and_save(session, semaphore, raw, known, src, saver)
            ins = await saver.flush_final()
            stats["ct_logs"] = {"raw": len(raw), "valid": v, "no_us": nu, "skipped": sk, "inserted": ins}
            all_samples.extend(sample)

    elapsed = time.monotonic() - t_start
    _hdr("FINAL REPORT")
    total_raw = total_valid = total_ins = 0
    queries_run = len(SERPER_QUERY_TERMS) * len(GH_DOMAINS) if "serper" in sources else 0
    log.info(f"  Serper queries run : {queries_run} ({len(SERPER_QUERY_TERMS)} terms × {len(GH_DOMAINS)} domains, 100 results each)")
    log.info(f"  Max URLs scanned   : ~{queries_run * 100:,}")
    log.info("")
    for src, s in stats.items():
        pct = f"{100*s['valid']//s['raw']}%" if s["raw"] else "—"
        log.info(
            f"  {src:12}  raw={s['raw']:5}  valid={s['valid']:5} ({pct:>4})  "
            f"no_us={s['no_us']:5}  skipped={s['skipped']:5}  saved={s['inserted']:5}"
        )
        total_raw   += s["raw"]
        total_valid += s["valid"]
        total_ins   += s["inserted"]
    total_pct = f"{100*total_valid//total_raw}%" if total_raw else "—"
    log.info(f"  {'TOTAL':12}  raw={total_raw:5}  valid={total_valid:5} ({total_pct:>4})  saved={total_ins:5}")
    log.info(f"  Elapsed: {elapsed:.0f}s")
    if all_samples:
        log.info("")
        log.info("  Sample valid tenants (top 20 by US job count):")
        top20 = sorted(all_samples, key=lambda x: x["us_jobs_count"], reverse=True)[:20]
        for c in top20:
            log.info(f"    {c['tenant']:40}  {c['us_jobs_count']:5} US jobs")
    if not apply:
        log.info("")
        log.info("  DRY RUN — re-run with --apply to write to DB")


def _hdr(msg: str) -> None:
    log.info("")
    log.info("=" * 62)
    log.info(f"  {msg}")
    log.info("=" * 62)


def main():
    ap = argparse.ArgumentParser(
        description="Aggressive multi-source Greenhouse tenant discovery with inline validation."
    )
    ap.add_argument(
        "--source",
        choices=ALL_SOURCES + ["all"],
        default="all",
        help="Source to run (default: all)",
    )
    ap.add_argument("--apply",   action="store_true", help="Write confirmed candidates to DB")
    ap.add_argument("--dry-run", action="store_true", help="Print candidates without writing (default)")
    ap.add_argument("--limit",   type=int, default=None,
                    help="Max raw slugs collected per source — use for testing")
    args = ap.parse_args()

    apply   = args.apply and not args.dry_run
    sources = ALL_SOURCES if args.source == "all" else [args.source]

    if not apply:
        log.info("DRY RUN — pass --apply to write to DB")

    asyncio.run(run(sources, apply, args.limit))


if __name__ == "__main__":
    main()
