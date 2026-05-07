#!/usr/bin/env python3
"""
discover_ats_aggressive.py

Aggressive multi-source ATS tenant discovery. Writes candidates to the
ats_tenants_candidates table for later validation by validate_ats_candidates.py.

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
import csv
import io
import json
import logging
import os
import random
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple
from urllib.parse import urljoin, urlparse

import psycopg2
import requests
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

PROBE_WORKERS  = 25
PROBE_TIMEOUT  = 6
SERPER_DELAY   = 0.4   # between Serper calls
CT_DELAY       = 3.0   # crt.sh is rate-limited

USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
]

# Company name suffix words to strip before slug generation
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

# Regex to pull tenant from a URL for each ATS
ATS_URL_RE: Dict[str, re.Pattern] = {
    "workday":       re.compile(r'([a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com', re.I),
    "greenhouse":    re.compile(r'(?:boards|job-boards)\.greenhouse\.io/(?:v1/boards/)?([a-z0-9_-]+)', re.I),
    "lever":         re.compile(r'jobs\.lever\.co/([a-z0-9_-]+)', re.I),
    "ashby":         re.compile(r'jobs\.ashbyhq\.com/([a-z0-9_-]+)', re.I),
    "workable":      re.compile(r'apply\.workable\.com/([a-z0-9_-]+)', re.I),
    "icims":         re.compile(r'([a-z0-9][a-z0-9_-]+)\.icims\.com', re.I),
    "taleo":         re.compile(r'([a-z0-9][a-z0-9_-]+)\.taleo\.net', re.I),
    "eightfold":     re.compile(r'([a-z0-9][a-z0-9_-]+)\.eightfold\.ai', re.I),
    "jobvite":       re.compile(r'jobs\.jobvite\.com/([a-z0-9_-]+)', re.I),
    "successfactors":re.compile(r'([a-z0-9][a-z0-9_-]+)\.successfactors\.com', re.I),
    "smartrecruiters":re.compile(r'careers\.smartrecruiters\.com/([a-z0-9_-]+)', re.I),
}

# Noise subdomains to ignore for domain-based ATSs
DOMAIN_NOISE = {"www", "api", "jobs", "careers", "app", "help", "support",
                "dev", "staging", "test", "mail", "blog", "static", "cdn",
                "us1", "us2", "eu1", "resources", "admin", "login", "auth"}

# ATS signals for career page HTML detection
ATS_HTML_SIGNALS: List[Tuple[str, re.Pattern]] = [
    ("greenhouse",       re.compile(r'boards\.greenhouse\.io|job-boards\.greenhouse\.io', re.I)),
    ("lever",            re.compile(r'jobs\.lever\.co/', re.I)),
    ("ashby",            re.compile(r'jobs\.ashbyhq\.com', re.I)),
    ("workday",          re.compile(r'myworkdayjobs\.com', re.I)),
    ("workable",         re.compile(r'apply\.workable\.com', re.I)),
    ("icims",            re.compile(r'\.icims\.com/jobs', re.I)),
    ("taleo",            re.compile(r'\.taleo\.net', re.I)),
    ("smartrecruiters",  re.compile(r'careers\.smartrecruiters\.com', re.I)),
    ("eightfold",        re.compile(r'\.eightfold\.ai', re.I)),
    ("bamboohr",         re.compile(r'\.bamboohr\.com', re.I)),
    ("jobvite",          re.compile(r'jobs\.jobvite\.com', re.I)),
    ("successfactors",   re.compile(r'successfactors\.com', re.I)),
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

        # Load from ats_tenants_candidates
        cur.execute("SELECT ats, tenant FROM ats_tenants_candidates")
        for ats, tenant in cur.fetchall():
            known.setdefault(ats, set()).add(tenant.lower())

        # Load from discovered_companies (parse board_token by ats_source)
        cur.execute("SELECT ats_source, board_token FROM discovered_companies WHERE ats_source IS NOT NULL")
        for ats, board_token in cur.fetchall():
            if not board_token:
                continue
            # For Workday: board_token = "tenant/server/board"
            # For others: board_token = "slug" or "company/section"
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


def save_candidates(candidates: List[Dict], apply: bool) -> int:
    """
    Insert candidates into ats_tenants_candidates (ON CONFLICT DO NOTHING).
    Returns count of rows inserted.
    """
    if not candidates:
        return 0

    if not apply:
        for c in candidates:
            log.info(
                f"  [DRY RUN] {c['ats']:15} | tenant={c['tenant']:30} "
                f"server={c.get('server') or '-':6} | source={c['source']}"
            )
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
                (
                    c["ats"],
                    c["tenant"],
                    c.get("server"),
                    c["source"],
                    c.get("company_name"),
                ),
            )
            if cur.rowcount > 0:
                inserted += 1
        except Exception as e:
            log.warning(f"  Insert failed for {c.get('ats')}/{c.get('tenant')}: {e}")
            conn.rollback()
            continue
    conn.commit()
    cur.close()
    conn.close()
    return inserted


def _ua() -> str:
    return random.choice(USER_AGENTS)


# ============================================================
# SOURCE 5: CERTIFICATE TRANSPARENCY LOGS (crt.sh)
# ============================================================

# ATSs that use tenant-as-subdomain (CT logs are useful for these)
CT_LOG_TARGETS = [
    ("workday",        "%.myworkdayjobs.com",   r'^([a-z0-9][a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com$'),
    ("icims",          "%.icims.com",            r'^([a-z0-9][a-z0-9_-]+)\.icims\.com$'),
    ("taleo",          "%.taleo.net",            r'^([a-z0-9][a-z0-9_-]+)\.taleo\.net$'),
    ("eightfold",      "%.eightfold.ai",         r'^([a-z0-9][a-z0-9_-]+)\.eightfold\.ai$'),
    ("jobvite",        "%.jobvite.com",           r'^([a-z0-9][a-z0-9_-]+)\.jobvite\.com$'),
    ("successfactors", "%.successfactors.com",   r'^([a-z0-9][a-z0-9_-]+)\.successfactors\.com$'),
]


def _parse_ct_entry(name: str, ats: str, pattern: re.Pattern) -> Optional[Dict]:
    """Parse a single CT name_value entry for a given ATS pattern."""
    name = name.strip().lower()
    if not name or name.startswith("*") or name.startswith("@"):
        return None

    m = pattern.match(name)
    if not m:
        return None

    tenant = m.group(1)
    if tenant in DOMAIN_NOISE or len(tenant) < 2:
        return None

    server = m.group(2) if m.lastindex >= 2 else None
    return {
        "ats": ats,
        "tenant": tenant,
        "server": server,
        "source": "ct_logs",
    }


def fetch_ct_logs(known: Dict[str, Set[str]], limit: Optional[int] = None) -> List[Dict]:
    """
    Query crt.sh certificate transparency logs for ATS subdomains.
    Single API call per ATS — typically returns thousands of subdomains.
    """
    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()

    for ats, ct_pattern, name_re in CT_LOG_TARGETS:
        known_ats = known.get(ats, set())
        compiled = re.compile(name_re, re.I)

        url = f"https://crt.sh/?q={requests.utils.quote(ct_pattern)}&output=json"
        log.info(f"CT logs: querying {ats} ({ct_pattern}) ...")

        for attempt in range(3):
            try:
                r = requests.get(
                    url,
                    timeout=90,
                    headers={"User-Agent": _ua(), "Accept": "application/json"},
                )
                if r.status_code == 429:
                    log.warning(f"  crt.sh rate-limited for {ats}, waiting 60s...")
                    time.sleep(60)
                    continue
                if r.status_code != 200:
                    log.warning(f"  crt.sh returned {r.status_code} for {ats}")
                    break
                entries = r.json()
                log.info(f"  {ats}: {len(entries)} cert entries returned")
                break
            except Exception as e:
                log.warning(f"  crt.sh attempt {attempt+1} failed for {ats}: {e}")
                if attempt < 2:
                    time.sleep(10)
                entries = []
        else:
            entries = []

        new_this_ats = 0
        for entry in entries:
            name_value = entry.get("name_value", "")
            for name in name_value.split("\n"):
                c = _parse_ct_entry(name, ats, compiled)
                if not c:
                    continue
                key = (ats, c["tenant"])
                if key in seen or c["tenant"] in known_ats:
                    continue
                seen.add(key)
                candidates.append(c)
                new_this_ats += 1

        log.info(f"  {ats}: {new_this_ats} new tenant candidates from CT logs")

        if limit and len(candidates) >= limit:
            break

        time.sleep(CT_DELAY)

    log.info(f"CT logs total: {len(candidates)} new candidates")
    return candidates


# ============================================================
# SOURCE 4: SERPER JOB BOARD SCRAPING
# ============================================================

# (ats, search_query) pairs — Serper Google search
SERPER_QUERIES = [
    # Workday — site: search on myworkdayjobs.com
    ("workday",   'site:myworkdayjobs.com "data engineer"'),
    ("workday",   'site:myworkdayjobs.com "data scientist"'),
    ("workday",   'site:myworkdayjobs.com "machine learning engineer"'),
    ("workday",   'site:myworkdayjobs.com "analytics engineer"'),
    ("workday",   'site:myworkdayjobs.com "data analyst"'),
    ("workday",   'site:myworkdayjobs.com "applied scientist"'),
    ("workday",   'site:myworkdayjobs.com "ml engineer"'),
    # Greenhouse — boards subdomain
    ("greenhouse", 'site:boards.greenhouse.io "data engineer"'),
    ("greenhouse", 'site:boards.greenhouse.io "data scientist"'),
    ("greenhouse", 'site:boards.greenhouse.io "machine learning engineer"'),
    ("greenhouse", 'site:boards.greenhouse.io "analytics engineer"'),
    ("greenhouse", 'site:boards.greenhouse.io "applied scientist"'),
    ("greenhouse", 'site:job-boards.greenhouse.io "data engineer"'),
    ("greenhouse", 'site:job-boards.greenhouse.io "data scientist"'),
    # Lever
    ("lever",      'site:jobs.lever.co "data engineer"'),
    ("lever",      'site:jobs.lever.co "data scientist"'),
    ("lever",      'site:jobs.lever.co "machine learning"'),
    ("lever",      'site:jobs.lever.co "analytics engineer"'),
    ("lever",      'site:jobs.lever.co "data analyst"'),
    # Ashby
    ("ashby",      'site:jobs.ashbyhq.com "data engineer"'),
    ("ashby",      'site:jobs.ashbyhq.com "data scientist"'),
    ("ashby",      'site:jobs.ashbyhq.com "machine learning"'),
    ("ashby",      'site:jobs.ashbyhq.com "analytics engineer"'),
    # Workable
    ("workable",   'site:apply.workable.com "data engineer"'),
    ("workable",   'site:apply.workable.com "data scientist"'),
    ("workable",   'site:apply.workable.com "machine learning"'),
    # iCIMS
    ("icims",      'site:icims.com "data engineer" "apply now"'),
    ("icims",      'site:icims.com "data scientist" "apply"'),
    # Taleo (many companies use branded Taleo URLs)
    ("taleo",      '"taleo.net" "data engineer" site:taleo.net'),
    ("taleo",      '"taleo.net" "data scientist" site:taleo.net'),
    # SmartRecruiters
    ("smartrecruiters", 'site:careers.smartrecruiters.com "data engineer"'),
    ("smartrecruiters", 'site:careers.smartrecruiters.com "data scientist"'),
]


def _serper_search(query: str, num: int = 10) -> List[Dict]:
    """Run a Serper Google search. Returns list of organic result dicts."""
    if not SERPER_API_KEY:
        log.error("SERPER_API_KEY not set — cannot run Serper queries")
        return []
    try:
        r = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": num, "gl": "us", "hl": "en"},
            timeout=15,
        )
        if r.status_code == 429:
            log.warning("Serper rate-limited, waiting 30s...")
            time.sleep(30)
            return []
        if r.status_code != 200:
            log.warning(f"Serper HTTP {r.status_code} for: {query[:60]}")
            return []
        return r.json().get("organic", [])
    except Exception as e:
        log.warning(f"Serper error ({query[:60]}): {e}")
        return []


def _extract_ats_from_text(text: str) -> List[Tuple[str, str, Optional[str]]]:
    """
    Scan arbitrary text (URL, snippet, title) for ATS tenant slugs.
    Returns list of (ats, tenant, server_or_None).
    """
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


def fetch_serper_ats(known: Dict[str, Set[str]], limit: Optional[int] = None) -> List[Dict]:
    """
    Query Serper with ATS-targeted search queries, extract tenant slugs from URLs.
    """
    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()
    query_count = 0

    for ats, query in SERPER_QUERIES:
        known_ats = known.get(ats, set())
        results = _serper_search(query, num=10)
        query_count += 1

        for result in results:
            # Scan URL + snippet + title for ATS signals
            search_text = " ".join([
                result.get("link", ""),
                result.get("snippet", ""),
                result.get("title", ""),
            ])
            for detected_ats, tenant, server in _extract_ats_from_text(search_text):
                # Use the detected_ats (might differ from query ats if snippet has cross-links)
                key = (detected_ats, tenant)
                if key in seen or tenant in known.get(detected_ats, set()):
                    continue
                seen.add(key)
                candidates.append({
                    "ats": detected_ats,
                    "tenant": tenant,
                    "server": server,
                    "source": "serper",
                })

        log.info(f"  serper [{ats:15}] '{query[:50]}' → {len(results)} results, {len(candidates)} total candidates")

        if limit and len(candidates) >= limit:
            break

        time.sleep(SERPER_DELAY)

    log.info(f"Serper: {len(candidates)} candidates from {query_count} queries")
    return candidates


# ============================================================
# SOURCE 1: COMPANY LIST → ATS PROBING
# ============================================================

def _name_to_slugs(name: str, ticker: str = "") -> List[str]:
    """
    Generate Workday tenant slug candidates from a company name + ticker.
    Returns up to 4 candidates.
    """
    slugs: List[str] = []

    clean = NAME_SUFFIXES.sub("", name)
    clean = re.sub(r"[^a-z0-9\s-]", "", clean.lower()).strip()
    words = clean.split()
    clean_nospace = re.sub(r"\s+", "", clean)

    # Full name, no spaces
    if clean_nospace and len(clean_nospace) >= 2:
        slugs.append(clean_nospace)

    # Hyphenated
    hyphen = "-".join(words) if words else ""
    if hyphen and hyphen != clean_nospace and len(hyphen) >= 3:
        slugs.append(hyphen)

    # First word
    first = words[0] if words else ""
    if first and first not in slugs and len(first) >= 2:
        slugs.append(first)

    # Ticker (lowercase)
    t = ticker.lower().strip()
    if t and t not in slugs and len(t) >= 2:
        slugs.append(t)

    return slugs[:4]


def _name_to_domain(name: str) -> str:
    """Guess the primary .com domain from a company name."""
    clean = re.sub(
        r'\b(Inc\.?|Corp\.?|LLC\.?|Ltd\.?|Co\.?|Group|Holdings|Technologies|'
        r'Solutions|Services|International|Company|Companies|Global|Systems)\b',
        "", name, flags=re.I,
    )
    clean = re.sub(r"[^a-z0-9]", "", clean.lower())
    return f"{clean}.com" if clean else ""


def _probe_workday_slug(args: Tuple[str, str]) -> Optional[Dict]:
    """
    GET https://{slug}.myworkdayjobs.com/ and follow redirects.
    Returns a candidate dict if redirect lands on a wd{N} host.
    """
    slug, company_name = args
    url = f"https://{slug}.myworkdayjobs.com/"
    try:
        r = requests.get(
            url,
            allow_redirects=True,
            timeout=PROBE_TIMEOUT,
            headers={"User-Agent": _ua()},
            stream=True,  # avoid downloading full page body
        )
        # Read only first 512 bytes to check redirect target
        content = r.raw.read(512)
        final = r.url
        m = re.search(
            r"https?://([a-zA-Z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com",
            final, re.IGNORECASE,
        )
        if m and m.group(1).lower() == slug.lower():
            return {
                "ats": "workday",
                "tenant": slug.lower(),
                "server": m.group(2).lower(),
                "source": "company_probe",
                "company_name": company_name,
            }
    except Exception:
        pass
    return None


def _probe_career_page(domain: str, company_name: str) -> Optional[Dict]:
    """
    Probe a company's career pages for ATS signatures.
    Returns detected ATS info or None.
    """
    career_urls = [
        f"https://www.{domain}/careers",
        f"https://www.{domain}/jobs",
        f"https://careers.{domain}",
        f"https://jobs.{domain}",
    ]
    for url in career_urls:
        try:
            r = requests.get(
                url,
                headers={"User-Agent": _ua()},
                timeout=8,
                allow_redirects=True,
            )
            if r.status_code not in (200, 301, 302, 303):
                continue
            content = r.text[:30_000]  # limit parse size

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
                server = m.group(2).lower() if (ats_name == "workday" and m.lastindex >= 2) else None
                if tenant in DOMAIN_NOISE or len(tenant) < 2:
                    continue

                return {
                    "ats": ats_name,
                    "tenant": tenant,
                    "server": server,
                    "source": "company_probe",
                    "company_name": company_name,
                }
            time.sleep(0.3)
        except Exception:
            continue
    return None


def _scrape_fortune1000() -> List[Tuple[str, str]]:
    """Scrape Fortune 1000 company names from Wikipedia. Returns [(name, "")]."""
    url = "https://en.wikipedia.org/wiki/Fortune_1000"
    log.info("Scraping Fortune 1000 from Wikipedia...")
    try:
        r = requests.get(url, headers={"User-Agent": _ua()}, timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "lxml")
        companies = []
        for table in soup.find_all("table", class_="wikitable"):
            headers = [th.get_text(strip=True).lower() for th in table.find_all("th")]
            # Find the company column index
            col_idx = None
            for i, h in enumerate(headers):
                if "company" in h or "name" in h:
                    col_idx = i
                    break
            if col_idx is None:
                col_idx = 1  # default: second column is usually company name

            for row in table.find_all("tr")[1:]:
                cells = row.find_all("td")
                if len(cells) <= col_idx:
                    continue
                name = cells[col_idx].get_text(strip=True)
                name = re.sub(r'\[.*?\]', '', name).strip()  # remove footnote markers
                if name and len(name) > 1:
                    companies.append((name, ""))

        log.info(f"  Fortune 1000: {len(companies)} companies scraped")
        return companies
    except Exception as e:
        log.warning(f"  Fortune 1000 scrape failed: {e}")
        return []


def _scrape_russell3000() -> List[Tuple[str, str]]:
    """
    Fetch Russell 3000 holdings from iShares CSV export.
    Returns [(name, ticker)].
    """
    log.info("Fetching Russell 3000 from iShares CSV...")
    # iShares IWV ETF holdings CSV — URL may drift; update timestamp if it breaks
    urls_to_try = [
        "https://www.ishares.com/us/products/239714/ishares-russell-3000-etf/1467271812596.ajax?tab=holdings&fileType=csv",
        "https://www.ishares.com/us/products/239714/IWV/1467271812596.ajax?tab=holdings&fileType=csv",
    ]
    for url in urls_to_try:
        try:
            r = requests.get(
                url,
                headers={
                    "User-Agent": _ua(),
                    "Referer": "https://www.ishares.com/",
                },
                timeout=30,
            )
            if r.status_code != 200:
                continue

            # iShares CSV has metadata rows at top before the actual table
            text = r.text
            lines = text.splitlines()
            # Find the header row (contains "Name" and "Ticker")
            header_idx = None
            for i, line in enumerate(lines):
                if "Name" in line and "Ticker" in line:
                    header_idx = i
                    break
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
            continue

    log.warning("  Russell 3000: all CSV attempts failed — skipping")
    return []


def _scrape_yc_companies() -> List[Tuple[str, str]]:
    """
    Fetch YC companies from the public YC API.
    Returns [(company_name, "")].
    """
    log.info("Fetching YC companies...")
    batches = "W25,S24,W24,S23,W23,S22,W22,S21,W21,S20,W20"
    url = f"https://api.ycombinator.com/v0.1/companies?batch={batches}&per_page=1000"
    companies = []
    page = 1
    while True:
        try:
            r = requests.get(
                f"{url}&page={page}",
                headers={"User-Agent": _ua()},
                timeout=20,
            )
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


def _fetch_edgar_companies() -> List[Tuple[str, str]]:
    """Fetch SEC EDGAR tickers. Returns [(company_name, ticker)]."""
    log.info("Fetching SEC EDGAR company tickers...")
    try:
        r = requests.get(
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


def _build_company_list() -> List[Tuple[str, str]]:
    """
    Build deduplicated company list from all available sources.
    Returns [(company_name, ticker)].
    """
    all_companies: Dict[str, str] = {}  # name_lower → (canonical_name, ticker)

    for name, ticker in _fetch_edgar_companies():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)

    for name, ticker in _scrape_fortune1000():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)

    for name, ticker in _scrape_russell3000():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)

    for name, ticker in _scrape_yc_companies():
        key = re.sub(r'\W', '', name.lower())
        if key and key not in all_companies:
            all_companies[key] = (name, ticker)

    result = list(all_companies.values())
    log.info(f"Combined company list: {len(result)} unique companies")
    return result


def fetch_company_probes(
    known: Dict[str, Set[str]],
    limit: Optional[int] = None,
    workday_only: bool = False,
) -> List[Dict]:
    """
    Build company list, generate Workday slug candidates, probe concurrently.
    Also probes career pages for non-Workday ATS detection.
    """
    companies = _build_company_list()
    if limit:
        companies = companies[:limit]

    known_workday = known.get("workday", set())
    probes: List[Tuple[str, str]] = []  # (slug, company_name)
    seen_slugs: Set[str] = set(known_workday)

    for name, ticker in companies:
        for slug in _name_to_slugs(name, ticker):
            if slug not in seen_slugs:
                probes.append((slug, name))
                seen_slugs.add(slug)

    log.info(f"Company probe: {len(probes)} Workday slug probes, {len(companies)} career page targets")

    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()
    done = 0

    # Phase 1: Concurrent Workday slug probing
    log.info("Phase 1: Workday slug probing (concurrent)...")
    with ThreadPoolExecutor(max_workers=PROBE_WORKERS) as pool:
        future_map = {pool.submit(_probe_workday_slug, (s, n)): (s, n) for s, n in probes}
        for future in as_completed(future_map):
            done += 1
            if done % 1000 == 0:
                log.info(f"  WD probe: {done}/{len(probes)} slugs done, {len(candidates)} confirmed")
            try:
                result = future.result()
                if result:
                    key = ("workday", result["tenant"])
                    if key not in seen:
                        seen.add(key)
                        candidates.append(result)
                        log.info(f"  ✅ Workday: {result['company_name']} → {result['tenant']}.{result['server']}")
            except Exception as e:
                pass

    log.info(f"Phase 1 complete: {len(candidates)} Workday tenants confirmed")

    if workday_only:
        return candidates

    # Phase 2: Career page ATS detection for non-Workday
    log.info("Phase 2: Career page probing for non-Workday ATSs...")
    career_done = 0
    for name, ticker in companies:
        domain = _name_to_domain(name)
        if not domain or len(domain) < 5:
            continue
        career_done += 1
        if career_done % 500 == 0:
            log.info(f"  Career probe: {career_done}/{len(companies)} domains, {len(candidates)} total candidates")

        result = _probe_career_page(domain, name)
        if result:
            key = (result["ats"], result["tenant"])
            if key not in seen and result["tenant"] not in known.get(result["ats"], set()):
                seen.add(key)
                candidates.append(result)
                log.info(f"  ✅ {result['ats']}: {name} → {result['tenant']}")

        if limit and len(candidates) >= limit:
            break

    log.info(f"Company probes total: {len(candidates)} candidates")
    return candidates


# ============================================================
# SOURCE 2: SITEMAP MINING
# ============================================================

def _fetch_sitemap_urls(domain: str) -> List[str]:
    """Fetch sitemap.xml and return all URLs found."""
    sitemap_paths = ["/sitemap.xml", "/sitemap_index.xml", "/careers/sitemap.xml", "/jobs/sitemap.xml"]
    urls: List[str] = []

    for path in sitemap_paths:
        url = f"https://www.{domain}{path}"
        try:
            r = requests.get(
                url,
                headers={"User-Agent": _ua()},
                timeout=8,
                allow_redirects=True,
            )
            if r.status_code != 200:
                continue

            soup = BeautifulSoup(r.text, "lxml-xml")
            for loc in soup.find_all("loc"):
                loc_url = loc.get_text(strip=True)
                if loc_url:
                    urls.append(loc_url)

            if urls:
                break  # found a working sitemap
        except Exception:
            continue

    return urls


def fetch_sitemap_mining(
    known: Dict[str, Set[str]],
    limit: Optional[int] = None,
) -> List[Dict]:
    """
    Fetch Fortune 1000 company domains, grab sitemaps, parse for ATS URLs.
    """
    # Use Fortune 1000 + Russell 3000 as the domain source (more likely to have sitemaps)
    companies = _scrape_fortune1000()[:1000] + _scrape_russell3000()[:1000]

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

        sitemap_urls = _fetch_sitemap_urls(domain)
        for url in sitemap_urls:
            for ats, tenant, server in _extract_ats_from_text(url):
                key = (ats, tenant)
                if key in seen or tenant in known.get(ats, set()):
                    continue
                seen.add(key)
                candidates.append({
                    "ats": ats,
                    "tenant": tenant,
                    "server": server,
                    "source": "sitemap",
                    "company_name": name,
                })

        if limit and len(candidates) >= limit:
            break

        time.sleep(0.3)

    log.info(f"Sitemap mining: {len(candidates)} candidates from {checked} domains")
    return candidates


# ============================================================
# SOURCE 3: LINKEDIN / JOB BOARDS VIA SERPER SNIPPETS ONLY
# ============================================================

# DO NOT fetch LinkedIn/Indeed/Glassdoor pages directly — use snippet text only
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


def fetch_linkedin_serper(
    known: Dict[str, Set[str]],
    limit: Optional[int] = None,
) -> List[Dict]:
    """
    Use Serper snippets only to find ATS URLs mentioned in job listing text.
    Never fetches LinkedIn/Indeed/Glassdoor pages directly.
    """
    candidates: List[Dict] = []
    seen: Set[Tuple[str, str]] = set()
    query_count = 0

    for query in LINKEDIN_QUERIES:
        results = _serper_search(query, num=10)
        query_count += 1

        for result in results:
            # Only use snippet text — never fetch the page
            snippet_text = " ".join([
                result.get("snippet", ""),
                result.get("title", ""),
                result.get("link", ""),  # Serper link is safe to parse
            ])
            for ats, tenant, server in _extract_ats_from_text(snippet_text):
                key = (ats, tenant)
                if key in seen or tenant in known.get(ats, set()):
                    continue
                seen.add(key)
                candidates.append({
                    "ats": ats,
                    "tenant": tenant,
                    "server": server,
                    "source": "linkedin",
                })

        log.info(f"  linkedin [{query[:50]}] → {len(results)} results, {len(candidates)} total")

        if limit and len(candidates) >= limit:
            break

        time.sleep(SERPER_DELAY)

    log.info(f"LinkedIn/Serper: {len(candidates)} candidates from {query_count} queries")
    return candidates


# ============================================================
# MAIN
# ============================================================

def main():
    ap = argparse.ArgumentParser(
        description="Aggressive multi-source ATS tenant discovery."
    )
    ap.add_argument(
        "--source",
        choices=["all", "ct_logs", "serper", "company_probe", "sitemap", "linkedin"],
        default="all",
        help="Which source to run (default: all, in order: ct_logs→serper→company_probe→sitemap→linkedin)",
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write candidates to ats_tenants_candidates table",
    )
    ap.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Limit candidates per source (useful for testing)",
    )
    ap.add_argument(
        "--workday-only",
        action="store_true",
        help="company_probe source: skip career page probing, only probe Workday slugs",
    )
    args = ap.parse_args()

    if not args.apply:
        log.info("DRY RUN — pass --apply to write to ats_tenants_candidates")

    known = load_known_candidates()
    all_candidates: List[Dict] = []

    sources_to_run = (
        ["ct_logs", "serper", "company_probe", "sitemap", "linkedin"]
        if args.source == "all"
        else [args.source]
    )

    for source in sources_to_run:
        log.info("")
        log.info("=" * 65)
        log.info(f"SOURCE: {source.upper()}")
        log.info("=" * 65)

        if source == "ct_logs":
            new = fetch_ct_logs(known, limit=args.limit)
        elif source == "serper":
            new = fetch_serper_ats(known, limit=args.limit)
        elif source == "company_probe":
            new = fetch_company_probes(known, limit=args.limit, workday_only=args.workday_only)
        elif source == "sitemap":
            new = fetch_sitemap_mining(known, limit=args.limit)
        elif source == "linkedin":
            new = fetch_linkedin_serper(known, limit=args.limit)
        else:
            new = []

        # Save incrementally after each source
        if new:
            inserted = save_candidates(new, apply=args.apply)
            if args.apply:
                log.info(f"  Inserted {inserted} new rows from {source}")
            # Add newly found to known set for dedup in later sources
            for c in new:
                known.setdefault(c["ats"], set()).add(c["tenant"])
            all_candidates.extend(new)

    # Summary
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
    log.info("")
    log.info("Next step: python python/validate_ats_candidates.py --apply")


if __name__ == "__main__":
    main()
