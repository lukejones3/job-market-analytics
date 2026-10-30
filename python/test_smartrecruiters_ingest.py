#!/usr/bin/env python3
"""Regression checks for the SmartRecruiters description-preservation fix.

Root cause (2026-08-09 audit: SmartRecruiters 4,723 raw -> 179 publication
candidates): fetch_smartrecruiters skipped the detail fetch for postings
already stored with a >=200-char description and emitted description="",
while ingest_job's ON CONFLICT clause unconditionally overwrote
description_text with the empty value and treated it as a description
change (nulling domain/role/embedding/experience and deleting skills).
Enrichment then refused the zero-length rows, so SmartRecruiters jobs
oscillated out of the publication candidates. Evidence-admitted titles
failed scope entirely on the empty text, so their last_seen_at never
refreshed and the 72h freshness gate removed them permanently.

Fix: the fetcher always retrieves the detail description, and ingest_job
treats an empty description as "preserve the stored row, refresh
last_seen_at only" for every source.
"""
import sys
from pathlib import Path
from unittest.mock import patch
from threading import Barrier
from contextlib import nullcontext
from types import SimpleNamespace
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import ingest_jobs
from ingest_jobs import RawJob, ingest_job


class FakeCursor:
    """Minimal DictCursor stand-in: records statements, serves one fetchone."""

    def __init__(self, existing=True):
        self.statements = []
        self.existing = existing

    def execute(self, statement, params=None):
        self.statements.append(statement)

    def fetchone(self):
        return {"job_id": "J-existing"} if self.existing else None


def _sr_job(description):
    return RawJob(
        source="smartrecruiters",
        source_id="123",
        title="Data Engineer",
        company="Probe",
        description=description,
    )


def test_empty_description_preserves_existing_row():
    cur = FakeCursor(existing=True)
    assert ingest_job(cur, _sr_job("")) is False
    joined = "\n".join(cur.statements)
    assert "UPDATE job_postings SET last_seen_at = now()" in joined
    assert "INSERT INTO job_postings" not in joined
    assert "DELETE FROM job_skills" not in joined
    assert "domain=NULL" not in joined


def test_empty_description_new_row_is_not_inserted():
    cur = FakeCursor(existing=False)
    assert ingest_job(cur, _sr_job("")) is False
    joined = "\n".join(cur.statements)
    assert "INSERT INTO job_postings" not in joined
    assert "UPDATE job_postings SET last_seen_at = now()" not in joined


def test_smartrecruiters_always_fetches_detail():
    listing = {
        "totalFound": 1,
        "content": [{
            "id": "42",
            "name": "Data Engineer",
            "location": {"city": "Austin", "region": "TX", "country": "us"},
            "releasedDate": "2026-08-01T00:00:00.000Z",
            "ref": "https://api.smartrecruiters.com/v1/companies/Probe/postings/42",
            "typeOfEmployment": {"id": "full-time"},
        }],
    }
    detail = {"jobAd": {"sections": {
        "jobDescription": {"text": "Build data pipelines. " * 20},
        "qualifications": {"text": "Python and SQL. " * 20},
    }}}
    calls = []

    def fake_get(url, params=None, timeout=12):
        calls.append(url)
        return detail if url.endswith("/42") else listing

    with patch.object(ingest_jobs, "_get", side_effect=fake_get), \
            patch.object(ingest_jobs, "_throttle", lambda: None), \
            patch.object(ingest_jobs, "record_success", lambda *a, **k: None):
        jobs = ingest_jobs.fetch_smartrecruiters("Probe", "Probe")

    assert len(jobs) == 1
    assert "Build data pipelines" in (jobs[0].description or "")
    assert any(url.endswith("/42") for url in calls), "detail endpoint must be hit"
    assert jobs[0].posted_date == "2026-08-01"


def test_smartrecruiters_fetches_companies_concurrently_and_keeps_failures(monkeypatch):
    cursor = SimpleNamespace(execute=lambda *a: None,
        fetchall=lambda: [('One', 'one'), ('Two', 'two')], close=lambda: None)
    conn = SimpleNamespace(cursor=lambda: cursor, close=lambda: None)
    monkeypatch.setattr(ingest_jobs, 'get_conn', lambda: conn)
    monkeypatch.setenv('SMARTRECRUITERS_CONCURRENCY', '2')
    barrier = Barrier(2, timeout=3)
    def fetch(name, slug):
        barrier.wait()
        if slug == 'two':
            raise RuntimeError('provider down')
        return [_sr_job('valid')]
    failures = []
    monkeypatch.setattr(ingest_jobs, 'fetch_smartrecruiters', fetch)
    monkeypatch.setattr(ingest_jobs, 'record_failure', lambda *a: failures.append(a))
    assert len(ingest_jobs.fetch_all_smartrecruiters()) == 1
    assert failures[0][:2] == ('smartrecruiters', 'two')


def test_empty_failed_batch_persists_outcomes_and_uses_outage_exit(monkeypatch):
    statements = []
    cursor = SimpleNamespace(execute=lambda sql, args=None: statements.append((sql, args)))
    conn = SimpleNamespace(cursor=lambda: nullcontext(cursor))
    monkeypatch.setattr(ingest_jobs, 'get_conn', lambda: nullcontext(conn))
    def fetch():
        ingest_jobs.record_failure('smartrecruiters', 'failed', 'provider unavailable')
        return []
    monkeypatch.setattr(ingest_jobs, 'fetch_all_smartrecruiters', fetch)
    with pytest.raises(SystemExit) as exc:
        ingest_jobs.run_ingestion('smartrecruiters', True, 'run', accept_empty=True)
    assert exc.value.code == 3
    crawl = next(args for sql, args in statements if 'INSERT INTO ingestion_crawl_runs' in sql)
    assert crawl[3] == 'source_failure'
    tenant = next(args for sql, args in statements if 'INSERT INTO ingestion_tenant_runs' in sql)
    assert tenant[2:4] == ('failed', 'failed')


def test_unavailable_smartrecruiters_detail_is_not_a_clean_tenant_crawl(monkeypatch):
    listing = {'totalFound': 1, 'content': [{'id':'42', 'name':'Data Engineer',
        'location': {'city':'Austin','region':'TX','country':'us'}}]}
    monkeypatch.setattr(ingest_jobs, '_get', lambda url, **kw: None if url.endswith('/42') else listing)
    monkeypatch.setattr(ingest_jobs, '_throttle', lambda: None)
    ingest_jobs.reset_tenant_outcomes()
    assert ingest_jobs.fetch_smartrecruiters('Probe', 'probe') == []
    outcome = ingest_jobs.tenant_outcomes('smartrecruiters')[0]
    assert outcome.status == 'partial_failure'


def test_smartrecruiters_tenant_budget_preserves_partial_rows_and_failure(monkeypatch):
    listing = {'totalFound': 200, 'content': [{'id':'42','name':'Data Engineer',
        'location': {'city':'Austin','region':'TX','country':'us'}}]}
    detail = {'jobAd': {'sections': {'jobDescription': {'text':'Build data pipelines. '*20}}}}
    monkeypatch.setattr(ingest_jobs, '_get', lambda url, **kw: detail if url.endswith('/42') else listing)
    monkeypatch.setattr(ingest_jobs, '_throttle', lambda: None)
    ticks = iter([0, 0, 0, 2])
    monkeypatch.setattr(ingest_jobs.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setenv('SMARTRECRUITERS_TENANT_TIMEOUT_SECONDS', '1')
    ingest_jobs.reset_tenant_outcomes()
    jobs = ingest_jobs.fetch_smartrecruiters('Probe', 'probe')
    assert len(jobs) == 1
    assert ingest_jobs.tenant_outcomes('smartrecruiters')[0].status == 'partial_failure'


if __name__ == "__main__":
    test_empty_description_preserves_existing_row()
    test_empty_description_new_row_is_not_inserted()
    test_smartrecruiters_always_fetches_detail()
