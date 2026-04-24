#!/usr/bin/env python3
"""Generate a trial access token. Usage: python3 gen_trial.py <name> <email> [days]"""
import sys, secrets, hashlib, psycopg2, os
from pathlib import Path

# Load password from .env
pw = None
for line in open(Path(__file__).parent.parent / '.env'):
    if line.startswith('PGPASSWORD='):
        pw = line.split('=', 1)[1].strip().strip('"').strip("'")

if len(sys.argv) < 3:
    print("Usage: python3 gen_trial.py <name> <email> [days=7]")
    sys.exit(1)

name, email = sys.argv[1], sys.argv[2]
days = int(sys.argv[3]) if len(sys.argv) > 3 else 7

token = secrets.token_urlsafe(32)
token_hash = hashlib.sha256(token.encode()).hexdigest()
prefix = token[:8]

conn = psycopg2.connect(host='208.68.38.249', port=5432, dbname='job_analytics',
    user='lukejones', password=pw)
conn.autocommit = True
cur = conn.cursor()
key_id = "K" + secrets.token_hex(8)
cur.execute(f"""
    INSERT INTO api_keys (key_id, client_name, client_email, api_key_hash, api_key_prefix,
                           tier, active, created_at, expires_at)
    VALUES (%s, %s, %s, %s, %s, 'trial', true, NOW(), NOW() + INTERVAL '{days} days')
""", (key_id, name, email, token_hash, prefix))

url = f"https://job-market-analytics-nyz8zrrujh8bafgniqhjyw.streamlit.app/?token={token}"
print(f"\n✅ Trial access created for {name} ({email})")
print(f"Expires: {days} days\n")
print(f"Send them this link:\n{url}\n")
