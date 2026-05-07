#!/usr/bin/env python3
"""
discover_company_ats.py

Probes companies from the DB to detect which ATS they use.
Checks for Greenhouse, Lever, Ashby, Workable, iCIMS, Taleo, Workday.

Writes detected ATS and board_token to discovered_companies table.
Run BEFORE ingest_jobs.py so new companies are picked up the same night.

Usage:
    python python/discover_company_ats.py --dry-run           # show detections, no DB writes
    python python/discover_company_ats.py --apply             # write detections to DB
    python python/discover_company_ats.py --apply --limit 50  # probe 50 companies

Cron (run at midnight before discover and ingest):
    0 0 * * * cd ~/github/job-market-analytics && python python/discover_company_ats.py --apply >> logs/ats_detect.log 2>&1
"""

import hashlib
import logging
import os
import re
import sys
import time
import argparse
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REQUEST_DELAY   = 0.5
REQUEST_TIMEOUT = 8
USER_AGENT = "LanderJobBot/1.0 contact: jones31luke@gmail.com"

# ============================================================
# ATS DETECTION PATTERNS
# ============================================================

# Ordered by signal confidence (most distinct patterns first)
ATS_SIGNALS: List[Tuple[str, re.Pattern]] = [
    ("greenhouse",      re.compile(r'boards\.greenhouse\.io|job-boards\.greenhouse\.io|api\.greenhouse\.io', re.I)),
    ("lever",           re.compile(r'jobs\.lever\.co/', re.I)),
    ("ashby",           re.compile(r'jobs\.ashbyhq\.com|ashbyhq\.com/api', re.I)),
    ("workday",         re.compile(r'myworkdayjobs\.com', re.I)),
    ("smartrecruiters", re.compile(r'smartrecruiters\.com|jobs\.smartrecruiters\.com', re.I)),
    ("icims",           re.compile(r'\.icims\.com', re.I)),
    ("taleo",           re.compile(r'\.taleo\.net', re.I)),
    ("workable",        re.compile(r'apply\.workable\.com|workable\.com/jobs', re.I)),
    ("eightfold",       re.compile(r'\.eightfold\.ai', re.I)),
    ("bamboohr",        re.compile(r'\.bamboohr\.com', re.I)),
    ("jobvite",         re.compile(r'jobs\.jobvite\.com|hire\.jobvite\.com', re.I)),
    ("successfactors",  re.compile(r'successfactors\.com', re.I)),
    ("breezyhr",        re.compile(r'breezy\.hr', re.I)),
]

# ATS board-token extractors: (ats_name, regex with group(1) = token)
ATS_TOKEN_RE: Dict[str, re.Pattern] = {
    "greenhouse":  re.compile(r'(?:boards|job-boards|api)\.greenhouse\.io/(?:v1/boards/)?([a-z0-9_-]+)', re.I),
    "lever":       re.compile(r'jobs\.lever\.co/([a-z0-9_-]+)', re.I),
    "ashby":       re.compile(r'jobs\.ashbyhq\.com/([a-z0-9_-]+)', re.I),
    "workable":    re.compile(r'apply\.workable\.com/([a-z0-9_-]+)', re.I),
    "workday":     re.compile(r'([a-z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com(?:/[^/]+)?/([a-z0-9_-]+)', re.I),
    "icims":       re.compile(r'([a-z0-9_-]+)\.icims\.com', re.I),
    "taleo":       re.compile(r'([a-z0-9_-]+)\.taleo\.net/careersection/([^/]+)', re.I),
    "smartrecruiters": re.compile(r'api\.smartrecruiters\.com/v1/companies/([a-z0-9_-]+)', re.I),
}

# Careers page URL patterns to probe per company name
CAREERS_URL_PATTERNS = [
    "https://www.{domain}/careers",
    "https://www.{domain}/jobs",
    "https://careers.{domain}",
    "https://jobs.{domain}",
    "https://www.{domain}/about/careers",
    "https://www.{domain}/company/careers",
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


def _md5_id(prefix: str, s: str, n: int = 10) -> str:
    return prefix + hashlib.md5(s.encode()).hexdigest()[:n]


def load_companies_to_probe(limit: Optional[int] = None) -> List[Tuple[str, str]]:
    """
    Load companies from the companies table that don't yet have an entry
    in discovered_companies. These are candidates for ATS detection.
    Returns list of (company_name, company_domain_guess).
    """
    conn = get_conn()
    cur = conn.cursor()

    # Companies in DB but not yet in discovered_companies
    cur.execute("""
        SELECT DISTINCT c.company_name
        FROM companies c
        LEFT JOIN discovered_companies dc ON lower(dc.company_name) = lower(c.company_name)
        WHERE dc.company_name IS NULL
          AND length(c.company_name) > 2
        ORDER BY c.company_name
        LIMIT %s
    """, (limit or 5000,))
    rows = cur.fetchall()
    cur.close()
    conn.close()

    results = []
    for (name,) in rows:
        domain = _name_to_domain_guess(name)
        results.append((name, domain))
    return results


def _name_to_domain_guess(name: str) -> str:
    """
    Convert company name to a likely domain for careers page probing.
    E.g. "Acme Corp" -> "acmecorp.com"
    """
    # Strip common suffixes
    clean = re.sub(r'\b(Inc\.?|Corp\.?|LLC\.?|Ltd\.?|Co\.?|Group|Holdings|Technologies|Solutions|Services|International)\b', '', name, flags=re.I)
    clean = re.sub(r'[^a-z0-9]', '', clean.lower())
    return f"{clean}.com"

# ============================================================
# ATS DETECTION
# ============================================================

def _probe_url(url: str) -> Optional[str]:
    """Fetch a URL and return the final text content (after redirects), or None."""
    headers = {"User-Agent": USER_AGENT}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code in (200, 301, 302):
            return r.text
        return None
    except Exception:
        return None


def detect_ats(company_name: str, domain: str) -> Optional[Dict]:
    """
    Probe a company's careers pages to detect which ATS they use.

    Returns dict with:
      ats_source: str ("greenhouse", "lever", etc.)
      board_token: str (slug or token for the ATS)
      careers_url: str (the URL where the ATS was detected)
    Or None if no ATS detected.
    """
    for url_pattern in CAREERS_URL_PATTERNS:
        url = url_pattern.format(domain=domain)
        content = _probe_url(url)
        time.sleep(REQUEST_DELAY)

        if not content:
            continue

        # Check for ATS signals in page content + final URL
        for ats_name, signal_re in ATS_SIGNALS:
            if not signal_re.search(content):
                continue

            # Extract board token
            board_token = ""
            token_re = ATS_TOKEN_RE.get(ats_name)
            if token_re:
                m = token_re.search(content)
                if m:
                    if ats_name == "workday":
                        # Format: tenant/wdN/board
                        board_token = f"{m.group(1)}/{m.group(2)}/{m.group(3)}"
                    elif ats_name == "taleo":
                        # Format: slug/section
                        board_token = f"{m.group(1)}/{m.group(2)}"
                    else:
                        board_token = m.group(1).lower().strip("/")

            if board_token:
                return {
                    "ats_source": ats_name,
                    "board_token": board_token,
                    "careers_url": url,
                }

    return None


def save_detection(cur, company_name: str, detection: Dict, apply: bool) -> bool:
    """
    Write a detected ATS to discovered_companies.
    Returns True if new record inserted.
    """
    ats_source  = detection["ats_source"]
    board_token = detection["board_token"]

    if not apply:
        log.info(f"  [DRY RUN] Would add: {company_name} → {ats_source} / {board_token}")
        return False

    company_id = "DC" + hashlib.md5(f"{ats_source}|{board_token}".encode()).hexdigest()[:10]
    try:
        cur.execute(
            """
            INSERT INTO discovered_companies
                (company_id, company_name, ats_source, board_token,
                 discovery_source, active_roles, total_seen, enabled)
            VALUES (%s, %s, %s, %s, 'ats_detect', 0, 0, true)
            ON CONFLICT (ats_source, board_token) DO NOTHING
            """,
            (company_id, company_name, ats_source, board_token)
        )
        inserted = cur.rowcount > 0
        if inserted:
            log.info(f"  Detected: {company_name} → {ats_source} / {board_token}")
        return inserted
    except Exception as e:
        log.warning(f"  Could not save detection for {company_name}: {e}")
        return False

# ============================================================
# MAIN PIPELINE
# ============================================================

def run_discovery(limit: Optional[int], apply: bool) -> None:
    companies = load_companies_to_probe(limit)
    log.info(f"Probing {len(companies)} companies for ATS detection...")

    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    detected   = 0
    undetected = 0
    errors     = 0
    ats_counts: Dict[str, int] = {}

    for company_name, domain in companies:
        try:
            log.debug(f"Probing: {company_name} ({domain})")
            result = detect_ats(company_name, domain)

            if result:
                saved = save_detection(cur, company_name, result, apply)
                if saved:
                    conn.commit()
                ats = result["ats_source"]
                ats_counts[ats] = ats_counts.get(ats, 0) + 1
                detected += 1
                log.info(f"  ✅ {company_name}: {ats} / {result['board_token']}")
            else:
                undetected += 1
                log.debug(f"  — {company_name}: no ATS detected")

        except Exception as e:
            log.warning(f"  Error probing {company_name}: {e}")
            errors += 1
            try:
                conn.rollback()
            except Exception:
                pass

    cur.close()
    conn.close()

    log.info("=" * 50)
    log.info(f"ATS detection complete:")
    log.info(f"  Detected:   {detected}")
    log.info(f"  No match:   {undetected}")
    log.info(f"  Errors:     {errors}")
    if ats_counts:
        log.info("  By ATS:")
        for ats, count in sorted(ats_counts.items(), key=lambda x: -x[1]):
            log.info(f"    {ats}: {count}")

# ============================================================
# ENTRY POINT
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Detect ATS for companies in the DB.")
    ap.add_argument("--apply",   action="store_true", help="Write detections to DB")
    ap.add_argument("--dry-run", dest="dry_run", action="store_true", help="Show detections only")
    ap.add_argument("--limit",   type=int, default=None, help="Max companies to probe")
    args = ap.parse_args()

    run_discovery(limit=args.limit, apply=args.apply and not args.dry_run)


if __name__ == "__main__":
    main()
