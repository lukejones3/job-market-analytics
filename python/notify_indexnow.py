#!/usr/bin/env python3
"""Notify IndexNow when Lander's public snapshot and crawl inventory change."""
from __future__ import annotations

import argparse
import os
from collections.abc import Iterable

import psycopg2
import requests
from psycopg2.extras import RealDictCursor


SITE_HOST = "www.landerjob.com"
SITE_ORIGIN = f"https://{SITE_HOST}"
DEFAULT_KEY = "a1ec8603b9981ce316b7b85334211821"  # pragma: allowlist secret (public verification key)
INDEXNOW_ENDPOINT = "https://api.indexnow.org/indexnow"
MAX_URLS = 10_000

ANSWER_SLUGS = (
    "data-analyst-job-market",
    "companies-hiring-data-scientists",
    "remote-product-manager-job-market",
    "chicago-data-scientist-salaries",
    "remote-job-market",
    "job-market-salary-transparency",
    "fastest-growing-company-hiring",
    "companies-with-most-verified-reposts",
)
INSIGHT_SLUGS = (
    "highest-paying-data-roles",
    "remote-jobs-with-salary-data",
    "companies-with-most-open-roles",
    "top-skills-in-data-jobs",
    "remote-friendly-companies",
)
CURATED_PATHS = (
    "/",
    "/market",
    "/answers",
    "/companies",
    "/jobs/skills",
    "/jobs/browse",
    "/methodology",
    *(f"/answers/{slug}" for slug in ANSWER_SLUGS),
    *(f"/insights/{slug}" for slug in INSIGHT_SLUGS),
)


def _connection():
    dsn = os.getenv("DATABASE_URL")
    if dsn:
        return psycopg2.connect(dsn)
    return psycopg2.connect(
        host=os.environ["PGHOST"],
        port=os.getenv("PGPORT", "5432"),
        dbname=os.environ.get("PGDATABASE", "job_analytics"),
        user=os.environ["PGUSER"],
        password=os.environ["PGPASSWORD"],
    )


def _unique(values: Iterable[str], limit: int) -> list[str]:
    return list(dict.fromkeys(values))[:limit]


def collect_urls(cur, limit: int) -> tuple[list[str], list[str]]:
    """Return changed public URLs and the queued URLs represented in the batch."""
    cur.execute(
        """DELETE FROM public.seo_indexing_queue stale
           USING public.seo_indexing_queue newer
           WHERE stale.indexnow_sent_at IS NULL
             AND stale.url = newer.url
             AND stale.queued_at < newer.queued_at"""
    )
    cur.execute(
        """SELECT DISTINCT ON (url) url
           FROM public.seo_indexing_queue
           WHERE indexnow_sent_at IS NULL AND indexnow_attempts < 5
           ORDER BY url, queued_at DESC
           LIMIT %s""",
        (limit,),
    )
    queued = [str(row["url"]) for row in cur.fetchall()]

    remaining = max(0, limit - len(queued) - len(CURATED_PATHS))
    inventory: list[str] = []
    if remaining:
        cur.execute(
            """SELECT path FROM (
                 SELECT '/companies/' || company_slug AS path, job_count AS weight
                 FROM public.seo_company_index
                 UNION ALL
                 SELECT '/jobs/skills/' || skill_slug, job_count
                 FROM public.seo_skill_index
                 UNION ALL
                 SELECT '/jobs/' || replace(role_category, '_', '-') || '/' || location_slug,
                        job_count
                 FROM public.seo_role_location_index
               ) crawlable
               ORDER BY weight DESC, path
               LIMIT %s""",
            (remaining,),
        )
        inventory = [f"{SITE_ORIGIN}{row['path']}" for row in cur.fetchall()]

    urls = _unique(
        [*queued, *(f"{SITE_ORIGIN}{path}" for path in CURATED_PATHS), *inventory],
        limit,
    )
    queued_in_batch = [url for url in queued if url in set(urls)]
    return urls, queued_in_batch


def mark_queue(cur, urls: list[str], *, error: str | None = None) -> None:
    if not urls:
        return
    if error is None:
        cur.execute(
            """UPDATE public.seo_indexing_queue
               SET indexnow_sent_at=now(),indexnow_attempts=indexnow_attempts+1,
                   indexnow_last_error=NULL
               WHERE indexnow_sent_at IS NULL AND url=ANY(%s)""",
            (urls,),
        )
    else:
        cur.execute(
            """UPDATE public.seo_indexing_queue
               SET indexnow_attempts=indexnow_attempts+1,indexnow_last_error=%s
               WHERE indexnow_sent_at IS NULL AND url=ANY(%s)""",
            (error[:1000], urls),
        )


def main(limit: int = MAX_URLS) -> None:
    key = os.getenv("INDEXNOW_KEY", DEFAULT_KEY)
    key_location = f"{SITE_ORIGIN}/{key}.txt"
    with _connection() as conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        urls, queued = collect_urls(cur, limit)
        if not urls:
            print({"status": "skipped", "reason": "no changed URLs"})
            return
        try:
            response = requests.post(
                INDEXNOW_ENDPOINT,
                json={
                    "host": SITE_HOST,
                    "key": key,
                    "keyLocation": key_location,
                    "urlList": urls,
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            mark_queue(cur, queued, error=str(exc))
            raise RuntimeError(f"IndexNow request failed: {exc}") from exc
        if response.status_code not in (200, 202):
            mark_queue(cur, queued, error=response.text or f"HTTP {response.status_code}")
            raise RuntimeError(f"IndexNow rejected batch: HTTP {response.status_code} {response.text[:500]}")
        mark_queue(cur, queued)
        print({"status": "submitted", "http_status": response.status_code, "urls": len(urls), "queued_urls": len(queued)})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=MAX_URLS, choices=range(1, MAX_URLS + 1))
    args = parser.parse_args()
    main(args.limit)
