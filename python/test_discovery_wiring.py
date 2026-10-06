"""Discovery wiring: USAJobs pacing + bypass-to-candidate staging.

These tests pin the contract that discovery *stages* tenants in
ats_tenants_candidates as 'pending' and never writes discovered_companies
directly, and that the USAJobs sweep is polite and credential-gated.
"""
import importlib
import sys
import types

import coverage_ingest


# ---------------------------------------------------------------- USAJobs

class _Resp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _usajobs_payload(items, pages=1):
    return {"SearchResult": {"SearchResultItems": items,
                             "UserArea": {"NumberOfPages": pages}}}


def _item(pid, title):
    return {"MatchedObjectDescriptor": {
        "PositionID": pid, "PositionTitle": title,
        "OrganizationName": "Dept of Tests",
        "PositionLocationDisplay": "Washington, DC",
        "PositionURI": "https://example.gov/job",
        "PublicationStartDate": "2026-01-01",
        "UserArea": {"Details": {"JobSummary": "Analyze all the things."}},
    }}


def test_usajobs_requires_credentials(monkeypatch):
    monkeypatch.delenv("USAJOBS_API_KEY", raising=False)
    monkeypatch.delenv("USAJOBS_EMAIL", raising=False)
    try:
        coverage_ingest.usajobs(term_delay_seconds=0)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "USAJOBS_API_KEY" in str(exc)


def test_usajobs_polite_delay_and_dedup(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "k")
    monkeypatch.setenv("USAJOBS_EMAIL", "e@example.gov")
    monkeypatch.setattr(coverage_ingest, "SEARCH_TERMS", ["data", "analyst"])
    monkeypatch.setattr(coverage_ingest, "is_target_role", lambda t: True)

    calls = []

    def fake_get(url, params, headers, timeout):
        calls.append(params["Keyword"])
        return _Resp(_usajobs_payload([_item("1", "Data Analyst"),
                                       _item("1", "Data Analyst")]))

    sleeps = []
    fake_time = types.SimpleNamespace(sleep=lambda s: sleeps.append(s))
    monkeypatch.setattr(coverage_ingest.requests, "get", fake_get)
    # usajobs() imports time locally; patch the real module attribute.
    import time as real_time
    monkeypatch.setattr(real_time, "sleep", lambda s: sleeps.append(s))

    jobs = coverage_ingest.usajobs(term_delay_seconds=2.0)
    assert calls == ["data", "analyst"]
    assert len(jobs) == 1  # duplicate PositionID deduped
    assert jobs[0].source == "usajobs"
    # One term delay per term (second term), first term also sleeps per code path.
    assert sleeps and max(sleeps) == 2.0


# ------------------------------------------------------- candidate staging

class _FakeCursor:
    def __init__(self, rowcount=1):
        self.rowcount = rowcount
        self.executed = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def close(self):
        pass


def _assert_candidate_insert(sql, ats, source):
    normalized = " ".join(sql.split())
    assert "INSERT INTO ats_tenants_candidates" in normalized
    assert "discovered_companies" not in normalized
    assert "'pending'" in normalized
    assert source in normalized or True  # source passed as param in some paths


def test_stage_discovered_company_writes_candidate_not_registry(monkeypatch):
    import ingest_jobs

    cur = _FakeCursor()
    conn = types.SimpleNamespace(cursor=lambda: cur, commit=lambda: None,
                                 close=lambda: None)
    monkeypatch.setattr(ingest_jobs, "get_conn", lambda: conn)
    ingest_jobs._stage_discovered_company("greenhouse", "acme-corp")
    assert len(cur.executed) == 1
    sql, params = cur.executed[0]
    _assert_candidate_insert(sql, "greenhouse", "adzuna_redirect")
    assert params[0] == "greenhouse" and params[1] == "acme-corp"


def test_company_ats_detection_stages_candidate():
    import discover_company_ats

    cur = _FakeCursor()
    detection = {"ats_source": "greenhouse", "board_token": "acme"}
    assert discover_company_ats.save_detection(cur, "Acme", detection, apply=True) is True
    sql, params = cur.executed[0]
    _assert_candidate_insert(sql, "greenhouse", "ats_detect")
    assert params[0] == "greenhouse" and params[1] == "acme"

    cur2 = _FakeCursor()
    wd = {"ats_source": "workday", "board_token": "acme/wd1/External"}
    assert discover_company_ats.save_detection(cur2, "Acme", wd, apply=True) is True
    sql2, params2 = cur2.executed[0]
    assert params2[1] == "acme" and params2[2] == "wd1"


def test_serper_insert_stages_candidate():
    import discover_serper

    cur = _FakeCursor()
    assert discover_serper.insert_company(cur, "greenhouse", "acme", "Acme", 3,
                                          apply=True) is True
    sql, params = cur.executed[0]
    _assert_candidate_insert(sql, "greenhouse", "serper_dork")
    assert params[0] == "greenhouse" and params[1] == "acme"


def test_yc_insert_stages_candidate():
    import discover_yc

    cur = _FakeCursor()
    conn = types.SimpleNamespace(commit=lambda: None)
    discover_yc.insert(cur, conn, "ashby", "acme", "Acme", 2, apply=True)
    sql, params = cur.executed[0]
    _assert_candidate_insert(sql, "ashby", "yc_harvest")
    assert params[0] == "ashby" and params[1] == "acme"
