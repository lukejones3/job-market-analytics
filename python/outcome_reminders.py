"""Application outcome reminders. Dry run unless explicitly enabled and --apply.

Each account receives at most one reminder in seven days. Historical applications
older than 21 days are excluded; this never bulk-mails the old backlog.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os

import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import RealDictCursor
import requests

ELIGIBLE = """
SELECT DISTINCT ON (a.user_id) a.id,a.user_id,a.job_id,ak.client_email
FROM user_applied_jobs a JOIN api_keys ak ON ak.key_id=a.user_id
LEFT JOIN user_notification_prefs p ON p.key_id=a.user_id
WHERE ak.active=true AND ak.client_email IS NOT NULL
  AND COALESCE(p.email_outcome_enabled,true)
  AND a.applied_at BETWEEN now()-interval '21 days' AND now()-interval '7 days'
  AND a.outcome IS NULL AND a.outcome_email_sent_at IS NULL
  AND NOT EXISTS (SELECT 1 FROM user_applied_jobs recent WHERE recent.user_id=a.user_id
    AND recent.outcome_email_sent_at>now()-interval '7 days')
ORDER BY a.user_id,a.applied_at ASC LIMIT 100
"""


def run(conn, apply=False):
    enabled = os.getenv('OUTCOME_EMAIL_DELIVERY_ENABLED') == '1'
    if apply and not (enabled and os.getenv('RESEND_API_KEY') and os.getenv('RESEND_FROM')):
        raise RuntimeError('Outcome delivery is not configured and explicitly enabled')
    sent = 0
    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext('lander:outcome-reminders')) AS acquired")
        if not cur.fetchone()['acquired']:
            return {'status': 'already running'}
        cur.execute(ELIGIBLE)
        rows = cur.fetchall()
        if apply:
            for row in rows:
                key = hashlib.sha256(f"outcome:{row['user_id']}:{row['job_id']}".encode()).hexdigest()
                response = requests.post('https://api.resend.com/emails', timeout=20,
                    headers={'Authorization': 'Bearer '+os.environ['RESEND_API_KEY'], 'Idempotency-Key': key},
                    json={'from': os.environ['RESEND_FROM'], 'to': [row['client_email']],
                          'subject': 'Any news on your application?',
                          'text': 'You marked a job as applied at least seven days ago. Got a response, interview or offer? Share an update in Lander: https://www.landerjob.com/applied\n\nYou can turn off outcome reminders at https://www.landerjob.com/settings'})
                response.raise_for_status()
                cur.execute('UPDATE user_applied_jobs SET outcome_email_sent_at=now() WHERE id=%s', (row['id'],))
                conn.commit()
                sent += 1
    return {'apply': apply, 'eligible_accounts': len(rows), 'sent': sent, 'delivery_enabled': enabled}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv()
    with psycopg2.connect(host=os.getenv('PGHOST'), port=os.getenv('PGPORT', '5432'),
        dbname=os.getenv('PGDATABASE'), user=os.getenv('PGUSER'), password=os.getenv('PGPASSWORD')) as conn:
        print(json.dumps(run(conn, args.apply), sort_keys=True))
