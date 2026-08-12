#!/usr/bin/env python3
"""Deliver idempotent Company Radar alert digests through Resend."""

from __future__ import annotations

import argparse
import hashlib
import html
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any

import psycopg2
import requests
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor

load_dotenv(Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("company-radar-notify")

RESEND_URL = "https://api.resend.com/emails"
APP_URL = os.getenv("LANDER_APP_URL", "https://landerjob.com").rstrip("/")
FROM_ADDRESS = os.getenv("RESEND_FROM", "Lander <hello@landerjob.com>")


def db_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"), port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE"), user=os.getenv("PGUSER"), password=os.getenv("PGPASSWORD")
    )


def eligible_users(cur, cadence: str, digest_date: date) -> list[dict[str, Any]]:
    lookback_days = 1 if cadence == "daily" else 7
    cur.execute(
        """
        SELECT ak.key_id AS user_id,ak.client_email,
               (ARRAY_AGG(a.alert_id ORDER BY a.signal_date DESC,a.created_at DESC))[:20] AS alert_ids
        FROM api_keys ak
        JOIN user_notification_prefs prefs ON prefs.key_id=ak.key_id
        JOIN company_radar_alerts a ON a.user_id=ak.key_id
        JOIN user_followed_companies f ON f.user_id=a.user_id AND f.company_id=a.company_id
        LEFT JOIN company_radar_alert_deliveries d
          ON d.user_id=ak.key_id AND d.cadence=%s AND d.digest_date=%s
        WHERE ak.active=true AND ak.client_email IS NOT NULL
          AND COALESCE(prefs.email_radar_enabled,true)=true
          AND COALESCE(prefs.radar_alert_frequency,'weekly')=%s
          AND f.alert_frequency=%s
          AND a.signal_date >= %s-(%s*interval '1 day')
          AND NOT EXISTS (
            SELECT 1 FROM company_radar_alert_deliveries prior
            WHERE a.alert_id=ANY(prior.alert_ids)
          )
          AND d.delivery_id IS NULL
        GROUP BY ak.key_id,ak.client_email
        ORDER BY MAX(a.signal_date) DESC
        """,
        (cadence, digest_date, cadence, cadence, digest_date, lookback_days),
    )
    return [dict(row) for row in cur.fetchall()]


def alerts_for_ids(cur, alert_ids: list[int]) -> list[dict[str, Any]]:
    cur.execute(
        """SELECT a.alert_id,a.alert_type,a.title,a.detail,a.signal_date,c.company_name,c.company_slug
           FROM company_radar_alerts a JOIN companies c ON c.company_id=a.company_id
           WHERE a.alert_id=ANY(%s::bigint[]) ORDER BY a.signal_date DESC,a.created_at DESC""",
        (alert_ids,),
    )
    return [dict(row) for row in cur.fetchall()]


def render_digest(alerts: list[dict[str, Any]], cadence: str) -> tuple[str, str]:
    companies = list(dict.fromkeys(str(item["company_name"]) for item in alerts))
    subject = f"Company Radar: {len(alerts)} signal{'s' if len(alerts) != 1 else ''} across {len(companies)} compan{'ies' if len(companies) != 1 else 'y'}"
    cards = []
    for item in alerts:
        company = html.escape(str(item["company_name"]))
        title = html.escape(str(item["title"]))
        detail = html.escape(str(item["detail"]))
        slug = str(item["company_slug"])
        url = f"{APP_URL}/radar/{slug}"
        cards.append(
            f'<div style="border:1px solid #2a2d25;border-radius:10px;padding:16px;margin:12px 0;background:#11120f">'
            f'<p style="margin:0 0 5px;color:#d4ff3a;font:600 12px Arial">{company}</p>'
            f'<p style="margin:0 0 6px;color:#f4f4f4;font:600 15px Arial">{title}</p>'
            f'<p style="margin:0 0 12px;color:#9a9a9a;font:13px/1.5 Arial">{detail}</p>'
            f'<a href="{html.escape(url)}" style="color:#d4ff3a;font:600 12px Arial">Open company signal →</a></div>'
        )
    body = (
        '<div style="max-width:620px;margin:auto;background:#0b0c0a;padding:28px;color:#eee">'
        '<p style="margin:0;color:#d4ff3a;font:700 11px Arial;letter-spacing:1.5px">COMPANY RADAR</p>'
        f'<h1 style="margin:12px 0 8px;font:700 28px Arial">Your {html.escape(cadence)} hiring signals</h1>'
        '<p style="margin:0 0 20px;color:#888;font:13px/1.5 Arial">Canonical opportunity movement and verified listing behavior. Bulk ATS refreshes are excluded.</p>'
        + "".join(cards)
        + f'<p style="margin:24px 0 0;color:#666;font:11px/1.5 Arial">Change alert cadence or opt out in <a href="{APP_URL}/settings" style="color:#a8c92f">Lander settings</a>.</p></div>'
    )
    return subject, body


def send_email(api_key: str, recipient: str, subject: str, body: str) -> str:
    response = requests.post(
        RESEND_URL,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"from": FROM_ADDRESS, "to": [recipient], "subject": subject, "html": body},
        timeout=25,
    )
    response.raise_for_status()
    return str(response.json().get("id") or "")


def main():
    parser = argparse.ArgumentParser(description="Deliver Company Radar digests")
    parser.add_argument("--apply", action="store_true", help="Send email and record delivery")
    parser.add_argument("--date", help="Digest date (YYYY-MM-DD), defaults to today")
    args = parser.parse_args()
    digest_date = date.fromisoformat(args.date) if args.date else date.today()
    cadences = ["daily"]
    if digest_date.weekday() == 0:
        cadences.append("weekly")
    api_key = os.getenv("RESEND_API_KEY")
    if args.apply and not api_key:
        log.warning("RESEND_API_KEY is not configured; no Radar email delivered")
        return

    db = db_conn()
    db.autocommit = False
    try:
        with db.cursor(cursor_factory=RealDictCursor) as cur:
            for cadence in cadences:
                for user in eligible_users(cur, cadence, digest_date):
                    alert_ids = [int(value) for value in user["alert_ids"]]
                    alerts = alerts_for_ids(cur, alert_ids)
                    subject, body = render_digest(alerts, cadence)
                    if not args.apply:
                        recipient_ref = hashlib.sha256(user["client_email"].strip().lower().encode()).hexdigest()[:12]
                        log.info("DRY RUN %s digest to ref=%s with %s alerts", cadence, recipient_ref, len(alerts))
                        continue
                    try:
                        message_id = send_email(api_key or "", str(user["client_email"]), subject, body)
                        cur.execute(
                            """INSERT INTO company_radar_alert_deliveries(user_id,cadence,digest_date,alert_ids,provider_message_id)
                               VALUES(%s,%s,%s,%s,%s) ON CONFLICT(user_id,cadence,digest_date) DO NOTHING""",
                            (user["user_id"], cadence, digest_date, alert_ids, message_id),
                        )
                        db.commit()
                        recipient_ref = hashlib.sha256(user["client_email"].strip().lower().encode()).hexdigest()[:12]
                        log.info("Delivered %s Radar alerts to ref=%s", len(alerts), recipient_ref)
                    except Exception:
                        db.rollback()
                        log.exception("Radar digest delivery failed for user %s", user["user_id"])
    finally:
        db.close()


if __name__ == "__main__":
    main()
