"""Purge expired resume content and derived data; dry-run unless --apply."""
from __future__ import annotations

import argparse
import json
import os

import psycopg2
from dotenv import load_dotenv


def enforce(conn, apply: bool = False) -> dict:
    with conn.cursor() as cur:
        cur.execute("SET LOCAL lock_timeout='5s'")
        cur.execute("SET LOCAL statement_timeout='60s'")
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('lander:data-retention'))")
        # The verified schema cascades deletion to entities, evidence, vectors,
        # skills, runs, matches and all private resume scoring tables.
        cur.execute("SELECT count(*) FROM resumes WHERE uploaded_at < now()-interval '30 days'")
        expired = cur.fetchone()[0]
        if apply:
            cur.execute("DELETE FROM resumes WHERE uploaded_at < now()-interval '30 days'")
            cur.execute("DELETE FROM auth_magic_tokens WHERE expires_at < now()-interval '1 day'")
            cur.execute("DELETE FROM user_product_activity_daily WHERE activity_date < current_date-90")
            cur.execute("DELETE FROM product_lifecycle_events WHERE occurred_at < now()-interval '90 days'")
            conn.commit()
        else:
            conn.rollback()
    return {'apply': apply, 'expired_resumes': expired, 'retention_days': 30}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    load_dotenv()
    with psycopg2.connect(host=os.getenv('PGHOST'), port=os.getenv('PGPORT', '5432'),
                          dbname=os.getenv('PGDATABASE'), user=os.getenv('PGUSER'),
                          password=os.getenv('PGPASSWORD')) as conn:
        print(json.dumps(enforce(conn, args.apply), sort_keys=True))


if __name__ == '__main__':
    main()
