"""Static regression checks for publication and ghost-pipeline invariants."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_publication_predicate_and_total_are_consistent():
    api = (ROOT / "python/api.py").read_text()
    gate = (ROOT / "python/airflow_quality_gate.py").read_text()
    publisher = (ROOT / "python/publish_snapshot.py").read_text()
    dag = (ROOT / "airflow/dags/lander_pipeline.py").read_text()
    ingest = (ROOT / "python/ingest_jobs.py").read_text()
    assert api.count("COALESCE(jp.loc_country, 'unknown') IN ('US', 'unknown')") >= 4
    assert '"total":        total' in api
    assert "is_public = true" in api
    assert "COALESCE(loc_country,'us') <> 'foreign'" in gate
    assert "scope_status IN ('accepted_core','accepted_evidence')" in gate
    assert "jp.scope_status IN ('accepted_core', 'accepted_evidence')" in publisher
    assert "pg_advisory_xact_lock" in publisher
    assert "publish_gate >> publish_snapshot" in dag
    assert "backfill_role_scope.py --apply --only-missing" in dag
    assert '_loc.country == "foreign"' in ingest


def test_ghost_model_uses_reposts_and_natural_closures():
    ghost = (ROOT / "sql/vw_ghost_job_index.sql").read_text()
    mart = (ROOT / "dbt/job_analytics_dbt/models/marts/core/mart_ghost_job_index.sql").read_text()
    assert "expired_reason='natural_cron'" in ghost
    assert "reappearance_count" in ghost and "related_posting_count" in ghost
    assert "re.posted_date>prior.posted_date" in ghost
    assert "GREATEST(CURRENT_DATE-jp.posted_date, 0)" in ghost
    assert "'unscored'" in ghost and "score_confidence" in ghost
    assert "public.vw_ghost_job_index" in mart


def test_api_exposes_repost_warning_contract():
    api = (ROOT / "python/api.py").read_text()
    assert '"label": "Reposted"' in api
    assert '"repost_warning"' in api
    assert api.count("_add_repost_warning(") >= 4


def test_changed_descriptions_invalidate_enrichment():
    ingest = (ROOT / "python/ingest_jobs.py").read_text()
    for field in ("domain=NULL", "role_category=NULL", "embedding=NULL",
                  "experience_level_v2=NULL", "experience_level_v3=NULL",
                  "experience_level_evidence=NULL", "DELETE FROM job_skills"):
        assert field in ingest


def test_v2_experience_is_promoted_with_canonical_labels():
    classifier = (ROOT / "python/classify_exp_level_v2.py").read_text()
    assert "experience_level_v3=%s" in classifier
    assert "experience_level=%s" in classifier
    assert "experience_level_evidence=%s" in classifier
    assert "experience_level IS DISTINCT FROM jp.experience_level_v3" in classifier
    assert "experience_level_v2" not in classifier


def test_canonicalizer_does_not_hold_schema_lock_during_backfill():
    source = (ROOT / "python" / "canonicalize_opportunities.py").read_text()
    assert "ALTER TABLE job_postings" not in source
    assert "lock_timeout" in source


if __name__ == "__main__":
    test_publication_predicate_and_total_are_consistent()
    test_ghost_model_uses_reposts_and_natural_closures()
    test_api_exposes_repost_warning_contract()
    test_changed_descriptions_invalidate_enrichment()
    test_v2_experience_is_promoted_with_canonical_labels()
    test_canonicalizer_does_not_hold_schema_lock_during_backfill()
    print("publication/ghost pipeline tests passed")
