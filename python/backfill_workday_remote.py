#!/usr/bin/env python3
"""
backfill_workday_remote.py

Re-fetch remoteType from Workday's detail API for active Workday jobs where
workplace_type is NULL or was set only via location-string heuristics (i.e.,
before the remoteType fix landed in fetch_workday_company).

Targets:
  - workplace_type IS NULL
  - workplace_type IN ('hybrid', 'onsite') AND loc_city IS NULL
    (proxy for "multi-location job where string match guessed wrong or missed")

Runtime: ~1,300 jobs × 0.3s ≈ 7 min.

Usage:
    python3 python/backfill_workday_remote.py            # dry run (prints changes)
    python3 python/backfill_workday_remote.py --apply    # write to DB
"""

import re
import sys
import time
import argparse
import requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

# Import DB helpers and the new helper from ingest_jobs
sys.path.insert(0, str(Path(__file__).parent))
from ingest_jobs import get_conn, _parse_remote_type

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "application/json",
    "Content-Type": "application/json",
}

WD_URL_RE = re.compile(
    r'https://([^.]+)\.(wd\d+)\.myworkdayjobs\.com/en-US/([^/]+)/(.+)'
)


def fetch_remote_type(job_url: str) -> str | None:
    """Hit Workday detail API and return normalized workplace_type, or None."""
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
        wt = _parse_remote_type(info.get("remoteType", ""))
        if wt:
            return wt
        # Fallback: location string from detail
        loc = (info.get("location") or "").lower()
        if "remote" in loc or "virtual" in loc:
            return "remote"
        if "hybrid" in loc:
            return "hybrid"
    except Exception:
        pass
    return None


def main(apply: bool):
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        SELECT job_id, job_url, workplace_type
        FROM job_postings
        WHERE ingestion_source = 'workday'
          AND status = 'raw'
          AND (
            workplace_type IS NULL
            OR (workplace_type IN ('hybrid', 'onsite') AND loc_city IS NULL)
          )
        ORDER BY last_seen_at DESC
    """)
    rows = cur.fetchall()
    print(f"Jobs to backfill: {len(rows)}")

    updated = skipped = errors = 0

    for job_id, job_url, old_type in rows:
        new_type = fetch_remote_type(job_url)
        time.sleep(0.3)

        if new_type is None:
            skipped += 1
            continue

        if new_type == old_type:
            skipped += 1
            continue

        tag = f"{str(old_type):8} → {new_type}"
        tenant = (job_url.split(".")[0].replace("https://", ""))
        print(f"  {tag}  [{tenant}]  {job_url[-65:]}")

        if apply:
            try:
                cur.execute(
                    "UPDATE job_postings SET workplace_type = %s WHERE job_id = %s",
                    (new_type, job_id)
                )
                updated += 1
            except Exception as e:
                print(f"    DB error: {e}")
                errors += 1
        else:
            updated += 1  # count as would-update in dry-run

    if apply:
        conn.commit()

    mode = "Applied" if apply else "Dry run —would update"
    print(f"\n{mode}: {updated} jobs  |  no change / no data: {skipped}  |  errors: {errors}")
    if not apply:
        print("Pass --apply to write changes to DB.")

    cur.close()
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write updates to DB")
    args = ap.parse_args()
    main(apply=args.apply)
