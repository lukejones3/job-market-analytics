#!/usr/bin/env python3
"""
fetch_headcount.py

Pulls employee headcount from Wikipedia for all active companies.
Stores results in company_headcount table.
Run monthly to keep data fresh.

Usage:
    python python/fetch_headcount.py --apply
    python python/fetch_headcount.py  # dry run
"""

import os, re, time, argparse
import requests
import psycopg2
from psycopg2.extras import DictCursor
from dotenv import load_dotenv
from pathlib import Path
from datetime import datetime, timezone

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def parse_employee_count(raw: str) -> int | None:
    """Parse employee count string to integer."""
    if not raw:
        return None
    # Remove wiki markup
    raw = re.sub(r'\{\{[^}]+\}\}', '', raw)
    raw = re.sub(r'\[\[[^\]]+\]\]', '', raw)
    raw = re.sub(r'<[^>]+>', '', raw)
    raw = raw.strip().rstrip('|').strip()

    # Handle ranges like "10,000-15,000" — take midpoint
    range_match = re.search(r'([\d,]+)\s*[-–]\s*([\d,]+)', raw)
    if range_match:
        low = int(range_match.group(1).replace(',', ''))
        high = int(range_match.group(2).replace(',', ''))
        return (low + high) // 2

    # Handle "~10,000" or "10,000+"
    num_match = re.search(r'([\d,]+)', raw)
    if num_match:
        val = int(num_match.group(1).replace(',', ''))
        if 10 <= val <= 500000:  # sanity check
            return val

    return None

def fetch_wiki_headcount(company_name: str) -> tuple[str | None, int | None]:
    """Fetch employee count from Wikipedia infobox."""
    base = "https://en.wikipedia.org/w/api.php"

    # Step 1 — find the page
    try:
        r = requests.get(base, params={
            "action": "query",
            "list": "search",
            "srsearch": f"{company_name} company",
            "format": "json",
            "srlimit": 1
        }, timeout=10, headers={"User-Agent": "JobMarketAnalytics/1.0"})
        results = r.json().get("query", {}).get("search", [])
        if not results:
            return None, None
        page_title = results[0]["title"]
    except Exception:
        return None, None

    # Step 2 — get raw wikitext
    try:
        r2 = requests.get(base, params={
            "action": "query",
            "titles": page_title,
            "prop": "revisions",
            "rvprop": "content",
            "format": "json",
            "rvsection": 0
        }, timeout=10, headers={"User-Agent": "JobMarketAnalytics/1.0"})
        pages = r2.json().get("query", {}).get("pages", {})
    except Exception:
        return page_title, None

    for page in pages.values():
        content = page.get("revisions", [{}])[0].get("*", "")
        patterns = [
            r'\|\s*num_employees\s*=\s*([^\n\|]+)',
            r'\|\s*employees\s*=\s*([^\n\|]+)',
            r'\|\s*number_of_employees\s*=\s*([^\n\|]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, content, re.IGNORECASE)
            if match:
                raw = match.group(1).strip()
                count = parse_employee_count(raw)
                if count:
                    return page_title, count

    return page_title, None

def ensure_table(cur):
    cur.execute("""
        CREATE TABLE IF NOT EXISTS company_headcount (
            company_name    text PRIMARY KEY,
            employee_count  int,
            wiki_page       text,
            source          text DEFAULT 'wikipedia',
            fetched_at      timestamptz DEFAULT now(),
            updated_at      timestamptz DEFAULT now()
        )
    """)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--limit", type=int, default=999)
    args = ap.parse_args()

    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    ensure_table(cur)
    conn.commit()

    # Get all active companies
    cur.execute("""
        SELECT DISTINCT company_name
        FROM discovered_companies
        WHERE active_roles > 0
        ORDER BY company_name
        LIMIT %s
    """, (args.limit,))
    companies = [r[0] for r in cur.fetchall()]
    print(f"Fetching headcount for {len(companies)} companies...\n")

    found = 0
    not_found = 0
    results = []

    for name in companies:
        page, count = fetch_wiki_headcount(name)
        time.sleep(0.5)  # be polite to Wikipedia

        if count:
            print(f"  ✅ {name:<30} {count:>8,} employees (wiki: {page})")
            found += 1
            results.append((name, count, page))
        else:
            print(f"  ❌ {name:<30} not found")
            not_found += 1

    print(f"\nFound: {found} | Not found: {not_found}")

    if args.apply and results:
        for name, count, page in results:
            cur.execute("""
                INSERT INTO company_headcount (company_name, employee_count, wiki_page, fetched_at, updated_at)
                VALUES (%s, %s, %s, now(), now())
                ON CONFLICT (company_name) DO UPDATE SET
                    employee_count = EXCLUDED.employee_count,
                    wiki_page      = EXCLUDED.wiki_page,
                    updated_at     = now()
            """, (name, count, page))
        conn.commit()
        print(f"\n✅ Saved {len(results)} headcount records")

        # Show hiring intensity preview
        cur.execute("""
            SELECT dc.company_name, dc.active_roles, ch.employee_count,
                   ROUND(dc.active_roles::numeric / ch.employee_count * 100, 2) as hiring_intensity_pct
            FROM discovered_companies dc
            JOIN company_headcount ch ON lower(ch.company_name) = lower(dc.company_name)
            WHERE dc.active_roles > 0
            AND ch.employee_count > 0
            ORDER BY hiring_intensity_pct DESC
            LIMIT 20
        """)
        rows = cur.fetchall()
        if rows:
            print(f"\n{'Company':<25} {'Open Roles':>10} {'Employees':>12} {'Intensity':>10}")
            print("-" * 62)
            for row in rows:
                print(f"{row[0]:<25} {row[1]:>10} {row[2]:>12,} {row[3]:>9}%")
    else:
        print("\nDry run — pass --apply to save")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
