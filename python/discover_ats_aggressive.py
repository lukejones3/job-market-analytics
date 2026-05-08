#!/usr/bin/env python3
"""
discover_ats_aggressive.py

Aggressive multi-source ATS tenant discovery using async HTTP (aiohttp).
Writes candidates to ats_tenants_candidates with incremental DB flushes every
25 confirmed candidates or 30 seconds — killing mid-run loses at most ~25 records.

Sources (in recommended execution order):
  ct_logs      — Certificate Transparency logs via crt.sh [highest yield, run first]
  serper       — Serper/Google queries on ATS job board domains [run second]
  company_probe— Company list → Workday slug probing + career page ATS detection [heavy]
  sitemap      — Sitemap.xml mining for ATS-linked URLs [supplementary]
  linkedin     — LinkedIn/Indeed/Glassdoor via Serper snippets only [fragile, run last]

Usage:
    python python/discover_ats_aggressive.py --source ct_logs --dry-run
    python python/discover_ats_aggressive.py --source ct_logs --apply
    python python/discover_ats_aggressive.py --source serper --apply
    python python/discover_ats_aggressive.py --source company_probe --apply --limit 5000
    python python/discover_ats_aggressive.py --source sitemap --apply
    python python/discover_ats_aggressive.py --source linkedin --apply
    python python/discover_ats_aggressive.py --apply                    # all sources
    python python/discover_ats_aggressive.py --source ct_logs --limit 500 --dry-run

Pre-req: python python/migrate_ats_candidates.py (run once)
"""

import argparse
import asyncio
import csv
import io
import logging
import os
import random
import re
import time
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import quote as url_quote, urljoin, urlparse

import aiohttp
import psycopg2
from bs4 import BeautifulSoup
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

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
SERPER_URL     = "https://google.serper.dev/search"

SERPER_DELAY   = 0.4   # between Serper calls
CT_DELAY       = 3.0   # crt.sh is rate-limited

# Async concurrency controls
GLOBAL_CONCURRENCY   = 100   # max concurrent requests total
PER_HOST_CONCURRENCY = 5     # max concurrent requests to same host
ASYNC_TIMEOUT        = aiohttp.ClientTimeout(total=30)
CT_TIMEOUT           = aiohttp.ClientTimeout(total=180)   # crt.sh can be slow

# Incremental flush thresholds
FLUSH_EVERY_N = 25     # flush after this many pending candidates
FLUSH_EVERY_S = 30.0   # flush if this many seconds have elapsed

WD_SERVERS_PROBE = ["wd1", "wd5", "wd3", "wd12", "wd103", "wd501", "wd108", "wd503", "wd1480"]
_WD_PROBE_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
}
_WD_PROBE_PAYLOAD = {"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}

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

NAME_SUFFIXES = re.compile(
    r"\b(inc\.?|corp\.?|llc\.?|ltd\.?|co\.?|plc\.?|"
    r"group|holdings|holdco|technologies|technology|tech|"
    r"solutions|services|international|company|companies|"
    r"incorporated|limited|enterprises|partners|industries|"
    r"global|systems|associates|ventures|capital|"
    r"corporation|enterprises|management|financial)\b",
    re.IGNORECASE,
)

# ============================================================
# ATS URL EXTRACTION PATTERNS
# ============================================================

ATS_URL_RE: Dict[str, re.Pattern] = {
    "workday":        re.compile(r'([a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com', re.I),
    "greenhouse":     re.compile(r'(?:boards|job-boards)\.greenhouse\.io/(?:v1/boards/)?([a-z0-9_-]+)', re.I),
    "lever":          re.compile(r'jobs\.lever\.co/([a-z0-9_-]+)', re.I),
    "ashby":          re.compile(r'jobs\.ashbyhq\.com/([a-z0-9_-]+)', re.I),
    "workable":       re.compile(r'apply\.workable\.com/([a-z0-9_-]+)', re.I),
    "icims":          re.compile(r'([a-z0-9][a-z0-9_-]+)\.icims\.com', re.I),
    "taleo":          re.compile(r'([a-z0-9][a-z0-9_-]+)\.taleo\.net', re.I),
    "eightfold":      re.compile(r'([a-z0-9][a-z0-9_-]+)\.eightfold\.ai', re.I),
    "jobvite":        re.compile(r'jobs\.jobvite\.com/([a-z0-9_-]+)', re.I),
    "successfactors": re.compile(r'([a-z0-9][a-z0-9_-]+)\.successfactors\.com', re.I),
    "smartrecruiters":re.compile(r'careers\.smartrecruiters\.com/([a-z0-9_-]+)', re.I),
}

DOMAIN_NOISE = {"www", "api", "jobs", "careers", "app", "help", "support",
                "dev", "staging", "test", "mail", "blog", "static", "cdn",
                "us1", "us2", "eu1", "resources", "admin", "login", "auth"}

ATS_HTML_SIGNALS: List[Tuple[str, re.Pattern]] = [
    ("greenhouse",     re.compile(r'boards\.greenhouse\.io|job-boards\.greenhouse\.io', re.I)),
    ("lever",          re.compile(r'jobs\.lever\.co/', re.I)),
    ("ashby",          re.compile(r'jobs\.ashbyhq\.com', re.I)),
    ("workday",        re.compile(r'myworkdayjobs\.com', re.I)),
    ("workable",       re.compile(r'apply\.workable\.com', re.I)),
    ("icims",          re.compile(r'\.icims\.com/jobs', re.I)),
    ("taleo",          re.compile(r'\.taleo\.net', re.I)),
    ("smartrecruiters",re.compile(r'careers\.smartrecruiters\.com', re.I)),
    ("eightfold",      re.compile(r'\.eightfold\.ai', re.I)),
    ("bamboohr",       re.compile(r'\.bamboohr\.com', re.I)),
    ("jobvite",        re.compile(r'jobs\.jobvite\.com', re.I)),
    ("successfactors", re.compile(r'successfactors\.com', re.I)),
]

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


def load_known_candidates() -> Dict[str, Set[str]]:
    """
    Return {ats: set_of_known_tenants} from both ats_tenants_candidates
    and discovered_companies (to avoid re-discovering).
    """
    known: Dict[str, Set[str]] = {}
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("SELECT ats, tenant FROM ats_tenants_candidates")
        for ats, tenant in cur.fetchall():
            known.setdefault(ats, set()).add(tenant.lower())
        cur.execute("""
            SELECT ats_source, board_token FROM discovered_companies
            WHERE ats_source IS NOT NULL
              AND NOT (
                active_roles = 0
                AND last_had_roles IS NULL
                AND first_seen_at < NOW() - INTERVAL '14 days'
              )
        """)
        for ats, board_token in cur.fetchall():
            if not board_token:
                continue
            parts = board_token.split("/")
            tenant = parts[0].lower() if parts else ""
            if tenant:
                known.setdefault(ats, set()).add(tenant)
        cur.close()
        conn.close()
    except Exception as e:
        log.warning(f"Could not load known candidates from DB: {e}")
    total = sum(len(v) for v in known.values())
    log.info(f"Known existing tenants: {total} across {len(known)} ATSs")
    return known


def _save_candidates_sync(candidates: List[Dict]) -> int:
    """Sync DB upsert — safe to call from asyncio executor. Returns rows inserted."""
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
                    (ats, tenant, server, source, company_name)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (ats, tenant) DO NOTHING
                """,
                (c["ats"], c["tenant"], c.get("server"), c["source"], c.get("company_name")),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            log.warning(f"  Insert failed for {c.get('ats')}/{c.get('tenant')}: {e}")
            conn.rollback()
    conn.commit()
    cur.close()
    conn.close()
    return inserted


def _ua() -> str:
    return random.choice(USER_AGENTS)


# ============================================================
# INCREMENTAL SAVER
# ============================================================

class IncrementalSaver:
    """
    Buffers discovered candidates and flushes to DB every FLUSH_EVERY_N records
    or every FLUSH_EVERY_S seconds. Killing the process mid-run loses at most
    FLUSH_EVERY_N unflushed candidates.
    """

    def __init__(self, apply: bool):
        self.apply = apply
        self._pending: List[Dict] = []
        self._last_flush: float = time.monotonic()
        self._total_inserted: int = 0
        self._lock = asyncio.Lock()

    async def add(self, candidate: Dict) -> None:
        async with self._lock:
            self._pending.append(candidate)
            if self._should_flush():
                await self._flush()

    async def add_batch(self, candidates: List[Dict]) -> None:
        async with self._lock:
            self._pending.extend(candidates)
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
        to_flush = self._pending[:]
        self._pending.clear()
        self._last_flush = time.monotonic()

        if self.apply:
            loop = asyncio.get_running_loop()
            inserted = await loop.run_in_executor(None, _save_candidates_sync, to_flush)
            self._total_inserted += inserted
            log.info(f"  [flush] +{inserted} rows (total: {self._total_inserted})")
        else:
            for c in to_flush:
                log.info(
                    f"  [DRY RUN] {c['ats']:15} | tenant={c['tenant']:30} "
                    f"server={c.get('server') or '-':6} | source={c['source']}"
                )


# ============================================================
# SOURCE 5: CERTIFICATE TRANSPARENCY LOGS (crt.sh)
# ============================================================

CT_LOG_TARGETS = [
    ("workday",        "%.myworkdayjobs.com",   r'^([a-z0-9][a-z0-9_-]+)\.(wd[\w-]+)\.myworkdayjobs\.com$'),
    ("icims",          "%.icims.com",            r'^([a-z0-9][a-z0-9_-]+)\.icims\.com$'),
    ("taleo",          "%.taleo.net",            r'^([a-z0-9][a-z0-9_-]+)\.taleo\.net$'),
    ("eightfold",      "%.eightfold.ai",         r'^([a-z0-9][a-z0-9_-]+)\.eightfold\.ai$'),
    ("jobvite",        "%.jobvite.com",           r'^([a-z0-9][a-z0-9_-]+)\.jobvite\.com$'),
    ("successfactors", "%.successfactors.com",   r'^([a-z0-9][a-z0-9_-]+)\.successfactors\.com$'),
]

ICIMS_INFRA_RE = re.compile(
    r'^(?:api(?:-[\w-]+)?|login(?:-[\w-]+)?|analytics(?:-[\w-]+)?|'
    r'statuspage|trust|teams|talent|agents|marketplace|engage|developers|'
    r'design-system|mta-sts|notacustomer[\w-]*|'
    r'us\d+|eu\d+|ca\d+)$',
    re.I,
)

WORKDAY_SERVER_RE = re.compile(
    r'^(?:wd\d|impl[-_]?wd|dr[-_]?wd|perf[-_]?wd|impltest\d*[-_]?wd|'
    r'wcpdev[-_]?|stgimpl[-_]?wd|stgprod[-_]?wd|odpimpl[-_]?wd|'
    r'cp2[-_]?wd|turbo[-_]|oms[-_]perf|wcpimpl[-_]?wd|aade[-_]?wd)',
    re.I,
)


def _parse_ct_entry(name: str, ats: str, pattern: re.Pattern) -> Optional[Dict]:
    name = name.strip().lower()
    if not name or name.startswith("*") or name.startswith("@"):
        return None
    m = pattern.match(name)
    if not m:
        return None
    tenant = m.group(1)
    if tenant in DOMAIN_NOISE or len(tenant) < 2:
        return None
    if ats == "icims" and ICIMS_INFRA_RE.match(tenant):
        return None
    if ats == "workday" and WORKDAY_SERVER_RE.match(tenant):
        return None
    server = m.group(2) if m.lastindex >= 2 else None
    return {"ats": ats, "tenant": tenant, "server": server, "source": "ct_logs"}


async def fetch_ct_logs(
    session: aiohttp.ClientSession,
    known: Dict[str, Set[str]],
    saver: IncrementalSaver,
    limit: Optional[int] = None,
) -> List[Dict]:
    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()

    for ats, ct_pattern, name_re in CT_LOG_TARGETS:
        known_ats = known.get(ats, set())
        compiled = re.compile(name_re, re.I)
        url = f"https://crt.sh/?q={url_quote(ct_pattern, safe='%')}&output=json"
        log.info(f"CT logs: querying {ats} ({ct_pattern}) ...")

        entries = []
        for attempt in range(3):
            try:
                async with session.get(
                    url,
                    headers={"User-Agent": _ua(), "Accept": "application/json"},
                    timeout=CT_TIMEOUT,
                ) as r:
                    if r.status == 429:
                        log.warning(f"  crt.sh rate-limited for {ats}, waiting 60s...")
                        await asyncio.sleep(60)
                        continue
                    if r.status != 200:
                        log.warning(f"  crt.sh returned {r.status} for {ats}")
                        break
                    entries = await r.json(content_type=None)
                    log.info(f"  {ats}: {len(entries)} cert entries returned")
                    break
            except Exception as e:
                log.warning(f"  crt.sh attempt {attempt+1} failed for {ats}: {e}")
                if attempt < 2:
                    await asyncio.sleep(10)

        new_this_ats = 0
        batch: List[Dict] = []
        for entry in entries:
            for name in entry.get("name_value", "").split("\n"):
                c = _parse_ct_entry(name, ats, compiled)
                if not c:
                    continue
                key = (ats, c["tenant"])
                if key in seen or c["tenant"] in known_ats:
                    continue
                seen.add(key)
                known.setdefault(ats, set()).add(c["tenant"])
                candidates.append(c)
                batch.append(c)
                new_this_ats += 1

        if batch:
            await saver.add_batch(batch)

        log.info(f"  {ats}: {new_this_ats} new tenant candidates from CT logs")
        if limit and len(candidates) >= limit:
            break
        await asyncio.sleep(CT_DELAY)

    log.info(f"CT logs total: {len(candidates)} new candidates")
    return candidates


# ============================================================
# SOURCE 4: SERPER JOB BOARD SCRAPING
# ============================================================

SERPER_QUERIES = [
    ("workday",   'site:myworkdayjobs.com "data engineer"'),
    ("workday",   'site:myworkdayjobs.com "data scientist"'),
    ("workday",   'site:myworkdayjobs.com "machine learning engineer"'),
    ("workday",   'site:myworkdayjobs.com "analytics engineer"'),
    ("workday",   'site:myworkdayjobs.com "data analyst"'),
    ("workday",   'site:myworkdayjobs.com "applied scientist"'),
    ("workday",   'site:myworkdayjobs.com "ml engineer"'),
    ("greenhouse", 'site:boards.greenhouse.io "data engineer"'),
    ("greenhouse", 'site:boards.greenhouse.io "data scientist"'),
    ("greenhouse", 'site:boards.greenhouse.io "machine learning engineer"'),
    ("greenhouse", 'site:boards.greenhouse.io "analytics engineer"'),
    ("greenhouse", 'site:boards.greenhouse.io "applied scientist"'),
    ("greenhouse", 'site:job-boards.greenhouse.io "data engineer"'),
    ("greenhouse", 'site:job-boards.greenhouse.io "data scientist"'),
    ("lever",      'site:jobs.lever.co "data engineer"'),
    ("lever",      'site:jobs.lever.co "data scientist"'),
    ("lever",      'site:jobs.lever.co "machine learning"'),
    ("lever",      'site:jobs.lever.co "analytics engineer"'),
    ("lever",      'site:jobs.lever.co "data analyst"'),
    ("ashby",      'site:jobs.ashbyhq.com "data engineer"'),
    ("ashby",      'site:jobs.ashbyhq.com "data scientist"'),
    ("ashby",      'site:jobs.ashbyhq.com "machine learning"'),
    ("ashby",      'site:jobs.ashbyhq.com "analytics engineer"'),
    ("workable",   'site:apply.workable.com "data engineer"'),
    ("workable",   'site:apply.workable.com "data scientist"'),
    ("workable",   'site:apply.workable.com "machine learning"'),
    ("icims",      'site:icims.com "data engineer" "apply now"'),
    ("icims",      'site:icims.com "data scientist" "apply"'),
    ("taleo",      '"taleo.net" "data engineer" site:taleo.net'),
    ("taleo",      '"taleo.net" "data scientist" site:taleo.net'),
    ("smartrecruiters", 'site:careers.smartrecruiters.com "data engineer"'),
    ("smartrecruiters", 'site:careers.smartrecruiters.com "data scientist"'),
]


def _extract_ats_from_text(text: str) -> List[Tuple[str, str, Optional[str]]]:
    results = []
    for ats, pattern in ATS_URL_RE.items():
        for m in pattern.finditer(text):
            if ats == "workday":
                tenant = m.group(1).lower()
                server = m.group(2).lower()
            else:
                tenant = m.group(1).lower()
                server = None
            if tenant and tenant not in DOMAIN_NOISE and len(tenant) >= 2:
                results.append((ats, tenant, server))
    return results


async def fetch_serper_ats(
    session: aiohttp.ClientSession,
    known: Dict[str, Set[str]],
    saver: IncrementalSaver,
    limit: Optional[int] = None,
) -> List[Dict]:
    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()
    query_count = 0

    for ats, query in SERPER_QUERIES:
        if not SERPER_API_KEY:
            log.error("SERPER_API_KEY not set — cannot run Serper queries")
            break

        results = []
        try:
            async with session.post(
                SERPER_URL,
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": 10, "gl": "us", "hl": "en"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 429:
                    log.warning("Serper rate-limited, waiting 30s...")
                    await asyncio.sleep(30)
                elif r.status == 200:
                    data = await r.json(content_type=None)
                    results = data.get("organic", [])
                else:
                    log.warning(f"Serper HTTP {r.status} for: {query[:60]}")
        except Exception as e:
            log.warning(f"Serper error ({query[:60]}): {e}")

        query_count += 1
        batch: List[Dict] = []
        for result in results:
            search_text = " ".join([
                result.get("link", ""),
                result.get("snippet", ""),
                result.get("title", ""),
            ])
            for detected_ats, tenant, server in _extract_ats_from_text(search_text):
                key = (detected_ats, tenant)
                if key in seen or tenant in known.get(detected_ats, set()):
                    continue
                seen.add(key)
                known.setdefault(detected_ats, set()).add(tenant)
                c = {"ats": detected_ats, "tenant": tenant, "server": server, "source": "serper"}
                candidates.append(c)
                batch.append(c)

        if batch:
            await saver.add_batch(batch)

        log.info(f"  serper [{ats:15}] '{query[:50]}' → {len(results)} results, {len(candidates)} total")

        if limit and len(candidates) >= limit:
            break
        await asyncio.sleep(SERPER_DELAY)

    log.info(f"Serper: {len(candidates)} candidates from {query_count} queries")
    return candidates


# ============================================================
# SOURCE 1: COMPANY LIST → ATS PROBING
# ============================================================

def _name_to_slugs(name: str, ticker: str = "") -> List[str]:
    slugs: List[str] = []
    clean = NAME_SUFFIXES.sub("", name)
    clean = re.sub(r"[^a-z0-9\s-]", "", clean.lower()).strip()
    words = clean.split()
    clean_nospace = re.sub(r"\s+", "", clean)

    if clean_nospace and len(clean_nospace) >= 2:
        slugs.append(clean_nospace)
    hyphen = "-".join(words) if words else ""
    if hyphen and hyphen != clean_nospace and len(hyphen) >= 3:
        slugs.append(hyphen)
    first = words[0] if words else ""
    if first and first not in slugs and len(first) >= 2:
        slugs.append(first)
    t = ticker.lower().strip()
    if t and t not in slugs and len(t) >= 2:
        slugs.append(t)
    return slugs[:4]


def _name_to_domain(name: str) -> str:
    clean = re.sub(
        r'\b(Inc\.?|Corp\.?|LLC\.?|Ltd\.?|Co\.?|Group|Holdings|Technologies|'
        r'Solutions|Services|International|Company|Companies|Global|Systems)\b',
        "", name, flags=re.I,
    )
    clean = re.sub(r"[^a-z0-9]", "", clean.lower())
    return f"{clean}.com" if clean else ""


async def _probe_workday_slug(
    session: aiohttp.ClientSession,
    slug: str,
    company_name: str,
) -> Optional[Dict]:
    """
    Confirm a Workday tenant slug exists via the CXS jobs API.
    200/404 = tenant confirmed; 422 = not on this server (try next).
    """
    for server in WD_SERVERS_PROBE:
        url = f"https://{slug}.{server}.myworkdayjobs.com/wday/cxs/{slug}/External/jobs"
        headers = {**_WD_PROBE_HEADERS, "User-Agent": _ua()}

        for attempt in range(3):
            try:
                async with session.post(
                    url,
                    json=_WD_PROBE_PAYLOAD,
                    headers=headers,
                    timeout=ASYNC_TIMEOUT,
                ) as r:
                    if r.status == 429:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        break  # exhausted retries — try next server
                    if r.status in (200, 404):
                        return {
                            "ats": "workday",
                            "tenant": slug.lower(),
                            "server": server,
                            "source": "company_probe",
                            "company_name": company_name,
                        }
                    break  # 422 or other: tenant not on this server
            except Exception:
                break  # network error: try next server

    return None


async def _probe_career_page(
    session: aiohttp.ClientSession,
    domain: str,
    company_name: str,
) -> Optional[Dict]:
    """Probe a company's career pages for ATS signatures."""
    career_urls = [
        f"https://www.{domain}/careers",
        f"https://www.{domain}/jobs",
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
    ]
    for url in career_urls:
        for attempt in range(3):
            try:
                async with session.get(
                    url,
                    headers={"User-Agent": _ua()},
                    timeout=ASYNC_TIMEOUT,
                    allow_redirects=True,
                ) as r:
                    if r.status == 429:
                        if attempt < 2:
                            await asyncio.sleep(2 ** attempt)
                            continue
                        break
                    if r.status != 200:
                        break

                    content_bytes = await r.content.read(30_000)
                    content = content_bytes.decode("utf-8", errors="replace")

                    for ats_name, signal_re in ATS_HTML_SIGNALS:
                        if not signal_re.search(content):
                            continue
                        token_re = ATS_URL_RE.get(ats_name)
                        if not token_re:
                            continue
                        m = token_re.search(content)
                        if not m:
                            continue
                        tenant = m.group(1).lower()
                        server = (
                            m.group(2).lower()
                            if ats_name == "workday" and m.lastindex >= 2
                            else None
                        )
                        if tenant in DOMAIN_NOISE or len(tenant) < 2:
                            continue
                        return {
                            "ats": ats_name,
                            "tenant": tenant,
                            "server": server,
                            "source": "company_probe",
                            "company_name": company_name,
                        }
                    break  # 200 but no ATS found — don't retry
            except Exception:
                break  # network error: try next URL

    return None


def _scrape_fortune1000_sync() -> List[Tuple[str, str]]:
    import requests as _req
    log.info("Scraping Fortune 1000 from Wikipedia...")
    try:
        r = _req.get("https://en.wikipedia.org/wiki/Fortune_1000", headers={"User-Agent": _ua()}, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        companies = []
        for table in soup.find_all("table", class_="wikitable"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            col_idx = next((i for i, h in enumerate(headers) if "company" in h or "name" in h), 1)
            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) <= col_idx:
                    continue
                name = re.sub(r'\[.*?\]', '', cells[col_idx].get_text(strip=True)).strip()
                if name and len(name) > 1:
                    companies.append((name, ""))
        log.info(f"  Fortune 1000: {len(companies)} companies scraped")
        return companies
    except Exception as e:
        log.warning(f"  Fortune 1000 scrape failed: {e}")
        return []


def _scrape_russell3000_sync() -> List[Tuple[str, str]]:
    import requests as _req
    log.info("Fetching Russell 3000 from iShares CSV...")
    urls_to_try = [
        "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/1467271812596.ajax?tab=holdings&fileType=csv",
        "https://www.ishares.com/us/products/239714/IWV/1467271812596.ajax?tab=holdings&fileType=csv",
    ]
    for url in urls_to_try:
        try:
            r = _req.get(url, headers={"User-Agent": _ua(), "Referer": "https://www.ishares.com/"}, timeout=30)
            if r.status_code != 200:
                continue
            lines = r.text.splitlines()
            header_idx = next((i for i, l in enumerate(lines) if "Name" in l and "Ticker" in l), None)
            if header_idx is None:
                continue
            reader = csv.DictReader(io.StringIO("\n".join(lines[header_idx:])))
            companies = []
            for row in reader:
                name = (row.get("Name") or row.get("Security") or "").strip()
                ticker = (row.get("Ticker") or row.get("Symbol") or "").strip()
                if name and name not in ("-", "Cash", "Other"):
                    companies.append((name, ticker))
            log.info(f"  Russell 3000: {len(companies)} companies from iShares CSV")
            return companies
        except Exception as e:
            log.warning(f"  Russell 3000 iShares attempt failed: {e}")
    log.warning("  Russell 3000: all CSV attempts failed — skipping")
    return []


def _scrape_yc_companies_sync() -> List[Tuple[str, str]]:
    import requests as _req
    log.info("Fetching YC companies...")
    batches = "W25,S24,W24,S23,W23,S22,W22,S21,W21,S20,W20"
    url = f"https://api.ycombinator.com/v0.1/companies?batch={batches}&per_page=1000"
    companies = []
    page = 1
    while True:
        try:
            r = _req.get(f"{url}&page={page}", headers={"User-Agent": _ua()}, timeout=20)
            if r.status_code != 200:
                break
            data = r.json()
            items = data.get("companies", [])
            if not items:
                break
            for c in items:
                name = c.get("name", "").strip()
                if name:
                    companies.append((name, ""))
            if not data.get("next_page"):
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            log.warning(f"  YC API error (page {page}): {e}")
            break
    log.info(f"  YC: {len(companies)} companies from API")
    return companies


def _fetch_edgar_companies_sync() -> List[Tuple[str, str]]:
    import requests as _req
    log.info("Fetching SEC EDGAR company tickers...")
    try:
        r = _req.get(
            "https://www.sec.gov/files/company_tickers.json",
            timeout=30,
            headers={"User-Agent": "Aggressive-ATS-Discovery/1.0 jones31luke@gmail.com"},
        )
        r.raise_for_status()
        data = r.json()
        companies = [
            (v["title"], v["ticker"])
            for v in data.values()
            if isinstance(v, dict) and "title" in v and "ticker" in v
        ]
        log.info(f"  EDGAR: {len(companies)} companies")
        return companies
    except Exception as e:
        log.error(f"  EDGAR fetch failed: {e}")
        return []


def _build_company_list_sync() -> List[Tuple[str, str]]:
    all_companies: Dict[str, tuple] = {}
    for name, ticker in _fetch_edgar_companies_sync():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)
    for name, ticker in _scrape_fortune1000_sync():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)
    for name, ticker in _scrape_russell3000_sync():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)
    for name, ticker in _scrape_yc_companies_sync():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)
    result = list(all_companies.values())
    log.info(f"Combined company list: {len(result)} unique companies")
    return result


async def fetch_company_probes(
    session: aiohttp.ClientSession,
    known: Dict[str, Set[str]],
    saver: IncrementalSaver,
    limit: Optional[int] = None,
    workday_only: bool = False,
) -> List[Dict]:
    loop = asyncio.get_running_loop()
    companies = await loop.run_in_executor(None, _build_company_list_sync)
    if limit:
        companies = companies[:limit]

    known_workday = known.get("workday", set())
    probes: List[Tuple[str, str]] = []
    seen_slugs: Set[str] = set(known_workday)
    for name, ticker in companies:
        for slug in _name_to_slugs(name, ticker):
            if slug not in seen_slugs:
                probes.append((slug, name))
                seen_slugs.add(slug)

    log.info(f"Company probe: {len(probes)} Workday slug probes across {len(companies)} companies")

    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()
    wd_done = 0
    wd_found = 0

    # Phase 1: Concurrent Workday slug probing
    log.info("Phase 1: Workday slug probing (async, 100 concurrent)...")

    async def _wd_one(slug: str, company: str) -> None:
        nonlocal wd_done, wd_found
        result = await _probe_workday_slug(session, slug, company)
        wd_done += 1
        if wd_done % 1000 == 0:
            log.info(f"  WD probe: {wd_done}/{len(probes)} done, {wd_found} confirmed")
        if result:
            key = ("workday", result["tenant"])
            if key not in seen:
                seen.add(key)
                known.setdefault("workday", set()).add(result["tenant"])
                candidates.append(result)
                wd_found += 1
                log.info(f"  ✅ Workday: {result['company_name']} → {result['tenant']}.{result['server']}")
                await saver.add(result)

    await asyncio.gather(*[_wd_one(s, n) for s, n in probes], return_exceptions=True)
    log.info(f"Phase 1 complete: {wd_found} Workday tenants confirmed from {wd_done} probes")

    if workday_only:
        return candidates

    # Phase 2: Career page ATS detection (concurrent)
    log.info("Phase 2: Career page probing (async, 100 concurrent)...")
    career_done = 0
    career_found = 0

    async def _career_one(name: str, ticker: str) -> None:
        nonlocal career_done, career_found
        domain = _name_to_domain(name)
        if not domain or len(domain) < 5:
            return
        result = await _probe_career_page(session, domain, name)
        career_done += 1
        if career_done % 500 == 0:
            log.info(f"  Career probe: {career_done}/{len(companies)}, {career_found} found")
        if result:
            key = (result["ats"], result["tenant"])
            if key not in seen and result["tenant"] not in known.get(result["ats"], set()):
                seen.add(key)
                known.setdefault(result["ats"], set()).add(result["tenant"])
                candidates.append(result)
                career_found += 1
                log.info(f"  ✅ {result['ats']}: {name} → {result['tenant']}")
                await saver.add(result)

    await asyncio.gather(*[_career_one(n, t) for n, t in companies], return_exceptions=True)
    log.info(f"Phase 2 complete: {career_found} non-Workday tenants confirmed from {career_done} domains")

    log.info(f"Company probes total: {len(candidates)} candidates")
    return candidates


# ============================================================
# SOURCE 2: SITEMAP MINING
# ============================================================

async def fetch_sitemap_mining(
    session: aiohttp.ClientSession,
    known: Dict[str, Set[str]],
    saver: IncrementalSaver,
    limit: Optional[int] = None,
) -> List[Dict]:
    loop = asyncio.get_running_loop()
    fortune = await loop.run_in_executor(None, _scrape_fortune1000_sync)
    russell = await loop.run_in_executor(None, _scrape_russell3000_sync)
    companies = (fortune[:1000] + russell[:1000])

    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()
    checked = 0

    for name, ticker in companies:
        domain = _name_to_domain(name)
        if not domain:
            continue

        checked += 1
        if checked % 100 == 0:
            log.info(f"  Sitemap: {checked}/{len(companies)} domains, {len(candidates)} candidates")

        sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/careers/sitemap.xml", "/jobs/sitemap.xml"]
        urls: List[str] = []
        for path in sitemap_paths:
            sitemap_url = f"https://www.{domain}{path}"
            try:
                async with session.get(
                    sitemap_url,
                    headers={"User-Agent": _ua()},
                    timeout=ASYNC_TIMEOUT,
                    allow_redirects=True,
                ) as r:
                    if r.status != 200:
                        continue
                    text = await r.text(errors="replace")
                    soup = BeautifulSoup(text, "lxml-xml")
                    for loc in soup.find_all("loc"):
                        loc_url = loc.get_text(strip=True)
                        if loc_url:
                            urls.append(loc_url)
                    if urls:
                        break
            except Exception:
                continue

        batch: List[Dict] = []
        for url in urls:
            for ats, tenant, server in _extract_ats_from_text(url):
                key = (ats, tenant)
                if key in seen or tenant in known.get(ats, set()):
                    continue
                seen.add(key)
                known.setdefault(ats, set()).add(tenant)
                c = {"ats": ats, "tenant": tenant, "server": server, "source": "sitemap", "company_name": name}
                candidates.append(c)
                batch.append(c)

        if batch:
            await saver.add_batch(batch)

        if limit and len(candidates) >= limit:
            break

        await asyncio.sleep(0.3)

    log.info(f"Sitemap mining: {len(candidates)} candidates from {checked} domains")
    return candidates


# ============================================================
# SOURCE 3: LINKEDIN / JOB BOARDS VIA SERPER SNIPPETS ONLY
# ============================================================

LINKEDIN_QUERIES = [
    '"myworkdayjobs.com" "data engineer"',
    '"myworkdayjobs.com" "data scientist"',
    '"boards.greenhouse.io" "data engineer"',
    '"jobs.lever.co" "data engineer"',
    '"jobs.ashbyhq.com" "data engineer"',
    '"apply.workable.com" "data engineer"',
    'site:linkedin.com/jobs "data engineer" "apply on company website"',
    'site:linkedin.com/jobs "data scientist" "apply on company website"',
    'site:linkedin.com/jobs "machine learning engineer" "apply on company website"',
    '"apply with" "myworkdayjobs.com" "data"',
    '"apply at" "greenhouse.io" "data engineer"',
    '"apply at" "lever.co" "data engineer"',
]


async def fetch_linkedin_serper(
    session: aiohttp.ClientSession,
    known: Dict[str, Set[str]],
    saver: IncrementalSaver,
    limit: Optional[int] = None,
) -> List[Dict]:
    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()
    query_count = 0

    for query in LINKEDIN_QUERIES:
        if not SERPER_API_KEY:
            log.error("SERPER_API_KEY not set — cannot run Serper queries")
            break

        results = []
        try:
            async with session.post(
                SERPER_URL,
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": 10, "gl": "us", "hl": "en"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                if r.status == 429:
                    log.warning("Serper rate-limited, waiting 30s...")
                    await asyncio.sleep(30)
                elif r.status == 200:
                    data = await r.json(content_type=None)
                    results = data.get("organic", [])
        except Exception as e:
            log.warning(f"Serper error ({query[:60]}): {e}")

        query_count += 1
        batch: List[Dict] = []
        for result in results:
            snippet_text = " ".join([
                result.get("snippet", ""),
                result.get("title", ""),
                result.get("link", ""),
            ])
            for ats, tenant, server in _extract_ats_from_text(snippet_text):
                key = (ats, tenant)
                if key in seen or tenant in known.get(ats, set()):
                    continue
                seen.add(key)
                known.setdefault(ats, set()).add(tenant)
                c = {"ats": ats, "tenant": tenant, "server": server, "source": "linkedin"}
                candidates.append(c)
                batch.append(c)

        if batch:
            await saver.add_batch(batch)

        log.info(f"  linkedin [{query[:50]}] → {len(results)} results, {len(candidates)} total")
        if limit and len(candidates) >= limit:
            break
        await asyncio.sleep(SERPER_DELAY)

    log.info(f"LinkedIn/Serper: {len(candidates)} candidates from {query_count} queries")
    return candidates


# ============================================================
# MAIN
# ============================================================

async def run(args: argparse.Namespace) -> None:
    if not args.apply:
        log.info("DRY RUN — pass --apply to write to ats_tenants_candidates")

    loop = asyncio.get_running_loop()
    known = await loop.run_in_executor(None, load_known_candidates)

    connector = aiohttp.TCPConnector(
        limit=GLOBAL_CONCURRENCY,
        limit_per_host=PER_HOST_CONCURRENCY,
        enable_cleanup_closed=True,
    )
    saver = IncrementalSaver(apply=args.apply)
    all_candidates: List[Dict] = []

    sources_to_run = (
        ["ct_logs", "serper", "company_probe", "sitemap", "linkedin"]
        if args.source == "all"
        else [args.source]
    )

    async with aiohttp.ClientSession(connector=connector) as session:
        for source in sources_to_run:
            log.info("")
            log.info("=" * 65)
            log.info(f"SOURCE: {source.upper()}")
            log.info("=" * 65)

            if source == "ct_logs":
                new = await fetch_ct_logs(session, known, saver, limit=args.limit)
            elif source == "serper":
                new = await fetch_serper_ats(session, known, saver, limit=args.limit)
            elif source == "company_probe":
                new = await fetch_company_probes(
                    session, known, saver, limit=args.limit, workday_only=args.workday_only
                )
            elif source == "sitemap":
                new = await fetch_sitemap_mining(session, known, saver, limit=args.limit)
            elif source == "linkedin":
                new = await fetch_linkedin_serper(session, known, saver, limit=args.limit)
            else:
                new = []

            all_candidates.extend(new)
            # Update known for dedup across later sources (already done inside each function)

    total_inserted = await saver.flush_final()

    log.info("")
    log.info("=" * 65)
    log.info("DISCOVERY SUMMARY")
    log.info("=" * 65)
    by_source: Dict[str, int] = {}
    by_ats: Dict[str, int] = {}
    for c in all_candidates:
        by_source[c["source"]] = by_source.get(c["source"], 0) + 1
        by_ats[c["ats"]] = by_ats.get(c["ats"], 0) + 1

    log.info("By source:")
    for src, count in sorted(by_source.items()):
        log.info(f"  {src:20} {count:6}")
    log.info("By ATS:")
    for ats, count in sorted(by_ats.items(), key=lambda x: -x[1]):
        log.info(f"  {ats:20} {count:6}")
    log.info(f"Total unique new candidates: {len(all_candidates)}")
    if args.apply:
        log.info(f"Total rows inserted to DB:  {total_inserted}")
    log.info("")
    log.info("Next step: python python/validate_ats_candidates.py --apply")


def main():
    ap = argparse.ArgumentParser(description="Aggressive multi-source ATS tenant discovery.")
    ap.add_argument(
        "--source",
        choices=["all", "ct_logs", "serper", "company_probe", "sitemap", "linkedin"],
        default="all",
        help="Which source to run (default: all)",
    )
    ap.add_argument("--apply", action="store_true", help="Write candidates to DB")
    ap.add_argument("--limit", type=int, default=None, metavar="N",
                    help="Limit candidates/companies per source (useful for testing)")
    ap.add_argument("--workday-only", action="store_true",
                    help="company_probe: skip career page probing, only probe Workday slugs")
    args = ap.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
