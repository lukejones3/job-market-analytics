#!/usr/bin/env python3
"""
discover_ashby_aggressive.py

Aggressively discovers new Ashby tenants via Serper site:jobs.ashbyhq.com queries.
Validates each candidate via the Ashby public posting API before writing to DB.

Ashby skews toward YC/AI/well-funded startups — high signal-to-noise.

Source 1 — Serper:
    ~110 queries × 100 results against site:jobs.ashbyhq.com
    Slug extracted from link field only (never snippet/title).

Validation:
    GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
    Keep if 1+ US jobs (locationName field). Polite: 1 req/s to Ashby's API.

DB writes:
    INSERT INTO ats_tenants_candidates (ats, tenant, source, us_jobs_count, status, last_validated_at)
    ON CONFLICT (ats, tenant) DO NOTHING

Usage:
    python python/discover_ashby_aggressive.py --dry-run
    python python/discover_ashby_aggressive.py --apply
"""

import argparse
import asyncio
import json
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

CONCURRENCY        = 10     # Ashby rate-limits aggressively
SERPER_CONCURRENCY = 3
PER_HOST_LIMIT     = 1      # max 1 concurrent conn to api.ashbyhq.com
MIN_REQ_GAP        = 1.0    # seconds between Ashby API requests
FLUSH_EVERY        = 25
HTTP_TIMEOUT       = aiohttp.ClientTimeout(total=30)
MAX_RETRIES        = 3
BASE_BACKOFF       = 5.0    # 5s → 10s → 20s → skip
SERPER_RESULTS     = 100
CHUNK_SIZE         = 200    # smaller chunks due to lower concurrency

SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
SERPER_URL     = "https://google.serper.dev/search"

_API_HEADERS = {
    "Accept":     "application/json",
    "User-Agent": "job-market-analytics/1.0 (research; contact: jones31luke@gmail.com)",
}

# ── Per-host throttle ─────────────────────────────────────────────────────────

class _HostThrottle:
    def __init__(self, gap: float):
        self._gap  = gap
        self._last = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now  = asyncio.get_event_loop().time()
            wait = self._gap - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = asyncio.get_event_loop().time()


# ── Ashby slug extraction ─────────────────────────────────────────────────────

_ASHBY_LINK_RE = re.compile(
    r'jobs\.ashbyhq\.com/([a-zA-Z0-9][a-zA-Z0-9._-]*)',
    re.I,
)

_SLUG_NOISE: Set[str] = {
    "", "jobs", "postings", "api", "embed", "apply", "board",
    "careers", "team", "hiring", "work", "ashby", "ashbyhq",
    "com", "view", "open", "v0", "v1", "www", "http", "https",
}

# UUID pattern for Ashby job IDs
_UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
)


def _slug_from_link(url: str) -> Optional[str]:
    """Extract org slug from jobs.ashbyhq.com URL (link field only)."""
    m = _ASHBY_LINK_RE.search(url)
    if not m:
        return None
    slug = m.group(1).lower().split("/")[0].split("?")[0].strip()
    if slug in _SLUG_NOISE or len(slug) < 2:
        return None
    if _UUID_RE.match(slug):
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
    "cybersecurity engineer",
    # Data / ML / AI — Ashby skews AI-heavy, expand this section
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
    "quantitative analyst",
    "computer vision engineer",
    "NLP engineer",
    "deep learning engineer",
    "AI researcher",
    "LLM engineer",
    "foundation model",
    "AI safety researcher",
    "alignment researcher",
    "AI product manager",
    # Languages / frameworks
    "Python engineer",
    "Go engineer",
    "Rust engineer",
    "TypeScript engineer",
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
    "partnership manager",
    "revenue operations manager",
    "VP sales",
    # Finance / ops / HR
    "finance manager",
    "financial analyst",
    "recruiting manager",
    "people operations manager",
    "HR business partner",
    "talent acquisition",
    "operations manager",
    "chief of staff",
    "program manager",
    # Customer success / support
    "customer success",
    "technical support engineer",
    "implementation manager",
    # Location combos
    "software engineer remote",
    "engineer San Francisco",
    "engineer New York",
    "engineer Seattle",
    "engineer Austin",
    "engineer Boston",
    "engineer Chicago",
    "remote United States",
    # YC/AI/startup industry combos — Ashby's core demographic
    "AI startup engineer",
    "YC startup engineer",
    "fintech engineer",
    "healthtech engineer",
    "biotech engineer",
    "climate tech",
    "cleantech",
    "crypto engineer",
    "blockchain developer",
    "web3 engineer",
    "B2B SaaS engineer",
    "developer tools engineer",
    "developer experience",
    "infrastructure startup",
    "early stage startup engineer",
    "Series A engineer",
    "Series B engineer",
    # Seniority sweeps
    "VP engineering",
    "director of engineering",
    "CTO",
    "head of engineering",
    "technical lead",
    "tech lead",
    "founding engineer",
    "first engineer",
    "early engineer",
    # Broad sweeps
    "join our team engineer",
    "open roles engineer",
    "startup jobs engineer",
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


def _is_us_ashby_job(job: dict) -> bool:
    """Check primary location, structured address, and secondaryLocations."""
    if _is_us_location(job.get("location", "") or ""):
        return True
    postal = (job.get("address") or {}).get("postalAddress") or {}
    if (postal.get("addressCountry") or "").upper() in ("USA", "UNITED STATES", "US"):
        return True
    for sec in (job.get("secondaryLocations") or []):
        if _is_us_location(sec.get("location", "") or ""):
            return True
        sec_postal = (sec.get("address") or {}).get("postalAddress") or {}
        if (sec_postal.get("addressCountry") or "").upper() in ("USA", "UNITED STATES", "US"):
            return True
    return False


def _count_us_jobs(jobs: List[dict]) -> int:
    return sum(1 for j in jobs if _is_us_ashby_job(j))


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
    """Return all already-known Ashby slugs (candidates + discovered_companies)."""
    conn = get_conn()
    cur  = conn.cursor()
    cur.execute("SELECT tenant FROM ats_tenants_candidates WHERE ats = 'ashby'")
    slugs = {r[0].lower() for r in cur.fetchall()}
    cur.execute("SELECT board_token FROM discovered_companies WHERE ats_source = 'ashby'")
    for (bt,) in cur.fetchall():
        s = bt.strip().strip("/").split("/")[0].split("?")[0].strip().lower()
        if s:
            slugs.add(s)
    cur.close()
    conn.close()
    log.info(f"Loaded {len(slugs)} known Ashby slugs (skip list)")
    return slugs


# ── Incremental DB saver ──────────────────────────────────────────────────────

class IncrementalSaver:
    def __init__(self, apply: bool):
        self.apply       = apply
        self._buf:       List[Dict] = []
        self._lock       = asyncio.Lock()
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
    conn  = get_conn()
    cur   = conn.cursor()
    saved = 0
    try:
        for c in candidates:
            try:
                cur.execute("SAVEPOINT sp")
                cur.execute(
                    """
                    INSERT INTO ats_tenants_candidates
                        (ats, tenant, server, source, us_jobs_count, status, last_validated_at)
                    VALUES ('ashby', %s, NULL, %s, %s, 'active', now())
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
    throttle: _HostThrottle,
) -> Tuple[Optional[int], Optional[object]]:
    """Ashby API fetch with throttle + 429 backoff."""
    backoff = BASE_BACKOFF
    for attempt in range(MAX_RETRIES + 1):
        wait_secs: Optional[float] = None
        try:
            async with semaphore:
                await throttle.acquire()
                async with session.get(
                    url, headers=_API_HEADERS, timeout=HTTP_TIMEOUT
                ) as resp:
                    status = resp.status
                    if status == 429:
                        retry_after = resp.headers.get("Retry-After")
                        wait_secs   = min(float(retry_after) if retry_after else backoff, 60.0)
                    elif status == 200:
                        body = await resp.json(content_type=None)
                        return status, body
                    else:
                        return status, None
            if wait_secs is not None and attempt < MAX_RETRIES:
                await asyncio.sleep(wait_secs)
                backoff = min(backoff * 2, 30.0)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            if attempt < MAX_RETRIES:
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)
    return None, None  # skip


# ── Ashby probe ───────────────────────────────────────────────────────────────

async def probe_slug(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    slug: str,
    throttle: _HostThrottle,
) -> int:
    """Return US job count for slug. 0 on failure, 404, or no US jobs."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    status, data = await _fetch_json(session, semaphore, url, throttle)
    if status == 200 and isinstance(data, dict):
        jobs = data.get("jobs") or []
        return _count_us_jobs(jobs)
    return 0


# ── Serper source ─────────────────────────────────────────────────────────────

_SERPER_HEADERS = {
    "X-API-KEY":    SERPER_API_KEY,
    "Content-Type": "application/json",
}


async def _serper_search(
    session: aiohttp.ClientSession,
    serper_sem: asyncio.Semaphore,
    query: str,
    num: int = SERPER_RESULTS,
) -> List[str]:
    """Run one Serper query; return list of organic link URLs."""
    payload = {"q": f"site:jobs.ashbyhq.com {query}", "num": num}
    async with serper_sem:
        try:
            async with session.post(
                SERPER_URL,
                json=payload,
                headers={**_SERPER_HEADERS, "X-API-KEY": SERPER_API_KEY},
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
    """Run all SERPER_QUERY_TERMS; return raw slug set from link fields."""
    serper_sem    = asyncio.Semaphore(SERPER_CONCURRENCY)
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
            log.info(f"  Serper: {done}/{total_queries} queries done — {len(slugs)} unique slugs so far")

    return slugs


# ── Validation + save ─────────────────────────────────────────────────────────

async def validate_and_save(
    session: aiohttp.ClientSession,
    semaphore: asyncio.Semaphore,
    raw_slugs: Set[str],
    known: Set[str],
    source: str,
    saver: IncrementalSaver,
    throttle: _HostThrottle,
) -> Tuple[int, int, List[Dict]]:
    """
    Validate raw_slugs. Mutates `known`. Returns (n_valid, n_no_us, sample_top20).
    """
    new_slugs = [s for s in raw_slugs if s not in known]
    log.info(
        f"  {source}: {len(raw_slugs)} raw → {len(new_slugs)} new "
        f"(skipping {len(raw_slugs)-len(new_slugs)} known)"
    )

    n_valid = n_no_us = 0
    sample:  List[Dict] = []

    slug_list = list(new_slugs)
    for chunk_start in range(0, len(slug_list), CHUNK_SIZE):
        chunk  = slug_list[chunk_start:chunk_start + CHUNK_SIZE]
        counts = await asyncio.gather(
            *[probe_slug(session, semaphore, s, throttle) for s in chunk],
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
            f"  {source}: validated {min(chunk_start + CHUNK_SIZE, len(slug_list))}"
            f"/{len(slug_list)} — {n_valid} valid so far"
        )

    return n_valid, n_no_us, sample


# ── Main ──────────────────────────────────────────────────────────────────────

async def run(apply: bool) -> None:
    if not SERPER_API_KEY:
        log.error("SERPER_API_KEY not set in environment")
        return

    known     = load_known_slugs()
    saver     = IncrementalSaver(apply=apply)
    throttle  = _HostThrottle(MIN_REQ_GAP)
    semaphore = asyncio.Semaphore(CONCURRENCY)
    connector = aiohttp.TCPConnector(
        limit=CONCURRENCY,
        limit_per_host=PER_HOST_LIMIT,
        enable_cleanup_closed=True,
    )

    t_start = time.monotonic()

    async with aiohttp.ClientSession(connector=connector) as session:
        log.info("")
        log.info("=" * 65)
        log.info(f"SOURCE 1 — Serper  ({len(_SERPER_QUERY_TERMS)} queries × {SERPER_RESULTS} results)")
        log.info("=" * 65)

        raw_slugs = await collect_serper_slugs(session)
        log.info(f"  Serper raw slugs collected: {len(raw_slugs)}")

        n_valid, n_no_us, sample = await validate_and_save(
            session, semaphore, raw_slugs, known, "serper", saver, throttle
        )

        log.info(f"  Serper results: {n_valid} valid / {n_no_us} no-US-jobs")

    await saver.flush_final()

    elapsed     = time.monotonic() - t_start
    total_saved = saver.total_saved

    log.info("")
    log.info("=" * 65)
    log.info("FINAL RESULTS")
    log.info("=" * 65)
    log.info(f"  New Ashby tenants found : {n_valid}")
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
        description="Aggressively discover new Ashby tenants via Serper."
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
