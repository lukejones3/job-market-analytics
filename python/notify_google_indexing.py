#!/usr/bin/env python3
"""Flush pending JobPosting lifecycle notifications to Google's Indexing API."""
from __future__ import annotations
import json, os
import psycopg2
from psycopg2.extras import RealDictCursor

def main() -> None:
    raw = os.getenv("GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON")
    if not raw:
        print({"status": "skipped", "reason": "GOOGLE_INDEXING_SERVICE_ACCOUNT_JSON not configured"})
        return
    from google.oauth2 import service_account
    from google.auth.transport.requests import AuthorizedSession
    info = json.loads(raw)
    credentials = service_account.Credentials.from_service_account_info(info, scopes=["https://www.googleapis.com/auth/indexing"])
    session = AuthorizedSession(credentials)
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect(host=os.environ["PGHOST"],port=os.getenv("PGPORT","5432"),dbname=os.environ.get("PGDATABASE","job_analytics"),user=os.environ["PGUSER"],password=os.environ["PGPASSWORD"])
    sent=failed=0
    try:
        with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("SELECT job_id,url,notification_type,queued_at FROM public.seo_indexing_queue WHERE sent_at IS NULL AND attempts<5 ORDER BY queued_at LIMIT 200")
            for row in cur.fetchall():
                response=session.post("https://indexing.googleapis.com/v3/urlNotifications:publish",json={"url":row["url"],"type":row["notification_type"]},timeout=20)
                if response.ok:
                    cur.execute("UPDATE public.seo_indexing_queue SET sent_at=now(),attempts=attempts+1,last_error=NULL WHERE job_id=%s AND notification_type=%s AND queued_at=%s",(row["job_id"],row["notification_type"],row["queued_at"]));sent+=1
                else:
                    cur.execute("UPDATE public.seo_indexing_queue SET attempts=attempts+1,last_error=%s WHERE job_id=%s AND notification_type=%s AND queued_at=%s",(response.text[:1000],row["job_id"],row["notification_type"],row["queued_at"]));failed+=1
    finally: conn.close()
    print({"sent":sent,"failed":failed})
if __name__ == "__main__": main()
