#!/usr/bin/env python3
"""
Day-3 nurture email cron for DataHiringIQ.

Sends a follow-up email to free users who:
  1. Signed up between 3 and 7 days ago
  2. Have NOT converted to paid (upgraded_to_paid_at IS NULL)
  3. Have visited the dashboard at least once (last_seen_at IS NOT NULL)
  4. Have NOT already received the day-3 nurture (nurture_sent_at IS NULL)
  5. Are not Luke's test accounts

Run as cron: daily at 14:00 UTC (9am ET)
  0 14 * * * /opt/job-market-analytics/.venv/bin/python /opt/job-market-analytics/python/nurture_email.py >> /opt/job-market-analytics/logs/nurture_email.log 2>&1

Requires:
  - RESEND_API_KEY in .env
  - PGHOST, PGPORT, PGDATABASE, PGUSER, PGPASSWORD in .env
  - 'requests' library (pip install requests)

First-run: will auto-add nurture_sent_at column to free_signups if missing.
"""

import os
import sys
import time
import psycopg2
import requests
from datetime import datetime, timezone
from urllib.parse import quote

# ── Config ─────────────────────────────────────────────────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY")
RESEND_FROM = os.environ.get("RESEND_FROM", "DataHiringIQ <hello@datahiringiq.com>")
REPLY_TO = "jones31luke@gmail.com"  # replies go to Luke's personal gmail

PGHOST = os.environ.get("PGHOST", "127.0.0.1")
PGPORT = int(os.environ.get("PGPORT", 5432))
PGDATABASE = os.environ.get("PGDATABASE", "job_analytics")
PGUSER = os.environ.get("PGUSER", "lukejones")
PGPASSWORD = os.environ.get("PGPASSWORD")

STRIPE_LINK = "https://buy.stripe.com/3cI4gs9hugN14obehifnO02"
UNSUBSCRIBE_BASE = "https://datahiringiq.com/unsubscribe"

PHYSICAL_ADDRESS = "Luke Jones · 1207 E Forrest St, Suite D PMB 1014, Athens, AL 35613"

# Test accounts to skip
SKIP_EMAIL_PATTERNS = ["jones31luke", "freetest", "headercheck", "debug@"]

# Rate limiting (Resend allows 100/sec but we go slow to be safe)
SLEEP_BETWEEN_SENDS = 1.0  # seconds

DRY_RUN = "--dry-run" in sys.argv

# ── Sanity checks ──────────────────────────────────────────────────────────
if not RESEND_API_KEY:
    print("FATAL: RESEND_API_KEY not set in environment")
    sys.exit(1)
if not PGPASSWORD:
    print("FATAL: PGPASSWORD not set in environment")
    sys.exit(1)


def get_conn():
    return psycopg2.connect(
        host=PGHOST, port=PGPORT, dbname=PGDATABASE,
        user=PGUSER, password=PGPASSWORD
    )


def ensure_nurture_column(conn):
    """Add nurture_sent_at column if it doesn't exist."""
    cur = conn.cursor()
    cur.execute("""
        SELECT column_name FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'free_signups'
          AND column_name = 'nurture_sent_at'
    """)
    if not cur.fetchone():
        print("[setup] Adding nurture_sent_at column to free_signups...")
        cur.execute("""
            ALTER TABLE free_signups
            ADD COLUMN nurture_sent_at TIMESTAMP WITH TIME ZONE
        """)
        conn.commit()
        print("[setup] Column added.")
    cur.close()


def find_eligible_users(conn):
    """Find users who should get the day-3 nurture email."""
    cur = conn.cursor()
    skip_clause = " AND ".join(
        f"email NOT LIKE '%{p}%'" for p in SKIP_EMAIL_PATTERNS
    )
    cur.execute(f"""
        SELECT id, email, signed_up_at, last_seen_at
        FROM free_signups
        WHERE signed_up_at < NOW() - INTERVAL '3 days'
          AND signed_up_at > NOW() - INTERVAL '7 days'
          AND upgraded_to_paid_at IS NULL
          AND last_seen_at IS NOT NULL
          AND nurture_sent_at IS NULL
          AND email IS NOT NULL
          AND email LIKE '%@%'
          AND {skip_clause}
        ORDER BY signed_up_at ASC
    """)
    rows = cur.fetchall()
    cur.close()
    return rows


def build_email_html(email: str) -> str:
    """Build the HTML email body."""
    unsubscribe_url = f"{UNSUBSCRIBE_BASE}?email={quote(email)}"
    return f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; color: #1a1a1a; max-width: 580px; margin: 0 auto; padding: 24px; line-height: 1.5; }}
  a.cta {{ display: inline-block; background: #0f0f0f; color: #e2ff5d; padding: 12px 28px; border-radius: 6px; text-decoration: none; font-weight: 600; margin: 16px 0; }}
  .feature {{ margin: 8px 0; }}
  .footer {{ margin-top: 32px; padding-top: 16px; border-top: 1px solid #eee; font-size: 12px; color: #888; line-height: 1.6; }}
  .footer a {{ color: #888; }}
</style>
</head>
<body>
  <p>Hey,</p>

  <p>You signed up for DataHiringIQ a few days ago. Quick check-in to see how it's going.</p>

  <p>The free tier gets you 500 fresh roles. If you've been actively searching, you might've noticed what's locked behind Pro:</p>

  <div class="feature">→ <strong>Hiring manager LinkedIn for every role</strong> — skip the resume black hole, message the actual person hiring</div>
  <div class="feature">→ <strong>Resume-to-job match scoring</strong> — see your top 50 best-fit roles ranked by skill overlap</div>
  <div class="feature">→ <strong>Full 2,000-job feed</strong> instead of 500</div>
  <div class="feature">→ <strong>Insights dashboard</strong> — top in-demand skills per role, salary premiums, ghost job leaderboards by company (shipping this week)</div>

  <p>$19/month, cancel anytime.</p>

  <p><a href="{STRIPE_LINK}" class="cta">Upgrade to Pro →</a></p>

  <p>Also — if you have feedback, broken bugs, or feature requests, just reply to this email. I read every one.</p>

  <p>Luke<br>
  <span style="color:#666;font-size:14px">Founder, DataHiringIQ</span><br>
  <a href="https://datahiringiq.com" style="color:#666;font-size:14px">datahiringiq.com</a></p>

  <div class="footer">
    {PHYSICAL_ADDRESS}<br>
    You're receiving this because you signed up at datahiringiq.com.
    <a href="{unsubscribe_url}">Unsubscribe</a>
  </div>
</body>
</html>"""


def build_email_text(email: str) -> str:
    """Build the plain text fallback."""
    unsubscribe_url = f"{UNSUBSCRIBE_BASE}?email={quote(email)}"
    return f"""Hey,

You signed up for DataHiringIQ a few days ago. Quick check-in to see how it's going.

The free tier gets you 500 fresh roles. If you've been actively searching, you might've noticed what's locked behind Pro:

→ Hiring manager LinkedIn for every role — skip the resume black hole, message the actual person hiring
→ Resume-to-job match scoring — see your top 50 best-fit roles ranked by skill overlap
→ Full 2,000-job feed instead of 500
→ Insights dashboard — top in-demand skills per role, salary premiums, ghost job leaderboards by company (shipping this week)

$19/month, cancel anytime.

Upgrade: {STRIPE_LINK}

Also — if you have feedback, broken bugs, or feature requests, just reply to this email. I read every one.

Luke
Founder, DataHiringIQ
https://datahiringiq.com

---
{PHYSICAL_ADDRESS}
You're receiving this because you signed up at datahiringiq.com.
Unsubscribe: {unsubscribe_url}
"""


def send_email(email: str) -> bool:
    """Send a single nurture email via Resend. Returns True on success."""
    if DRY_RUN:
        print(f"[DRY RUN] Would send to: {email}")
        return True

    payload = {
        "from": RESEND_FROM,
        "to": [email],
        "reply_to": REPLY_TO,
        "subject": "quick check-in on your DataHiringIQ search",
        "html": build_email_html(email),
        "text": build_email_text(email),
        "headers": {
            "List-Unsubscribe": f"<{UNSUBSCRIBE_BASE}?email={quote(email)}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    }
    try:
        resp = requests.post(
            "https://api.resend.com/emails",
            json=payload,
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json",
            },
            timeout=15,
        )
        if resp.status_code in (200, 202):
            return True
        print(f"  Resend error {resp.status_code}: {resp.text[:200]}")
        return False
    except Exception as e:
        print(f"  Send error: {e}")
        return False


def mark_sent(conn, signup_id: int):
    """Mark a signup as having received the nurture email."""
    if DRY_RUN:
        return
    cur = conn.cursor()
    cur.execute(
        "UPDATE free_signups SET nurture_sent_at = NOW() WHERE id = %s",
        (signup_id,)
    )
    conn.commit()
    cur.close()


def main():
    print(f"=== Day-3 nurture email cron ===")
    print(f"Started at: {datetime.now(timezone.utc).isoformat()}")
    if DRY_RUN:
        print("*** DRY RUN MODE — no emails will be sent, no DB updates ***")

    conn = get_conn()
    ensure_nurture_column(conn)

    eligible = find_eligible_users(conn)
    print(f"Found {len(eligible)} eligible users for day-3 nurture")

    if not eligible:
        print("Nothing to do. Exiting.")
        conn.close()
        return

    sent_count = 0
    failed_count = 0

    for signup_id, email, signed_up_at, last_seen_at in eligible:
        days_since_signup = (datetime.now(timezone.utc) - signed_up_at).days
        print(f"  -> {email} (signed up {days_since_signup}d ago)", end=" ")

        if send_email(email):
            mark_sent(conn, signup_id)
            sent_count += 1
            print("✓")
        else:
            failed_count += 1
            print("✗ FAILED")

        time.sleep(SLEEP_BETWEEN_SENDS)

    conn.close()
    print(f"\nDone. Sent: {sent_count} | Failed: {failed_count}")


if __name__ == "__main__":
    main()
