import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))

from ingest_jobs import ensure_observability_schema, ensure_schema_columns


class Cursor:
    def __init__(self, result_sets):
        self.result_sets = list(result_sets)
        self.statements = []

    def execute(self, statement):
        self.statements.append(statement)

    def fetchall(self):
        return self.result_sets.pop(0)

    def fetchone(self):
        return self.result_sets.pop(0)


def test_current_schema_avoids_runtime_ddl():
    from ingest_jobs import _INGESTION_COLUMN_DEFINITIONS

    cursor = Cursor([
        [(name,) for name in _INGESTION_COLUMN_DEFINITIONS],
        ("idx_jp_source",),
        ("ingestion_crawl_runs", "ingestion_tenant_runs", "role_scope_decisions", "job_posting_events"),
    ])
    ensure_schema_columns(cursor)
    ensure_observability_schema(cursor)
    assert all("ALTER TABLE" not in statement for statement in cursor.statements)
    assert all("CREATE INDEX idx_jp_source" not in statement for statement in cursor.statements)


def test_missing_column_bootstraps_only_missing_ddl():
    from ingest_jobs import _INGESTION_COLUMN_DEFINITIONS

    existing = [(name,) for name in _INGESTION_COLUMN_DEFINITIONS if name != "source_checked_at"]
    cursor = Cursor([existing, ("idx_jp_source",)])
    ensure_schema_columns(cursor)
    ddl = next(statement for statement in cursor.statements if "ALTER TABLE" in statement)
    assert "ADD COLUMN source_checked_at timestamptz" in ddl
    assert "ADD COLUMN ingestion_source" not in ddl
