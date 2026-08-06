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
    assert not ingest_jobs._wd_is_obviously_non_us("Seattle, WA")
    assert not ingest_jobs._wd_is_obviously_non_us("United States, Remote")
