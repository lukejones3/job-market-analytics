#!/usr/bin/env python3
"""
discover_lever_aggressive.py

Aggressively discovers new Lever tenants via Serper site:jobs.lever.co queries.
Validates each candidate via the Lever public API before writing to DB.

Source 1 — Serper:
    ~110 queries × 100 results against site:jobs.lever.co
    Slug extracted from link field only (never snippet/title).

Validation:
    GET https://api.lever.co/v0/postings/{slug}?mode=json
    Keep if response is a non-empty list (board exists with at least 1 posting).
    US jobs counted from categories.location / categories.allLocations.

DB writes:
    INSERT INTO ats_tenants_candidates (ats, tenant, source, us_jobs_count, status, last_validated_at)
    ON CONFLICT (ats, tenant) DO NOTHING

Usage:
    python python/discover_lever_aggressive.py --dry-run
    python python/discover_lever_aggressive.py --apply
"""

import argparse
import asyncio
import logging
import os
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

CONCURRENCY       = 100     # Lever API validation
SERPER_CONCURRENCY = 3      # polite Serper pacing
PER_HOST_LIMIT    = 10
FLUSH_EVERY       = 25
HTTP_TIMEOUT      = aiohttp.ClientTimeout(total=20)
MAX_RETRIES       = 4
BASE_BACKOFF      = 1.0
SERPER_RESULTS    = 100     # results per query (max Serper supports)
CHUNK_SIZE        = 500     # validation chunk size for progress feedback

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL     = "https://google.serper.dev/search"

_GH_HEADERS = {
    "Accept":     "application/json",
    "User-Agent": "job-market-analytics/1.0 (research; contact: jones31luke@gmail.com)",
}

# ── Lever slug extraction ─────────────────────────────────────────────────────

# Extract company slug from jobs.lever.co/{slug}[/{job-uuid}]
_LEVER_LINK_RE = re.compile(
    r'jobs\.lever\.co/([a-zA-Z0-9][a-zA-Z0-9_-]*)',
    re.I,
)

_SLUG_NOISE: Set[str] = {
    "", "jobs", "postings", "api", "embed", "apply", "board",
    "careers", "team", "hiring", "work", "lever", "co", "view",
    "open", "v0", "v1", "www", "http", "https",
}


def _slug_from_link(url: str) -> Optional[str]:
    """Extract company slug from a jobs.lever.co URL (link field only)."""
    m = _LEVER_LINK_RE.search(url)
    if not m:
        return None
    slug = m.group(1).lower().split("/")[0].split("?")[0].strip()
    if slug in _SLUG_NOISE or len(slug) < 2:
        return None
    # Filter UUIDs (job IDs look like 8char-4char-4char-4char-12char hex)
    if re.fullmatch(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}', slug):
        return None
    return slug


# ── Serper queries ────────────────────────────────────────────────────────────

_SERPER_QUERY_TERMS = [
    # Software engineering — generic
    "software engineer",
    "senior software engineer",
    "staff engineer",
    "principal engineer",
    "engineering manager",
    "backend engineer",
    "frontend engineer",
    "full stack engineer",
    "fullstack developer",
    # Backend / infrastructure
    "backend developer",
    "infrastructure engineer",
    "platform engineer",
    "devops engineer",
    "site reliability engineer",
    "cloud engineer",
    "distributed systems engineer",
    "systems engineer",
    "reliability engineer",
    # Frontend / mobile
    "frontend developer",
    "React developer",
    "iOS engineer",
    "Android engineer",
    "mobile developer",
    "React Native developer",
    # Security
    "security engineer",
    "application security engineer",
    "security researcher",
    "cybersecurity engineer",
    "penetration tester",
    # Data / ML / AI
    "data engineer",
    "data scientist",
    "senior data scientist",
    "machine learning engineer",
    "ML engineer",
    "AI engineer",
    "applied scientist",
    "research scientist",
    "data analyst",
    "analytics engineer",
    "business intelligence engineer",
    "quantitative analyst",
    "computer vision engineer",
    "NLP engineer",
    "deep learning engineer",
    "AI researcher",
    "data science manager",
    # Languages / frameworks
    "Python engineer",
    "Go engineer",
    "Rust engineer",
    "Java engineer",
    "Kotlin developer",
    "TypeScript engineer",
    "Elixir developer",
    "Scala engineer",
    "C++ engineer",
    # Product / design
    "product manager",
    "senior product manager",
    "principal product manager",
    "group product manager",
    "director of product",
    "head of product",
    "product designer",
    "UX designer",
    "UI designer",
    "UX researcher",
    "design manager",
    "staff product designer",
    # Marketing / growth
    "growth engineer",
    "product marketing manager",
    "marketing manager",
    "content marketing manager",
    "demand generation manager",
    "SEO manager",
    "performance marketing manager",
    "brand manager",
    "digital marketing manager",
    "growth marketing manager",
    "VP marketing",
    "director of marketing",
    # Sales / BD
    "account executive",
    "senior account executive",
    "enterprise account executive",
    "sales development representative",
    "solutions engineer",
    "solutions architect",
    "customer success manager",
    "business development manager",
    "sales manager",
    "account manager",
    "partnership manager",
    "revenue operations manager",
    "VP sales",
    "director of sales",
    # Finance / ops / HR
    "finance manager",
    "financial analyst",
    "accounting manager",
    "controller",
    "FP&A analyst",
    "recruiting manager",
    "people operations manager",
    "HR business partner",
    "talent acquisition",
    "operations manager",
    "chief of staff",
    "program manager",
    "project manager",
    "strategy and operations",
    # Customer success / support
    "customer success",
    "technical support engineer",
    "customer support manager",
    "implementation manager",
    "solutions consultant",
    # Legal / policy / clinical
    "general counsel",
    "legal counsel",
    "policy manager",
    "regulatory affairs",
    "clinical operations",
    "clinical research associate",
    # Location combos
    "software engineer remote",
    "engineer San Francisco",
    "engineer New York",
    "engineer Seattle",
    "engineer Austin",
    "engineer Boston",
    "engineer Chicago",
    "engineer Denver",
    "engineer Atlanta",
    "engineer Los Angeles",
    "engineer Washington DC",
    "engineer Miami",
    "remote United States",
    # Industry combos
    "fintech engineer",
    "healthtech engineer",
    "biotech engineer",
    "legaltech",
    "proptech",
    "insurtech",
    "edtech",
    "climate tech",
    "cleantech",
    "crypto engineer",
    "blockchain developer",
    "web3 engineer",
    "gaming engineer",
    "defense engineer",
    "aerospace engineer",
    "SaaS engineer",
    # Seniority sweeps
    "VP engineering",
    "director of engineering",
    "CTO",
    "head of engineering",
    "senior director",
    "technical lead",
    "tech lead",
    # Broad sweeps
    "hiring engineer",
    "join our team engineer",
    "open roles engineer",
    "startup engineer",
    "series A engineer",
    "series B engineer",
]

# ── US location detection ─────────────────────────────────────────────────────

_US_STATE_RE = re.compile(
    r"\b(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|"
    r"MI|MN|MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|"
    r"VT|VA|WA|WV|WI|WY|DC)\b"
)
_US_EXPLICIT_RE = re.compile(r"United States|USA\b|U\.S\.A\.|U\.S\.", re.I)


def _is_us_location(location: str) -> bool:
    return bool(_US_STATE_RE.search(location) or _US_EXPLICIT_RE.search(location))


def _count_us_jobs(postings: List[dict]) -> int:
    count = 0
    for job in postings:
        cats = job.get("categories", {})
        all_locs = cats.get("allLocations") or []
        if all_locs:
            if any(_is_us_location(loc) for loc in all_locs):
                count += 1
        else:
            if _is_us_location(cats.get("location", "")):
                count += 1
    return count


# ── DB ────────────────────────────────────────────────────────────────────────

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def load_known_slugs() -> Set[str]:
    """Return all already-known Lever slugs (candidates + discovered_companies)."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT tenant FROM ats_tenants_candidates WHERE ats = 'lever'")
    slugs = {r[0].lower() for r in cur.fetchall()}
    cur.execute("SELECT board_token FROM discovered_companies WHERE ats_source = 'lever'")
    for (bt,) in cur.fetchall():
        s = bt.strip().strip("/").split("/")[0].split("?")[0].strip().lower()
        if s:
            slugs.add(s)
    cur.close()
    conn.close()
    log.info(f"Loaded {len(slugs)} known Lever slugs (skip list)")
    return slugs


# ── Incremental DB saver ──────────────────────────────────────────────────────

class IncrementalSaver:
    """Async-safe buffer; flushes to DB every FLUSH_EVERY records or 30 seconds."""

    def __init__(self, apply: bool):
        self.apply    = apply
        self._buf:    List[Dict] = []
        self._lock    = asyncio.Lock()
        self._last_flush = time.monotonic()
        self.total_saved = 0

    async def add(self, candidates: List[Dict]) -> None:
        async with self._lock:
            self._buf.extend(candidates)
            elapsed = time.monotonic() - self._last_flush
            if len(self._buf) >= FLUSH_EVERY or elapsed >= 30:
                await self._flush()

    async def flush_final(self) -> None:
        async with self._lock:
            if self._buf:
                await self._flush()

    async def _flush(self) -> None:
        if not self._buf:
            return
        batch = self._buf[:]
        self._buf.clear()
        self._last_flush = time.monotonic()

        if not self.apply:
            self.total_saved += len(batch)
            return

        n = _save_sync(batch)
        self.total_saved += n


def _save_sync(candidates: List[Dict]) -> int:
    if not candidates:
        return 0
    conn = get_conn()
    cur = conn.cursor()
    saved = 0
    try:
        for c in candidates:
            try:
                cur.execute("SAVEPOINT sp")
                cur.execute(
                    """
                    INSERT INTO ats_tenants_candidates
                        (ats, tenant, server, source, us_jobs_count, status, last_validated_at)
                    VALUES ('lever', %s, NULL, %s, %s, 'active', now())
                    ON CONFLICT (ats, tenant) DO NOTHING
                    """,
                    (c["tenant"], c["source"], c["us_jobs"]),
                )
                if cur.rowcount:
                    saved += 1
                cur.execute("RELEASE SAVEPOINT sp")
            except Exception as e:
                cur.execute("ROLLBACK TO SAVEPOINT sp")
                log.warning(f"  !! Save error for {c['tenant']}: {e}")
        conn.commit()
    except Exception as e:
        log.error(f"  DB flush error: {e}")
        conn.rollback()
    finally:
        cur.close()
        conn.close()
    return saved


# ── HTTP helpers ──────────────────────────────────────────────────────────────

async def _fetch_json(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    url: str,
    *,
    method: str = "GET",
    json_body: Optional[dict] = None,
    headers: Optional[dict] = None,
) -> Tuple[Optional[int], Optional[object]]:
    """Generic fetch with 429 backoff. Semaphore released before sleeping."""
    backoff = BASE_BACKOFF
    _hdrs = headers or _GH_HEADERS
    for attempt in range(MAX_RETRIES + 1):
        wait_secs: Optional[float] = None
        try:
            async with semaphore:
                req = session.get if method == "GET" else session.post
                kwargs: dict = {"headers": _hdrs, "timeout": HTTP_TIMEOUT}
                if json_body is not None:
                    kwargs["json"] = json_body
                async with req(url, **kwargs) as resp:
                    status = resp.status
                    if status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_secs = min(float(retry_after) if retry_after else backoff, 60.0)
                    elif status == 200:
                        body = await resp.json(content_type=None)
                        return status, body
                    else:
                        return status, None
            if wait_secs is not None and attempt < MAX_RETRIES:
                await asyncio.sleep(wait_secs)
                backoff *= 2
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff *= 2
    return None, None


# ── Lever probe ───────────────────────────────────────────────────────────────

async def probe_slug(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    slug: str,
) -> int:
    """Return US job count for slug, 0 on any failure or no US jobs."""
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    status, data = await _fetch_json(session, semaphore, url)
    if status == 200 and isinstance(data, list) and data:
        return _count_us_jobs(data)
    return 0


# ── Serper source ─────────────────────────────────────────────────────────────

_SERPER_HEADERS = {
    "X-API-KEY":   SERPER_API_KEY,
    "Content-Type": "application/json",
}


async def _serper_search(
    session: aiohttp.ClientSession,
    serper_sem: asyncio.Semaphore,
    query: str,
    num: int = SERPER_RESULTS,
) -> List[str]:
    """Run one Serper query; return list of organic link URLs."""
    payload = {"q": f"site:jobs.lever.co {query}", "num": num}
    async with serper_sem:
        try:
            async with session.post(
                SERPER_URL,
                json=payload,
                headers=_SERPER_HEADERS,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    log.warning(f"  Serper {resp.status} for: {query}")
                    return []
                data = await resp.json(content_type=None)
                return [r.get("link", "") for r in data.get("organic", [])]
        except Exception as e:
            log.warning(f"  Serper error ({query}): {e}")
            return []


async def collect_serper_slugs(
    session: aiohttp.ClientSession,
) -> Set[str]:
    """Run all SERPER_QUERY_TERMS; return raw slug set extracted from link fields."""
    serper_sem = asyncio.Semaphore(SERPER_CONCURRENCY)
    slugs: Set[str] = set()
    total_queries = len(_SERPER_QUERY_TERMS)
    done = 0

    tasks = [_serper_search(session, serper_sem, term) for term in _SERPER_QUERY_TERMS]
    for coro in asyncio.as_completed(tasks):
        links = await coro
        for link in links:
            s = _slug_from_link(link)
            if s:
                slugs.add(s)
        done += 1
        if done % 10 == 0 or done == total_queries:
            log.info(f"  Serper queries: {done}/{total_queries} done — {len(slugs)} unique slugs so far")

    return slugs


# ── Validation + save ─────────────────────────────────────────────────────────

async def validate_and_save(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    raw_slugs: Set[str],
    known: Set[str],
    source: str,
    saver: IncrementalSaver,
) -> Tuple[int, int, int, List[Dict]]:
    """
    Validate raw_slugs against the Lever API.
    Mutates `known` to deduplicate across sources.
    Returns (n_valid, n_no_us, n_skipped, sample_top20).
    """
    new_slugs = [s for s in raw_slugs if s not in known]
    log.info(f"  {source}: {len(raw_slugs)} raw → {len(new_slugs)} new (skipping {len(raw_slugs)-len(new_slugs)} known)")

    n_valid = n_no_us = n_skipped = 0
    sample: List[Dict] = []

    # Process in chunks for progress feedback
    slug_list = list(new_slugs)
    for chunk_start in range(0, len(slug_list), CHUNK_SIZE):
        chunk = slug_list[chunk_start:chunk_start + CHUNK_SIZE]
        counts = await asyncio.gather(
            *[probe_slug(session, semaphore, s) for s in chunk],
            return_exceptions=True,
        )
        batch: List[Dict] = []
        for slug, result in zip(chunk, counts):
            if isinstance(result, Exception) or result == 0:
                n_no_us += 1
                known.add(slug)
                continue
            n_valid += 1
            known.add(slug)
            rec = {"tenant": slug, "source": source, "us_jobs": result}
            batch.append(rec)
            if len(sample) < 20:
                sample.append(rec)
        await saver.add(batch)
        log.info(
            f"  {source}: validated {min(chunk_start + CHUNK_SIZE, len(slug_list))}/{len(slug_list)} "
            f"— {n_valid} valid so far"
        )

    return n_valid, n_no_us, n_skipped, sample


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(apply: bool) -> None:
    if not SERPER_API_KEY:
        log.error("SERPER_API_KEY not set in environment")
        return

    known = load_known_slugs()
    saver = IncrementalSaver(apply=apply)

    semaphore = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY,
        limit_per_host=PER_HOST_LIMIT,
        enable_cleanup_closed=True,
    )

    t_start = time.monotonic()

    async with aiohttp.ClientSession(connector=connector) as session:
        # ── Source 1: Serper ────────────────────────────────────────────────
        log.info("")
        log.info("=" * 65)
        log.info(f"SOURCE 1 — Serper  ({len(_SERPER_QUERY_TERMS)} queries × {SERPER_RESULTS} results)")
        log.info("=" * 65)

        raw_slugs = await collect_serper_slugs(session)
        log.info(f"  Serper raw slugs collected: {len(raw_slugs)}")

        n_valid, n_no_us, n_skipped, sample = await validate_and_save(
            session, semaphore, raw_slugs, known, "serper", saver
        )

        log.info(
            f"  Serper results: {n_valid} valid / {n_no_us} no-US-jobs / {n_skipped} skipped"
        )

    await saver.flush_final()

    elapsed = time.monotonic() - t_start
    total_saved = saver.total_saved

    log.info("")
    log.info("=" * 65)
    log.info("FINAL RESULTS")
    log.info("=" * 65)
    log.info(f"  New Lever tenants found : {n_valid}")
    log.info(f"  Saved to DB             : {total_saved}")
    log.info(f"  Elapsed                 : {elapsed:.0f}s")
    log.info("")

    if sample:
        log.info("  Sample new tenants (up to 20):")
        for r in sample:
            log.info(f"    {r['tenant']:35}  ({r['us_jobs']} US jobs)")

    if not apply:
        log.info("")
        log.info("  DRY RUN — re-run with --apply to write to DB")


def main():
    ap = argparse.ArgumentParser(
        description="Aggressively discover new Lever tenants via Serper."
    )
    ap.add_argument("--apply",   action="store_true", help="Write results to DB")
    ap.add_argument("--dry-run", action="store_true", help="Dry run (default behaviour)")
    args = ap.parse_args()

    apply = args.apply and not args.dry_run
    if not apply:
        log.info("DRY RUN — add --apply to write to ats_tenants_candidates")

    asyncio.run(run(apply=apply))


if __name__ == "__main__":
    main()
