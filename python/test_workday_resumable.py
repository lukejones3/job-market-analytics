from __future__ import annotations

import ingest_workday_resumable as resumable


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
    assert captured["command"][-1] == "--apply"
    assert captured["command"][-3:-1] == ["--orchestration-run-id", "manual__one"]
