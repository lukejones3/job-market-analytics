"""Real PostgreSQL checks. Run only against an explicitly named restore/test DB.

LANDER_TEST_DB=lander_restore_... python -m pytest tests/test_scale_database.py -v
Every fixture rolls back; Stripe is mocked and no messages are sent.
"""
import os
from pathlib import Path
from unittest.mock import patch

import psycopg2
import pytest
from dotenv import load_dotenv

from python.billing import process_event
from python.enforce_data_retention import enforce


@pytest.fixture
def conn():
    name = os.getenv('LANDER_TEST_DB', '')
    if not name.startswith(('lander_restore_', 'lander_test_')):
        pytest.skip('Requires isolated LANDER_TEST_DB')
    load_dotenv(Path(__file__).parents[1] / '.env')
    connection = psycopg2.connect(dbname=name, host=os.getenv('PGHOST'),
        user=os.getenv('PGUSER'), password=os.getenv('PGPASSWORD'))
    yield connection
    connection.rollback()
    connection.close()


def test_content_dates_ignore_recrawls_but_track_real_edits(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT job_id,content_updated_at FROM job_postings LIMIT 1')
        job_id, before = cur.fetchone()
        cur.execute('UPDATE job_postings SET last_seen_at=now() WHERE job_id=%s RETURNING content_updated_at', (job_id,))
        assert cur.fetchone()[0] == before
        cur.execute("UPDATE job_postings SET description_text=COALESCE(description_text,'') || ' test content edit' WHERE job_id=%s RETURNING content_updated_at", (job_id,))
        assert cur.fetchone()[0] is not None


def test_billing_preserves_identity_deduplicates_and_downgrades_without_deactivation(conn):
    key_id = 'lander_scale_test_account'
    with conn.cursor() as cur:
        cur.execute("INSERT INTO api_keys(key_id,client_name,client_email,api_key_hash,api_key_prefix) VALUES(%s,'Scale test','scale-test@account.invalid',%s,'scale-test')", (key_id, key_id))
    # process_event commits a real transaction, so this synthetic account is
    # explicitly removed afterwards. Only a restore database can run this test.
    try:
        event = {'id': 'evt_scale_test_1', 'created': 1, 'type': 'checkout.session.completed',
                 'data': {'object': {'subscription': 'sub_scale_test', 'customer': 'cus_scale_test', 'client_reference_id': key_id}}}
        current = {'id': 'sub_scale_test', 'customer': 'cus_scale_test', 'status': 'active',
                   'items': {'data': [{'price': {'id': 'price_scale_test'}}]}}
        with patch('python.billing.stripe.Subscription.retrieve', side_effect=lambda _: current):
            assert process_event(event, lambda: conn, lambda _: None, 'price_scale_test')['tier'] == 'pro'
            assert process_event(event, lambda: conn, lambda _: None, 'price_scale_test')['status'] == 'duplicate'
            current['status'] = 'canceled'
            # A delayed checkout event must reconcile the cancellation too.
            event['id'] = 'evt_scale_test_2'
            assert process_event(event, lambda: conn, lambda _: None, 'price_scale_test')['tier'] == 'free'
        with conn.cursor() as cur:
            cur.execute('SELECT active,tier FROM api_keys WHERE key_id=%s', (key_id,))
            assert cur.fetchone() == (True, 'free')
    finally:
        conn.rollback()
        with conn.cursor() as cur:
            cur.execute("DELETE FROM billing_events WHERE event_id LIKE 'evt_scale_test_%'")
            cur.execute('DELETE FROM billing_subscriptions WHERE key_id=%s', (key_id,))
            cur.execute('DELETE FROM billing_customers WHERE key_id=%s', (key_id,))
            cur.execute('DELETE FROM api_keys WHERE key_id=%s', (key_id,))
        conn.commit()


def test_retention_dry_run_leaves_resume_rows_unchanged(conn):
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM resumes')
        before = cur.fetchone()[0]
    result = enforce(conn, apply=False)
    assert not result['apply']
    with conn.cursor() as cur:
        cur.execute('SELECT count(*) FROM resumes')
        assert cur.fetchone()[0] == before


def test_retention_apply_cascades_private_derived_data_in_restore_only(conn):
    result = enforce(conn, apply=True)
    assert result['apply']
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM resumes WHERE uploaded_at<now()-interval '30 days'")
        assert cur.fetchone()[0] == 0
        for table in ['resume_entities', 'resume_skills', 'resume_runs', 'resume_job_matches', 'resume_scores']:
            cur.execute(f'SELECT count(*) FROM {table} d LEFT JOIN resumes r USING(resume_id) WHERE r.resume_id IS NULL')
            assert cur.fetchone()[0] == 0


def test_web_role_can_record_milestones_and_remove_account_activity(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_roles WHERE rolname='lander_web'")
        if not cur.fetchone():
            pytest.skip('No separate web role in this fixture database')
        for table, privileges in [('product_lifecycle_events', ['INSERT', 'SELECT', 'DELETE']),
                                  ('auth_magic_tokens', ['DELETE']), ('api_usage', ['DELETE']),
                                  ('company_radar_alert_deliveries', ['DELETE']), ('outreach_recipients', ['DELETE'])]:
            for privilege in privileges:
                cur.execute('SELECT has_table_privilege(%s,%s,%s)', ('lander_web', table, privilege))
                assert cur.fetchone()[0], (table, privilege)
