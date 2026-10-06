"""Infra-ceiling coverage: embed streaming/batching and the enrich deadlock.

Offline only. Boilerplate semantics are pinned against the original
implementation's behaviour; DB access is faked at the cursor seam.
"""
from __future__ import annotations

from pathlib import Path

import embed_jobs

ROOT = Path(__file__).resolve().parents[1]

BOILER = "We offer great benefits and equal opportunity employment for everyone here today. "


def _jd(body: str) -> str:
    # Long shared prefix (multiple identical 150-char chunks) + long unique body.
    return (BOILER * 4) + ((body + " ") * 8)


# ---------------------------------------------------------------------------
# Boilerplate semantics (unchanged by the streaming rewrite)
# ---------------------------------------------------------------------------


def test_find_boilerplate_needs_three_jobs():
    assert embed_jobs.find_boilerplate([_jd("alpha role"), _jd("beta role")]) == set()


def test_find_boilerplate_detects_shared_chunks():
    descs = [
        _jd("data pipelines airflow sql python modelling warehouse"),
        _jd("dashboards tableau metrics stakeholders reporting"),
        _jd("experiments causal inference python statistics"),
        _jd("machine learning ranking recommendations systems"),
    ]
    bp = embed_jobs.find_boilerplate(descs)
    assert bp, "shared company boilerplate should be detected"
    stripped = embed_jobs.strip_boilerplate(descs[0], bp)
    assert "equal opportunity" not in stripped.lower()
    assert "airflow" in stripped.lower()


def test_build_text_truncates_and_prefixes_title():
    text = embed_jobs.build_text("Data Engineer", "x " * 3000, set())
    assert text.startswith("Data Engineer. ")
    assert len(text) <= embed_jobs.MAX_TEXT_CHARS


def test_batched_slices_and_validates():
    assert list(embed_jobs.batched([1, 2, 3, 4, 5], 2)) == [[1, 2], [3, 4], [5]]
    try:
        list(embed_jobs.batched([1], 0))
        raise AssertionError("size 0 must raise")
    except ValueError:
        pass


# ---------------------------------------------------------------------------
# Eligibility predicates: domain gate stays, stranded rows only reported
# ---------------------------------------------------------------------------


def test_company_query_keeps_domain_and_missing_gates():
    class Cur:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchall(self):
            return [("c1", 3)]

    cur = Cur()
    assert embed_jobs.companies_needing_embeddings(cur) == ["c1"]
    assert "jp.domain IS NOT NULL" in cur.sql
    assert "jp.embedding IS NULL" in cur.sql
    assert "ORDER BY missing DESC" in cur.sql


def test_stranded_count_targets_domain_null_only():
    class Cur:
        def __init__(self):
            self.sql = ""

        def execute(self, sql, params=None):
            self.sql = sql

        def fetchone(self):
            return (7,)

    cur = Cur()
    assert embed_jobs.count_stranded_domain_null(cur) == 7
    assert "jp.domain IS NULL" in cur.sql


# ---------------------------------------------------------------------------
# Per-company end-to-end with a fake model: only missing rows are written,
# and boilerplate is stripped before encoding.
# ---------------------------------------------------------------------------


class _FakeModel:
    def __init__(self):
        self.seen: list[str] = []

    def encode(self, texts, batch_size=None, show_progress_bar=False, convert_to_numpy=True):
        import numpy as np

        self.seen.extend(texts)
        return np.zeros((len(texts), 3), dtype=float)


class _FakeCur:
    def __init__(self, descriptions, jobs):
        self._descriptions = descriptions
        self._jobs = jobs
        self.updates: list = []
        self.sql_log: list[str] = []

    def execute(self, sql, params=None):
        self.sql_log.append(sql)
        self._sql = sql
        self._params = params

    def fetchall(self):
        if "description_text, ''" in self._sql and "ORDER BY" not in self._sql:
            return [(d,) for d in self._descriptions]
        return list(self._jobs)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_embed_company_streams_and_writes(monkeypatch):
    jobs = [
        ("j1", "Data Engineer", _jd("pipelines airflow sql")),
        ("j2", "Analyst", _jd("dashboards tableau metrics")),
        ("j3", "Scientist", _jd("experiments statistics python")),
        ("j4", "ML Engineer", _jd("ranking models systems")),
    ]
    cur = _FakeCur([j[2] for j in jobs], jobs)
    conn_calls = {"commits": 0}

    class Conn:
        def commit(self):
            conn_calls["commits"] += 1

    captured = {}

    def fake_execute_batch(c, sql, updates, page_size=None):
        captured.setdefault("updates", []).extend(updates)

    monkeypatch.setattr(embed_jobs, "execute_batch", fake_execute_batch)
    model = _FakeModel()
    written = embed_jobs.embed_company(Conn(), cur, model, "c1", batch_size=2)
    assert written == 4
    assert {u[1] for u in captured["updates"]} == {"j1", "j2", "j3", "j4"}
    # Boilerplate stripped: encoded text carries role content, not the boiler.
    assert all("equal opportunity" not in t.lower() for t in model.seen)
    assert any("airflow" in t.lower() for t in model.seen)
    assert conn_calls["commits"] == 2  # one commit per 2-wide micro-batch


# ---------------------------------------------------------------------------
# Static invariants: the enrich selector deadlock fix and the DAG ceiling
# ---------------------------------------------------------------------------


def test_enrich_selector_includes_domain_null():
    src = (ROOT / "python" / "enrich_job_postings.py").read_text()
    assert "jp.domain IS NULL" in src
    # The deadlock explanation must stay attached to the condition.
    assert "enrich/embed deadlock" in src


def test_dag_enrich_limit_drains_backlog():
    src = (ROOT / "airflow" / "dags" / "lander_pipeline.py").read_text()
    assert "--no-llm --limit 40000" in src
    assert "--no-llm --limit 5000" not in src
