from python.airflow_quality_gate import INGEST_SOURCES, bad_ingest_sources


def complete_rows():
    return {source: (True, 0, 1) for source in INGEST_SOURCES}


def test_ingest_gate_rejects_failed_latest_tenant():
    rows = complete_rows()
    assert bad_ingest_sources(rows, {"ashby": 1}) == ["ashby"]


def test_ingest_gate_accepts_complete_latest_source_and_tenants():
    assert bad_ingest_sources(complete_rows(), {}) == []


def test_ingest_gate_rejects_missing_or_running_source():
    rows = complete_rows()
    rows.pop("lever")
    rows["workday"] = (True, 1, 0)
    assert bad_ingest_sources(rows, {}) == ["lever", "workday"]
