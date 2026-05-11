#!/usr/bin/env python3
"""
fetch_contacts.py

Fetches domain-aware hiring manager contacts from Apollo.io.
One contact per (company, domain) pair — stored with the domain so the
frontend can serve the right leader for each job vertical.

Usage:
    python3 python/fetch_contacts.py --domain engineering            # dry-run
    python3 python/fetch_contacts.py --domain engineering --apply    # write DB + spend credits
    python3 python/fetch_contacts.py --domain data_ml --min-jobs 5   # filter by min open jobs
    python3 python/fetch_contacts.py --domain sales --limit 30       # cap at 30 companies
"""

import os, time, logging, argparse, requests, psycopg2
from psycopg2.extras import DictCursor
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

TARGET_SENIORITIES = ["manager", "director", "vp", "c_suite", "head"]

DOMAIN_TITLES = {
    "data_ml": [
        "head of data science",
        "director of data science",
        "director data science",
        "vp data science",
        "vp data",
        "head of machine learning",
        "director of machine learning",
        "director of ai",
        "head of ai",
        "chief data officer",
        "head of analytics",
        "director of analytics",
        "head of data engineering",
        "director of data engineering",
    ],
    "engineering": [
        "vp engineering",
        "vp of engineering",
        "director of engineering",
        "head of engineering",
        "cto",
        "chief technology officer",
        "svp engineering",
    ],
    "sales": [
        "vp sales",
        "vp of sales",
        "chief revenue officer",
        "cro",
        "head of sales",
        "director of sales",
        "svp sales",
    ],
    "finance": [
        "cfo",
        "chief financial officer",
        "vp finance",
        "vp of finance",
        "controller",
        "director of fp&a",
        "head of finance",
        "director of finance",
    ],
    "marketing": [
        "cmo",
        "chief marketing officer",
        "vp marketing",
        "vp of marketing",
        "head of marketing",
        "head of growth",
        "director of marketing",
    ],
    "product": [
        "cpo",
        "chief product officer",
        "vp product",
        "vp of product",
        "head of product",
        "director of product",
    ],
    "design": [
        "head of design",
        "vp design",
        "vp of design",
        "director of design",
        "chief design officer",
    ],
    "ops": [
        "coo",
        "chief operating officer",
        "vp operations",
        "vp of operations",
        "head of operations",
        "head of people",
        "chief people officer",
        "vp people",
        "director of operations",
    ],
}

VALID_DOMAINS = set(DOMAIN_TITLES.keys())


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=int(os.getenv("PGPORT", 5432)),
        dbname=os.getenv("PGDATABASE"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def search_people(company_name: str, domain: str) -> list[dict]:
    """Search Apollo for hiring leaders at a company for the given domain."""
    titles = DOMAIN_TITLES[domain]
    try:
        r = requests.post(SEARCH_URL, headers=HEADERS, json={
            'page': 1,
            'per_page': 5,
            'q_organization_name': company_name,
            'person_titles': titles,
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
    """Reveal full contact details for a person ID. Costs 1 Apollo credit."""
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
    ap.add_argument("--domain", required=True, choices=sorted(VALID_DOMAINS),
                    help="Vertical domain to fetch contacts for")
    ap.add_argument("--apply", action="store_true",
                    help="Write to DB and spend Apollo credits (default: dry-run)")
    ap.add_argument("--min-jobs", type=int, default=5,
                    help="Min open jobs a company must have in this domain (default: 5)")
    ap.add_argument("--limit", type=int, default=50,
                    help="Max companies to process (default: 50)")
    args = ap.parse_args()

    conn = get_conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=DictCursor)

    # Companies with enough jobs in this domain that don't already have
    # a domain-specific contact fetched in the last 30 days
    cur.execute("""
        SELECT c.company_id, c.company_name, COUNT(*) AS job_count
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        WHERE jp.data_tier = 1
          AND jp.status = 'raw'
          AND jp.domain = %s
          AND jp.source != 'adzuna'
          AND NOT EXISTS (
            SELECT 1 FROM company_contacts cc
            WHERE cc.company_id = c.company_id
              AND cc.domain = %s
              AND cc.fetched_at > NOW() - INTERVAL '30 days'
              AND cc.email IS NOT NULL
          )
        GROUP BY c.company_id, c.company_name
        HAVING COUNT(*) >= %s
        ORDER BY job_count DESC
        LIMIT %s
    """, (args.domain, args.domain, args.min_jobs, args.limit))
    companies = cur.fetchall()

    log.info(f"Domain: {args.domain} | Companies needing contacts: {len(companies)} "
             f"(min_jobs={args.min_jobs}, limit={args.limit})")
    log.info(f"Titles searched: {DOMAIN_TITLES[args.domain]}")

    if not companies:
        log.info("Nothing to do.")
        return

    credits_used = 0
    fetched = 0
    skipped = 0

    for company_id, company_name, job_count in companies:
        log.info(f"  [{fetched+skipped+1}/{len(companies)}] {company_name} ({job_count} {args.domain} jobs)")

        people = search_people(company_name, args.domain)
        if not people:
            log.info(f"    No results found")
            skipped += 1
            time.sleep(0.5)
            continue

        best = next((p for p in people if p.get('has_email')), people[0])
        log.info(f"    Found: {best.get('first_name')} {best.get('last_name_obfuscated')} | {best.get('title')}")

        if not args.apply:
            log.info(f"    (dry-run — would reveal email, cost 1 credit)")
            fetched += 1
            continue

        full = reveal_email(best['id'])
        credits_used += 1

        if full and full.get('name'):
            contact_id = 'CC' + args.domain[:3] + best['id'][:6]
            cur.execute("""
                INSERT INTO company_contacts
                    (contact_id, company_id, company_name, full_name, title,
                     email, linkedin_url, seniority, department, domain, source, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'apollo', NOW())
                ON CONFLICT (contact_id) DO UPDATE SET
                    full_name    = EXCLUDED.full_name,
                    title        = EXCLUDED.title,
                    email        = EXCLUDED.email,
                    linkedin_url = EXCLUDED.linkedin_url,
                    domain       = EXCLUDED.domain,
                    fetched_at   = NOW()
            """, (
                contact_id, company_id, company_name,
                full.get('name'), full.get('title'),
                full.get('email'), full.get('linkedin_url'),
                full.get('seniority'),
                full.get('departments', [''])[0] if full.get('departments') else None,
                args.domain,
            ))
            log.info(f"    ✅ {full.get('name')} | {full.get('email')}")
            fetched += 1
        else:
            log.info(f"    ⚠️  No email revealed")
            skipped += 1

        time.sleep(0.5)

    log.info(f"\nDone. domain={args.domain} | Fetched: {fetched} | Skipped: {skipped} | Credits used: {credits_used}")
    cur.execute("SELECT COUNT(*) FROM company_contacts WHERE domain = %s AND email IS NOT NULL", (args.domain,))
    log.info(f"Total {args.domain} contacts in DB: {cur.fetchone()[0]}")
    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
