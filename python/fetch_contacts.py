#!/usr/bin/env python3
"""
fetch_contacts.py

Fetches hiring manager contacts from Apollo.io for companies
with fresh job postings (last 5 days). Stores in company_contacts table.

Usage:
    python3 python/fetch_contacts.py --apply
    python3 python/fetch_contacts.py --apply --days 5
"""

import os, time, logging, argparse, requests, psycopg2
from psycopg2.extras import DictCursor
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

APOLLO_KEY = os.getenv("APOLLO_API_KEY")
if not APOLLO_KEY:
    raise ValueError("APOLLO_API_KEY not set in .env")

SEARCH_URL = "https://api.apollo.io/api/v1/mixed_people/api_search"
MATCH_URL  = "https://api.apollo.io/api/v1/people/match"
HEADERS    = {"Content-Type": "application/json", "Cache-Control": "no-cache", "X-Api-Key": APOLLO_KEY}

# Titles to search for — hiring managers for data/ML roles
TARGET_TITLES = [
    "head of data science",
    "head of data engineering",
    "director of data science",
    "director data science",
    "director of data engineering",
    "director data engineering",
    "vp of data science",
    "vp data",
    "data science manager",
    "data engineering manager",
    "engineering manager data",
    "head of machine learning",
    "director of machine learning",
    "director of ai",
    "head of ai",
    "vp of engineering",
    "chief data officer",
    "head of analytics",
    "director of analytics",
]

TARGET_SENIORITIES = ["manager", "director", "vp", "c_suite", "head"]


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=int(os.getenv("PGPORT", 5432)),
        dbname=os.getenv("PGDATABASE"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def search_people(company_name: str) -> list[dict]:
    """Search Apollo for hiring managers at a company."""
    try:
        r = requests.post(SEARCH_URL, headers=HEADERS, json={
            'page': 1,
            'per_page': 5,
            'q_organization_name': company_name,
            'person_titles': TARGET_TITLES,
            'person_seniorities': TARGET_SENIORITIES,
        }, timeout=15)
        if r.status_code != 200:
            log.warning(f"  Search failed for {company_name}: {r.status_code}")
            return []
        return r.json().get('people', [])
    except Exception as e:
        log.warning(f"  Search error for {company_name}: {e}")
        return []


def reveal_email(person_id: str) -> dict:
    """Reveal full contact details for a person ID. Costs 1 credit."""
    try:
        r = requests.post(MATCH_URL, headers=HEADERS, json={
            'id': person_id,
            'reveal_personal_emails': False,
            'reveal_phone_number': False,
        }, timeout=15)
        if r.status_code != 200:
            return {}
        return r.json().get('person', {})
    except Exception as e:
        log.warning(f"  Match error for {person_id}: {e}")
        return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--days", type=int, default=5)
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=DictCursor)

    # Get companies with fresh jobs that don't have a recent contact
    cur.execute("""
        SELECT DISTINCT c.company_id, c.company_name
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        WHERE jp.data_tier = 1
          AND jp.status = 'raw'
          AND jp.date_found > NOW() - INTERVAL '%s days'
          AND jp.source != 'adzuna'
          AND NOT EXISTS (
            SELECT 1 FROM company_contacts cc
            WHERE cc.company_id = c.company_id
              AND cc.fetched_at > NOW() - INTERVAL '30 days'
              AND cc.email IS NOT NULL
          )
        ORDER BY c.company_name
    """, (args.days,))
    companies = cur.fetchall()
    log.info(f"Companies needing contacts: {len(companies)}")

    credits_used = 0
    fetched = 0
    skipped = 0

    for company_id, company_name in companies:
        log.info(f"  [{fetched+skipped+1}/{len(companies)}] {company_name}")

        people = search_people(company_name)
        if not people:
            log.info(f"    No results found")
            skipped += 1
            time.sleep(0.5)
            continue

        # Take best match — first person with verified email
        best = None
        for p in people:
            if p.get('has_email'):
                best = p
                break
        if not best:
            best = people[0]

        log.info(f"    Found: {best.get('first_name')} {best.get('last_name_obfuscated')} | {best.get('title')}")

        # Reveal email (costs 1 credit)
        if args.apply:
            full = reveal_email(best['id'])
            credits_used += 1

            if full and full.get('name'):
                contact_id = 'CC' + best['id'][:8]
                cur.execute("""
                    INSERT INTO company_contacts
                        (contact_id, company_id, company_name, full_name, title,
                         email, linkedin_url, seniority, department, source, fetched_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, 'apollo', NOW())
                    ON CONFLICT (contact_id) DO UPDATE SET
                        full_name = EXCLUDED.full_name,
                        title = EXCLUDED.title,
                        email = EXCLUDED.email,
                        linkedin_url = EXCLUDED.linkedin_url,
                        fetched_at = NOW()
                """, (
                    contact_id, company_id, company_name,
                    full.get('name'), full.get('title'),
                    full.get('email'), full.get('linkedin_url'),
                    full.get('seniority'), full.get('departments', [''])[0] if full.get('departments') else None
                ))
                log.info(f"    ✅ {full.get('name')} | {full.get('email')}")
                fetched += 1
            else:
                log.info(f"    ⚠️  No email revealed")
                skipped += 1
        else:
            log.info(f"    (dry run - would reveal email)")
            fetched += 1

        time.sleep(0.5)

    log.info(f"\nDone. Fetched: {fetched} | Skipped: {skipped} | Credits used: {credits_used}")
    cur.execute("SELECT COUNT(*) FROM company_contacts WHERE email IS NOT NULL")
    log.info(f"Total contacts in DB: {cur.fetchone()[0]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
