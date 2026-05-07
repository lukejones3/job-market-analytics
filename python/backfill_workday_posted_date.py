#!/usr/bin/env python3
"""
backfill_workday_posted_date.py

Re-fetch startDate from Workday's detail API for active Workday jobs where
posted_date IS NULL (all such jobs, because the harvester previously parsed
postedOn relative strings which were imprecise).

Query: ingestion_source='workday' AND posted_date IS NULL AND status='raw'

Runtime: ~N jobs × 0.3s per request.

Usage:
    python3 python/backfill_workday_posted_date.py            # dry run (prints what would change)
    python3 python/backfill_workday_posted_date.py --apply    # write to DB
"""

import re
import sys
import time
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

sys.path.insert(0, str(Path(__file__).parent))
from ingest_jobs import get_conn, _parse_workday_start_date

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "X-Requested-With": "XMLHttpRequest",
}

WD_URL_RE = re.compile(
    r'https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/en-US/([^/]+)/(.+)'
)


def fetch_start_date(job_url: str) -> str | None:
    """Hit Workday detail API and return startDate as ISO string, or None."""
    m = WD_URL_RE.match(job_url)
    if not m:
        return None
    tenant, server, board, path = m.groups()
    detail_url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/{path}"
    try:
        r = requests.get(detail_url, headers=HEADERS, timeout=10)
        if r.status_code != 200:
            return None
        info = r.json().get("jobPostingInfo", {})
        sd = _parse_workday_start_date(info.get("startDate", ""))
        return str(sd) if sd else None
    except Exception:
        return None


def main(apply: bool):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT job_id, job_url
        FROM job_postings
        WHERE ingestion_source = 'workday'
          AND posted_date IS NULL
          AND status = 'raw'
        ORDER BY last_seen_at DESC
    """)
    rows = cur.fetchall()
    print(f"Jobs to backfill: {len(rows)}")

    updated = skipped = errors = 0

    for i, (job_id, job_url) in enumerate(rows, 1):
        if i % 100 == 0:
            print(f"  ... {i}/{len(rows)} processed (updated={updated} skipped={skipped} errors={errors})")

        start_date = fetch_start_date(job_url)
        time.sleep(0.3)

        if start_date is None:
            skipped += 1
            continue

        tenant = job_url.split(".")[0].replace("https://", "")
        print(f"  {start_date}  [{tenant}]  {job_url[-65:]}")

        if apply:
            try:
                cur.execute(
                    "UPDATE job_postings SET posted_date = %s WHERE job_id = %s",
                    (start_date, job_id)
                )
                updated += 1
            except Exception as e:
                print(f"    DB error: {e}")
                errors += 1
        else:
            updated += 1

    if apply:
        conn.commit()

    mode = "Applied" if apply else "Dry run — would update"
    print(f"\n{mode}: {updated} jobs  |  no data / already set: {skipped}  |  errors: {errors}")
    if not apply:
        print("Pass --apply to write changes to DB.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write updates to DB")
    args = ap.parse_args()
    main(apply=args.apply)
