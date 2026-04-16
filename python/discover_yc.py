#!/usr/bin/env python3
"""
discover_yc.py

Harvests YC-funded companies from the public YC API and checks each
for Greenhouse/Ashby/Lever ATS presence by probing their career pages.

Free — no API key required.

Usage:
    python python/discover_yc.py --apply --limit 200
    python python/discover_yc.py --dry-run
"""

import os
import re
import time
import hashlib
import logging
import argparse
from pathlib import Path
from typing import Optional

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

REQUEST_TIMEOUT = 8
REQUEST_DELAY   = 0.3

TARGET_ROLE_RE = re.compile(
    r"data\s+(analyst|scientist|engineer|architect|platform)|"
    r"analytics\s+engineer|machine\s+learning|ml\s+engineer|ai\s+engineer|"
    r"applied\s+scientist|research\s+scientist|business\s+intelligence|"
    r"revenue\s+operations|revops|quantitative|llm\s+engineer",
    re.IGNORECASE,
)

def get_yc_companies(batch: str = "W24,S24,W23,S23,W22,S22,W21,S21") -> list[dict]:
    """Fetch YC companies from public API."""
    all_companies = []
    page = 1
    while True:
        try:
            r = requests.get(
                "https://api.ycombinator.com/v0.1/companies",
                params={"page": page, "batch": batch},
                timeout=10,
                headers={"User-Agent": "Mozilla/5.0"}
            )
            if r.status_code != 200:
                break
            data = r.json()
            companies = data.get("companies", [])
            if not companies:
                break
            all_companies.extend(companies)
            if not data.get("nextPage"):
                break
            page += 1
            time.sleep(0.5)
        except Exception as e:
            log.warning(f"YC API error: {e}")
            break
    return all_companies

def check_greenhouse(website: str) -> Optional[str]:
    """Check if company website links to a Greenhouse board."""
    try:
        r = requests.get(website, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        # Look for Greenhouse board token in page
        m = re.search(
            r'boards\.greenhouse\.io/([a-z0-9\-_]+)|'
            r'job-boards\.greenhouse\.io/([a-z0-9\-_]+)',
            r.text, re.IGNORECASE
        )
        if m:
            return m.group(1) or m.group(2)
    except Exception:
        pass
    return None

def check_ashby(website: str) -> Optional[str]:
    """Check if company website links to an Ashby board."""
    try:
        r = requests.get(website, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r'jobs\.ashbyhq\.com/([a-z0-9\-_]+)', r.text, re.IGNORECASE)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def check_lever(website: str) -> Optional[str]:
    """Check if company website links to a Lever board."""
    try:
        r = requests.get(website, timeout=6, headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r'jobs\.lever\.co/([a-z0-9\-_]+)', r.text, re.IGNORECASE)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None

def probe_greenhouse(token: str) -> tuple[bool, int]:
    try:
        r = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs",
            timeout=REQUEST_TIMEOUT
        )
        if r.status_code == 200:
            jobs = r.json().get("jobs", [])
            target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("title", ""))]
            return len(target) > 0, len(target)
    except Exception:
        pass
    return False, 0

def probe_ashby(token: str) -> tuple[bool, int]:
    try:
        r = requests.get(
            f"https://api.ashbyhq.com/posting-api/job-board/{token}",
            timeout=REQUEST_TIMEOUT
        )
        if r.status_code == 200:
            jobs = r.json().get("jobPostings", [])
            target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("title", ""))]
            return len(target) > 0, len(target)
    except Exception:
        pass
    return False, 0

def probe_lever(token: str) -> tuple[bool, int]:
    try:
        r = requests.get(
            f"https://api.lever.co/v0/postings/{token}?mode=json&limit=250",
            timeout=REQUEST_TIMEOUT
        )
        if r.status_code == 200:
            jobs = r.json() if isinstance(r.json(), list) else []
            target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("text", ""))]
            return len(target) > 0, len(target)
    except Exception:
        pass
    return False, 0

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def load_existing(cur, source: str) -> set[str]:
    cur.execute(
        "SELECT board_token FROM discovered_companies WHERE ats_source = %s",
        (source,)
    )
    return {r["board_token"] for r in cur.fetchall()}

def insert(cur, conn, source: str, token: str, name: str, roles: int, apply: bool):
    company_id = "C" + hashlib.md5(f"{source}:{token}".encode()).hexdigest()[:9]
    if apply:
        cur.execute("""
            INSERT INTO discovered_companies
                (company_id, company_name, ats_source, board_token,
                 active_roles, total_seen, discovery_source, enabled)
            VALUES (%s, %s, %s, %s, %s, %s, 'yc_harvest', true)
            ON CONFLICT (ats_source, board_token) DO NOTHING
        """, (company_id, name, source, token, roles, roles))
        conn.commit()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--batches", default="W24,S24,W23,S23,W22,S22",
                    help="YC batches to harvest")
    args = ap.parse_args()
    apply = args.apply and not args.dry_run

    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    existing_gh = load_existing(cur, "greenhouse")
    existing_as = load_existing(cur, "ashby")
    existing_lv = load_existing(cur, "lever")

    log.info(f"Existing: GH={len(existing_gh)} AS={len(existing_as)} LV={len(existing_lv)}")
    log.info(f"Fetching YC companies for batches: {args.batches}")

    companies = get_yc_companies(args.batches.split(',') if args.batches else None)
    log.info(f"Found {len(companies)} YC companies")

    found = inserted = 0

    for company in companies[:args.limit]:
        name = company.get("name", "")
        website = company.get("website", "")
        if not website:
            continue

        # Ensure https
        if not website.startswith("http"):
            website = f"https://{website}"

        log.info(f"Checking {name} ({website})")

        # Check for ATS links on their website
        gh_token = check_greenhouse(website)
        as_token = check_ashby(website)
        lv_token = check_lever(website)

        if gh_token and gh_token not in existing_gh:
            has_roles, role_count = probe_greenhouse(gh_token)
            if has_roles:
                log.info(f"  ✅ GH {name} ({gh_token}) — {role_count} roles")
                insert(cur, conn, "greenhouse", gh_token, name, role_count, apply)
                inserted += 1
            found += 1

        if as_token and as_token not in existing_as:
            has_roles, role_count = probe_ashby(as_token)
            if has_roles:
                log.info(f"  ✅ AS {name} ({as_token}) — {role_count} roles")
                insert(cur, conn, "ashby", as_token, name, role_count, apply)
                inserted += 1
            found += 1

        if lv_token and lv_token not in existing_lv:
            has_roles, role_count = probe_lever(lv_token)
            if has_roles:
                log.info(f"  ✅ LV {name} ({lv_token}) — {role_count} roles")
                insert(cur, conn, "lever", lv_token, name, role_count, apply)
                inserted += 1
            found += 1

        time.sleep(REQUEST_DELAY)

    log.info(f"\n=== SUMMARY ===")
    log.info(f"YC companies checked: {min(len(companies), args.limit)}")
    log.info(f"New ATS boards found: {found}")
    log.info(f"With target roles inserted: {inserted}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
