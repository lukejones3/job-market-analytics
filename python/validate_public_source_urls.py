#!/usr/bin/env python3
"""Validate application URLs before they enter or remain in the public feed.

Only conclusive evidence closes a source URL. Bot defenses, throttling, timeouts,
and upstream server failures are recorded as inconclusive and never suppress a
posting.
"""
from __future__ import annotations

import argparse
import os
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv(Path(__file__).resolve().parents[1] / ".env")

USER_AGENT = (
    "Mozilla/5.0 (compatible; LanderJobSourceValidator/1.0; "
    "+https://www.landerjob.com)"
)
DEAD_STATUSES = {404, 410}
INCONCLUSIVE_STATUSES = {401, 403, 408, 425, 429}
DEAD_MARKERS = tuple(re.compile(pattern, re.IGNORECASE) for pattern in (
    r"this (?:job|position|posting) (?:is |has been )?(?:no longer available|filled|closed|expired)",
    r"the (?:job|position|posting) you (?:are looking for|requested) (?:is )?no longer available",
    r"we (?:are no longer|aren't) accepting applications for this (?:job|position)",
    r"this requisition (?:is |has been )?(?:closed|filled|cancelled|canceled)",
    r"job posting (?:not found|has expired)",
))
_local = threading.local()


@dataclass(frozen=True)
class Result:
    job_id: str
    verdict: str
    status_code: int | None
    note: str


def connection():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def _session() -> requests.Session:
    if not hasattr(_local, "session"):
        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"})
        _local.session = session
    return _local.session


def classify_response(status_code: int, body: str) -> tuple[str, str]:
    if status_code in DEAD_STATUSES:
        return "dead", f"http_{status_code}"
    if status_code in INCONCLUSIVE_STATUSES or status_code >= 500:
        return "inconclusive", f"http_{status_code}"
    if 200 <= status_code < 400:
        normalized = " ".join(body.split())
        for marker in DEAD_MARKERS:
            if marker.search(normalized):
                return "dead", "closed_page_marker"
        return "alive", f"http_{status_code}"
    # Unknown client errors can be ATS routing/auth behavior. Only universally
    # terminal 404/410 responses are treated as hard closure evidence.
    return "inconclusive", f"http_{status_code}"


def validate_one(job_id: str, url: str, timeout: float) -> Result:
    try:
        with _session().get(url, timeout=timeout, allow_redirects=True, stream=True) as response:
            chunks: list[bytes] = []
            byte_count = 0
            for chunk in response.iter_content(chunk_size=16_384):
                chunks.append(chunk)
                byte_count += len(chunk)
                if byte_count >= 750_000:
                    break
            body = b"".join(chunks).decode(response.encoding or "utf-8", errors="ignore")
            verdict, note = classify_response(response.status_code, body)
            return Result(job_id, verdict, response.status_code, note)
    except requests.RequestException as exc:
        return Result(job_id, "inconclusive", None, f"request_{type(exc).__name__}"[:120])


def candidate_rows(cur, limit: int, all_rows: bool) -> list[tuple[str, str]]:
    refresh_predicate = "TRUE" if all_rows else """
        (
          source_checked_at IS NULL
          OR (is_public = true AND source_checked_at < now() - interval '24 hours')
          OR (source = 'icims' AND source_checked_at < now() - interval '24 hours')
          OR (source_quality_status = 'dead_url' AND source_checked_at < now() - interval '7 days')
        )
    """
    cur.execute(f"""
        SELECT job_id, job_url
        FROM job_postings
        WHERE data_tier = 1
          AND status = 'raw'
          AND last_seen_at >= now() - interval '7 days'
          AND job_url ~* '^https?://'
          AND COALESCE(source_quality_status, 'active') IN ('active', 'dead_url')
          AND {refresh_predicate}
        ORDER BY
          (source_checked_at IS NULL) DESC,
          (source = 'icims') DESC,
          is_public DESC,
          source_checked_at ASC NULLS FIRST,
          last_seen_at DESC
        LIMIT %s
    """, (limit,))
    return cur.fetchall()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--all", action="store_true", help="ignore validation age and rescan current rows")
    parser.add_argument("--limit", type=int, default=10_000)
    parser.add_argument("--workers", type=int, default=20)
    parser.add_argument("--timeout", type=float, default=12.0)
    args = parser.parse_args()

    with connection() as conn, conn.cursor() as cur:
        rows = candidate_rows(cur, args.limit, args.all)
        results: list[Result] = []
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(validate_one, job_id, url, args.timeout): job_id
                for job_id, url in rows
            }
            for future in as_completed(futures):
                results.append(future.result())

        if results:
            execute_values(cur, """
                UPDATE job_postings AS jp
                SET source_checked_at = now(),
                    source_http_status = result.status_code::integer,
                    source_validation_note = result.note,
                    source_quality_status = CASE
                        WHEN result.verdict = 'dead' THEN 'dead_url'
                        WHEN result.verdict = 'alive'
                             AND jp.source_quality_status = 'dead_url' THEN 'active'
                        ELSE jp.source_quality_status
                    END
                FROM (VALUES %s) AS result(job_id, verdict, status_code, note)
                WHERE jp.job_id = result.job_id
            """, [(r.job_id, r.verdict, r.status_code, r.note) for r in results], page_size=1000)
        if not args.apply:
            conn.rollback()

    counts = {verdict: sum(result.verdict == verdict for result in results)
              for verdict in ("alive", "dead", "inconclusive")}
    prefix = "Validated" if args.apply else "Would validate"
    print(f"{prefix} {len(results)} source URLs: {counts}")


if __name__ == "__main__":
    main()
