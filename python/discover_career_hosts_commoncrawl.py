#!/usr/bin/env python3
"""Mine recent Common Crawl URL indexes for career-platform host seeds.

The URL index is used only to discover tenants. Every seed still passes through
live career-host resolution, ATS validation, and shadow quality gates.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
import json
import logging
from pathlib import Path
import hashlib
import re
from typing import Optional

from dotenv import load_dotenv
from psycopg2.extras import Json
import requests

from career_host_engine import company_key, connection, fingerprint_url


ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")
log = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

COLLINFO_URL = "https://index.commoncrawl.org/collinfo.json"
PATTERNS = {
    "bamboohr": "*.bamboohr.com/careers/*",
    "icims": "*.icims.com/jobs/*",
    "oracle_cloud": "*.oraclecloud.com/hcmUI/CandidateExperience/*/sites/*",
    "workable": "apply.workable.com/*",
    "jobvite": "jobs.jobvite.com/*",
    "greenhouse": "job-boards.greenhouse.io/*",
    "greenhouse_legacy": "boards.greenhouse.io/*",
    "lever": "jobs.lever.co/*",
    "ashby": "jobs.ashbyhq.com/*",
    "smartrecruiters": "jobs.smartrecruiters.com/*",
    "taleo": "*.taleo.net/careersection/*",
    "eightfold": "*.eightfold.ai/careers/*",
}


def _humanize(token: str) -> str:
    cleaned = re.sub(r"^(careers?|jobs?)[-_]", "", token, flags=re.I)
    cleaned = re.sub(r"[-_]?(careers?|jobs?)$", "", cleaned, flags=re.I)
    return re.sub(r"[-_]+", " ", cleaned).strip().title() or token.title()


def _latest_indexes(count: int) -> list[str]:
    response = requests.get(COLLINFO_URL, timeout=20)
    response.raise_for_status()
    return [row["id"] for row in response.json()[:count]]


def _query_index(index: str, pattern: str, limit: int) -> list[str]:
    response = requests.get(
        f"https://index.commoncrawl.org/{index}-index",
        params={"url": pattern, "output": "json", "fl": "url,status", "filter": "status:200", "limit": limit},
        timeout=60,
    )
    if response.status_code == 404:
        return []
    response.raise_for_status()
    urls = []
    for line in response.text.splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("url"):
            urls.append(row["url"])
    return urls


def discover(*, apply: bool, platforms: list[str], crawls: int, limit_per_query: int) -> dict:
    indexes = _latest_indexes(crawls)
    seeds: dict[tuple[str, str], dict] = {}
    query_counts = defaultdict(int)
    for index in indexes:
        for platform in platforms:
            urls = _query_index(index, PATTERNS[platform], limit_per_query)
            query_counts[f"{index}:{platform}"] = len(urls)
            for url in urls:
                fingerprint = fingerprint_url(url)
                if not fingerprint or not fingerprint.tenant_token:
                    continue
                key = (fingerprint.platform, fingerprint.tenant_token)
                seeds.setdefault(key, {
                    "platform": fingerprint.platform,
                    "token": fingerprint.tenant_token,
                    "url": url,
                    "strategy": fingerprint.strategy,
                    "server": fingerprint.server,
                    "indexes": [],
                })["indexes"].append(index)

    inserted = 0
    if apply:
        with connection() as conn, conn.cursor() as cur:
            for seed in seeds.values():
                token = seed["token"].split("/", 1)[0]
                name = _humanize(token)
                # Oracle pod hostnames are opaque and cannot establish employer
                # identity. Preserve them for review instead of auto-resolving.
                status = "needs_review" if seed["platform"] == "oracle_cloud" else "pending"
                digest = hashlib.sha256(seed["url"].encode()).hexdigest()[:12]
                key = company_key(name) if status == "pending" else f"oracle-{token}-{digest}"
                cur.execute(
                    """INSERT INTO career_host_candidates
                        (company_name,company_key,discovered_url,discovery_source,status,evidence)
                       VALUES (%s,%s,%s,'commoncrawl',%s,%s)
                       ON CONFLICT (company_key) DO UPDATE SET
                         discovered_url=COALESCE(career_host_candidates.discovered_url,EXCLUDED.discovered_url),
                         evidence=career_host_candidates.evidence || EXCLUDED.evidence""",
                    (name, key, seed["url"], status, Json({
                        "platform": seed["platform"], "tenant": seed["token"],
                        "server": seed["server"], "crawl_indexes": sorted(set(seed["indexes"])),
                    })),
                )
                inserted += int(cur.rowcount > 0)
    return {"indexes": indexes, "query_counts": dict(query_counts), "unique_seeds": len(seeds), "upserted": inserted}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--platform", action="append", choices=sorted(PATTERNS))
    parser.add_argument("--crawls", type=int, default=3)
    parser.add_argument("--limit-per-query", type=int, default=5000)
    args = parser.parse_args()
    platforms = args.platform or list(PATTERNS)
    print(json.dumps(discover(apply=args.apply, platforms=platforms, crawls=args.crawls,
                              limit_per_query=args.limit_per_query), indent=2))


if __name__ == "__main__":
    main()
