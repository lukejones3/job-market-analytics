from python.airflow_quality_gate import INGEST_SOURCES, bad_ingest_sources, apply_checkpoint_sweeps
from datetime import datetime, timezone
import pytest


def complete_rows():
    return {source: (True, 0, 1) for source in INGEST_SOURCES}


def test_ingest_gate_accepts_complete_source_orchestration():
    assert bad_ingest_sources(complete_rows()) == []


def test_ingest_gate_rejects_missing_or_running_source():
    rows = complete_rows()
    rows.pop("lever")
    rows["workday"] = (True, 1, 0)
    assert bad_ingest_sources(rows) == ["lever", "workday"]


def test_ingest_gate_accepts_resumable_source_total():
    rows = complete_rows()
    rows["workday"] = (True, 0, 42_000)
    assert bad_ingest_sources(rows) == []


@pytest.mark.parametrize('status,finished,errors', [
    ('running', None, 0), ('source_failure', datetime.now(timezone.utc), 0),
    ('complete_nonzero', datetime.now(timezone.utc), 1),
    ('complete_nonzero', None, 0)])
def test_unfinished_or_failed_sweep_cannot_hide_healthy_checkpoints(status, finished, errors):
    rows = complete_rows()
    apply_checkpoint_sweeps(rows, [('workday', status, finished, errors)])
    assert bad_ingest_sources(rows) == ['workday']


def test_completed_inventory_retry_can_supersede_interrupted_checkpoints():
    rows = complete_rows()
    rows['workday'] = (False, 2, 42000)
    apply_checkpoint_sweeps(rows, [('workday', 'complete_nonzero', datetime.now(timezone.utc), 0)])
    assert bad_ingest_sources(rows) == []
    assert rows['workday'][2] == 42000
