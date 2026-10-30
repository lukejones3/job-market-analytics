from __future__ import annotations

import ingest_workday_resumable as resumable
import subprocess
import pytest
from contextlib import nullcontext
from types import SimpleNamespace
from threading import Barrier


def test_unique_tenants_preserves_order_and_collapses_multiple_boards():
    rows = [
        ("Acme", "acme", "Careers", "wd5"),
        ("Beta", "beta", "Jobs", "wd1"),
        ("Acme Other", "ACME", "External", "wd5"),
    ]
    assert resumable.unique_tenants(rows) == ["acme", "beta"]


def test_batches_are_bounded_and_lossless():
    assert list(resumable.batches(["a", "b", "c", "d", "e"], 2)) == [
        ["a", "b"],
        ["c", "d"],
        ["e"],
    ]


def test_run_batch_scopes_child_ingestor(monkeypatch):
    captured = {}

    def fake_run(command, env, check):
        captured.update(command=command, env=env, check=check)

    monkeypatch.setattr(resumable.subprocess, "run", fake_run)
    resumable.run_batch(["acme", "beta"], "manual__one", apply=True)

    assert captured["check"] is True
    assert captured["env"]["WORKDAY_TENANT_FILTER"] == "acme,beta"
    assert "--accept-empty" in captured["command"]
    assert captured["command"][-1] == "--apply"
    run_id_index = captured["command"].index("--orchestration-run-id")
    assert captured["command"][run_id_index + 1] == "manual__one"


def test_failed_provider_batch_does_not_strand_later_tenants(monkeypatch):
    attempted = []
    def run(tenants, *args, **kwargs):
        attempted.extend(tenants)
        if tenants == ['bad']:
            raise subprocess.CalledProcessError(3, ['ingest'])
    monkeypatch.setattr(resumable, 'run_batch', run)
    with pytest.raises(resumable.TenantBatchFailures):
        resumable.run_checkpoints(['bad', 'healthy'], 'run', apply=True,
                                 source='workday', batch_size=1, delay=0)
    assert attempted == ['bad', 'healthy']


def test_program_or_write_failure_stops_sweep(monkeypatch):
    attempted = []
    def run(tenants, *args, **kwargs):
        attempted.extend(tenants)
        raise subprocess.CalledProcessError(1, ['ingest'])
    monkeypatch.setattr(resumable, 'run_batch', run)
    with pytest.raises(subprocess.CalledProcessError):
        resumable.run_checkpoints(['bad', 'later'], 'run', apply=True,
                                 source='workday', batch_size=1, delay=0)
    assert attempted == ['bad']


def test_smartrecruiters_checkpoints_scope_the_correct_source(monkeypatch):
    calls = []
    monkeypatch.setattr(resumable.subprocess, 'run', lambda command, env, check: calls.append((command, env)))
    resumable.run_batch(['acme'], 'run', apply=True, source='smartrecruiters')
    command, env = calls[0]
    assert command[command.index('--source')+1] == 'smartrecruiters'
    assert env['SMARTRECRUITERS_TENANT_FILTER'] == 'acme'
    assert env['WORKDAY_TENANT_TIMEOUT_SECONDS'] == '600'


@pytest.mark.parametrize('terminal,clean,expected', [
    ({'healthy','failed'}, {'healthy'}, 'complete_nonzero'),
    ({'healthy'}, {'healthy'}, 'source_failure'),
    ({'failed'}, set(), 'source_failure'),
])
def test_sweep_requires_an_outcome_for_every_planned_tenant(monkeypatch, terminal, clean, expected):
    monkeypatch.setattr(resumable, 'completed_tenants', lambda *a: clean)
    writes = []
    cursor = SimpleNamespace(execute=lambda sql, args: writes.append((sql,args)),
                             fetchall=lambda: [(tenant,) for tenant in terminal])
    conn = SimpleNamespace(cursor=lambda: nullcontext(cursor))
    monkeypatch.setattr(resumable.ingest_jobs, 'get_conn', lambda: nullcontext(conn))
    assert resumable.sweep_record('sweep', 'workday', 'run', ['healthy','failed'], finished=True) == expected
    args = writes[-1][1]
    assert args[3] == expected
    assert args[5].adapted['unaccounted_tenants'] == sorted({'healthy','failed'}-terminal)


def test_company_checkpoints_run_concurrently_and_commit_independently(monkeypatch):
    barrier = Barrier(2, timeout=3)
    completed = []
    def run(tenants, orchestration_run_id, *, apply, source):
        assert len(tenants) == 1
        assert source == 'smartrecruiters'
        barrier.wait()
        completed.extend(tenants)
        if tenants == ['failed']:
            raise subprocess.CalledProcessError(3, ['ingest'])
    monkeypatch.setattr(resumable, 'run_batch', run)
    monkeypatch.setenv('SMARTRECRUITERS_CONCURRENCY', '2')
    with pytest.raises(resumable.TenantBatchFailures):
        resumable.run_checkpoints(['healthy', 'failed'], 'run', apply=True,
                                 source='smartrecruiters', batch_size=8, delay=0)
    assert set(completed) == {'healthy', 'failed'}
