"""Exercise expiry SQL against temporary fixtures in an explicitly named test DB."""
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import psycopg2
import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / 'python'))
import expire_jobs


@pytest.fixture
def conn():
    database = os.getenv('LANDER_TEST_DB', '')
    if not database.startswith('lander_test_'):
        pytest.skip('Requires isolated LANDER_TEST_DB')
    connection = psycopg2.connect(dbname=database, host=os.getenv('PGHOST'),
        user=os.getenv('PGUSER'), password=os.getenv('PGPASSWORD'))
    with connection.cursor() as cur:
        cur.execute("""
            CREATE TEMP TABLE ingestion_crawl_runs(
                run_id text PRIMARY KEY,source text,orchestration_run_id text,
                started_at timestamptz,finished_at timestamptz,status text,
                errors integer DEFAULT 0,detail jsonb DEFAULT '{}');
            CREATE TEMP TABLE ingestion_tenant_runs(run_id text,source text,crawl_tenant text,status text);
            CREATE TEMP TABLE job_postings(job_id text,data_tier integer,status text,
                ingestion_source text,crawl_tenant text,last_seen_at timestamptz,
                expired_reason text,posted_date date);
            CREATE TEMP TABLE job_posting_events(job_id text,event_type text,
                observed_at timestamptz,source text,posted_date date,gap_days integer);
            INSERT INTO ingestion_crawl_runs VALUES
                ('interrupted','workday','test',now()-interval '4 hours',NULL,'running',0,'{"checkpoint_sweep":true}'),
                ('clean','workday','test',now()-interval '3 hours',now(),'complete_nonzero',0,'{}'),
                ('failed','workday','test',now()-interval '2 hours',now(),'source_failure',0,'{}'),
                ('sweep','workday','test',now()-interval '1 hour',now(),'complete_nonzero',0,'{"checkpoint_sweep":true}');
            INSERT INTO ingestion_tenant_runs VALUES
                ('clean','workday','clean','complete_nonzero'),
                ('clean','workday','later_failed','complete_nonzero'),
                ('failed','workday','later_failed','failed');
            INSERT INTO job_postings(job_id,data_tier,status,ingestion_source,crawl_tenant,last_seen_at) VALUES
                ('expire',1,'raw','workday','clean',now()-interval '3 days'),
                ('keep_failed',1,'raw','workday','later_failed',now()-interval '3 days'),
                ('keep_unknown',1,'raw','workday','unattempted',now()-interval '3 days');
        """)
    yield connection
    connection.rollback()
    connection.close()


def run_expiry(conn, monkeypatch):
    # Keep the entire fixture transaction uncommitted and inspect its result.
    proxy = SimpleNamespace(cursor=conn.cursor, commit=lambda: None,
                            rollback=lambda: None, close=lambda: None)
    monkeypatch.setattr(expire_jobs, 'get_conn', lambda: proxy)
    monkeypatch.setattr(sys, 'argv', ['expire_jobs', '--since', 'test'])
    expire_jobs.main()


def test_completed_sweep_allows_only_latest_clean_tenant_expiry(conn, monkeypatch):
    run_expiry(conn, monkeypatch)
    with conn.cursor() as cur:
        cur.execute('SELECT job_id,status FROM job_postings')
        assert dict(cur.fetchall()) == {'expire':'expired','keep_failed':'raw','keep_unknown':'raw'}
        cur.execute('SELECT job_id,event_type FROM job_posting_events')
        assert cur.fetchall() == [('expire','disappeared')]


def test_running_sweep_cannot_authorize_expiry(conn, monkeypatch):
    with conn.cursor() as cur:
        cur.execute("UPDATE ingestion_crawl_runs SET status='running',finished_at=NULL WHERE run_id='sweep'")
    run_expiry(conn, monkeypatch)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM job_postings WHERE status='expired'")
        assert cur.fetchone()[0] == 0
