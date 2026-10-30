"""Regression coverage for Workday role admission and result-window partitioning."""
from __future__ import annotations

import asyncio

import ingest_jobs
from role_scope import discovery_terms, evaluate_role


def test_workiva_sales_data_analyst_is_admitted_and_discoverable():
    decision = evaluate_role("Sales Data Analyst")
    assert decision.admitted
    assert decision.domain == "data_ml"
    terms = set(discovery_terms())
    assert "data analyst" in terms
    assert "sales data analyst" in terms
    assert "sales analyst" in terms
    assert "sales operations" in terms


def test_workday_query_pages_never_cross_safe_window(monkeypatch):
    calls = []

    async def fake_page(session, url, headers, offset, limit, search_text=""):
        calls.append((offset, limit, search_text))
        if offset == 0:
            return ([{"externalPath": "/job/one"}], 9999, 200)
        return ([{"externalPath": f"/job/{offset}"}], 9999, 200)

    monkeypatch.setattr(ingest_jobs, "_wd_fetch_page", fake_page)
    postings, status = asyncio.run(
        ingest_jobs._wd_fetch_query_pages(None, "url", {}, "data analyst", 20)
    )
    assert status == 200
    assert postings
    assert max(offset for offset, _, _ in calls) < ingest_jobs._WD_SAFE_RESULT_WINDOW
    assert all(search == "data analyst" for _, _, search in calls)


def test_workday_rejects_obvious_foreign_locations_before_detail_fetch():
    assert ingest_jobs._wd_is_obviously_non_us("Pune, Maharashtra, India")
    assert ingest_jobs._wd_is_obviously_non_us("Taguig, Philippines")
    assert ingest_jobs._wd_is_obviously_non_us("Hyderabad---TS---IN")
    assert ingest_jobs._wd_is_obviously_non_us("Bogota-Colombia")
    assert not ingest_jobs._wd_is_obviously_non_us("Seattle, WA")
    assert not ingest_jobs._wd_is_obviously_non_us("Indianapolis, IN")
    assert not ingest_jobs._wd_is_obviously_non_us("United States, Remote")


def test_workday_detail_circuit_opens_and_resets_on_success(monkeypatch):
    monkeypatch.setattr(ingest_jobs, "_wd_host_429s", {})
    url = "https://example.wd5.myworkdayjobs.com/wday/cxs/example/jobs/job/1"
    for _ in range(ingest_jobs._WD_HOST_429_LIMIT - 1):
        assert not ingest_jobs._wd_note_detail_status(url, 429)
    assert ingest_jobs._wd_note_detail_status(url, 429)
    assert ingest_jobs._wd_host_circuit_open(url)

    assert not ingest_jobs._wd_note_detail_status(url, 200)
    assert not ingest_jobs._wd_host_circuit_open(url)


def test_workday_tenant_timeout_returns_without_failing_source(monkeypatch):
    async def blocked_tenant(*_args, **_kwargs):
        await asyncio.sleep(1)
        return []

    monkeypatch.setattr(ingest_jobs, "_WD_TENANT_TIMEOUT_SECS", 0.01)
    monkeypatch.setattr(ingest_jobs, "_fetch_workday_tenant_async", blocked_tenant)
    result = asyncio.run(
        ingest_jobs._fetch_workday_tenant_bounded(None, "Blocked", "blocked", "Jobs", "wd5")
    )
    assert result == []


def test_workday_timeout_keeps_completed_details_without_authorizing_expiry(monkeypatch):
    postings = [
        {'title':'Data Engineer','externalPath':'/job/fast','locationsText':'Austin, TX'},
        {'title':'Data Engineer','externalPath':'/job/slow','locationsText':'Austin, TX'},
    ]
    async def listing(*args, **kwargs):
        return postings, 2, 200
    async def detail(session, url, headers):
        if url.endswith('/slow'):
            await asyncio.sleep(10)
        return 'Build reliable data pipelines. '*20, 'Austin, TX', '2026-10-30', 'hybrid'
    async def no_pause():
        pass
    monkeypatch.setattr(ingest_jobs, '_wd_fetch_page', listing)
    monkeypatch.setattr(ingest_jobs, '_wd_detail', detail)
    monkeypatch.setattr(ingest_jobs, '_wd_check_pause', no_pause)
    monkeypatch.setattr(ingest_jobs, '_WD_TENANT_TIMEOUT_SECS', .05)
    ingest_jobs.reset_tenant_outcomes()
    rows = asyncio.run(ingest_jobs._fetch_workday_tenant_bounded(None, 'Probe', 'probe', 'Jobs', 'wd5'))
    assert len(rows) == 1
    assert rows[0].job_url.endswith('/job/fast')
    assert rows[0].description.startswith('Build reliable')
    assert rows[0].workplace_type == 'hybrid'
    outcome = ingest_jobs.tenant_outcomes('workday')[0]
    assert outcome.status == 'partial_failure'
    assert outcome.detail['completed_details'] == 1
