#!/usr/bin/env python3
"""Flush pending JobPosting lifecycle notifications to Google's Indexing API."""
from __future__ import annotations
import argparse
import json
import os
import psycopg2
from psycopg2.extras import RealDictCursor


def _credentials():
    raw = os.getenv("GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON")
    credential_file = os.getenv("GOOGLE_APPLICATION_CREDENTIALS")
    if not raw and not credential_file:
        return None

    from google.oauth2 import service_account

    scopes = ["https://www.googleapis.com/auth/indexing"]
    if raw:
        return service_account.Credentials.from_service_account_info(json.loads(raw), scopes=scopes)
    if credential_file:
        return service_account.Credentials.from_service_account_file(credential_file, scopes=scopes)


def compact_and_fetch_pending(cur, limit: int):
    """Keep only each URL's newest desired state and return quota-worthy rows."""
    cur.execute(
        """DELETE FROM public.seo_indexing_queue stale
           USING public.seo_indexing_queue newer
           WHERE stale.sent_at IS NULL
             AND stale.url = newer.url
             AND stale.queued_at < newer.queued_at"""
    )
    compacted = cur.rowcount
    cur.execute(
        """SELECT job_id,url,notification_type,queued_at
           FROM public.seo_indexing_queue
           WHERE sent_at IS NULL AND attempts < 5
           ORDER BY
             CASE WHEN notification_type='URL_DELETED' THEN 0 ELSE 1 END,
             queued_at DESC
           LIMIT %s""",
        (limit,),
    )
    return compacted, cur.fetchall()


def main(limit: int = 200) -> None:
    credentials = _credentials()
    if credentials is None:
        print({"status": "skipped", "reason": "Google Indexing credentials not configured"})
        return
    from google.auth.transport.requests import AuthorizedSession
    session = AuthorizedSession(credentials)
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect(host=os.environ["PGHOST"],port=os.getenv("PGPORT","5432"),dbname=os.environ.get("PGDATABASE","job_analytics"),user=os.environ["PGUSER"],password=os.environ["PGPASSWORD"])
    sent=failed=compacted=0
    quota_exhausted = False
    try:
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            compacted, pending = compact_and_fetch_pending(cur, limit)
            for row in pending:
                response=session.post("https://indexing.googleapis.com/v3/urlNotifications:publish",json={"url":row["url"],"type":row["notification_type"]},timeout=20)
                if response.ok:
                    cur.execute("UPDATE public.seo_indexing_queue SET sent_at=now(),attempts=attempts+1,last_error=NULL WHERE job_id=%s AND notification_type=%s AND queued_at=%s",(row["job_id"],row["notification_type"],row["queued_at"]));sent+=1
                else:
                    cur.execute("UPDATE public.seo_indexing_queue SET attempts=attempts+1,last_error=%s WHERE job_id=%s AND notification_type=%s AND queued_at=%s",(response.text[:1000],row["job_id"],row["notification_type"],row["queued_at"]));failed+=1
                    if response.status_code == 429 or "RESOURCE_EXHAUSTED" in response.text:
                        quota_exhausted = True
                        break
    finally: conn.close()
    print({"compacted":compacted,"sent":sent,"failed":failed,"quota_exhausted":quota_exhausted})
if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=200, choices=range(1, 201))
    args = parser.parse_args()
    main(args.limit)
