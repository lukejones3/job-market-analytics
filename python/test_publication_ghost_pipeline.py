"""Static regression checks for publication and ghost-pipeline invariants."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_publication_predicate_and_total_are_consistent():
    api = (ROOT / "python/api.py").read_text()
    gate = (ROOT / "python/airflow_quality_gate.py").read_text()
    publisher = (ROOT / "python/publish_snapshot.py").read_text()
    seo_refresh = (ROOT / "python/refresh_seo_collection_index.py").read_text()
    dag = (ROOT / "airflow/dags/lander_pipeline.py").read_text()
    ingest = (ROOT / "python/ingest_jobs.py").read_text()
    expiry = (ROOT / "python/expire_jobs.py").read_text()
    assert '"total":        total' in api
    assert "jp.is_public = true" in api
    assert "vw_lander_visible_opportunities" in seo_refresh
    boundary = (ROOT / "sql/publication_boundary.sql").read_text()
    assert "vw_lander_publication_candidates" in gate
    assert "vw_lander_publication_candidates" in publisher
    assert "vw_lander_visible_opportunities" in boundary
    assert "WHERE jp.is_public = true" in boundary
    assert "scope_status IN ('accepted_core', 'accepted_evidence')" in boundary
    assert "representative_rank = 1" in boundary
    assert "location_evidence, '{}'::jsonb) <> '{}'::jsonb" in boundary
    assert "jsonb_object_length" not in boundary
    assert "pg_advisory_xact_lock" in publisher
    assert "publish_gate >> publish_snapshot" in dag
    assert "backfill_role_scope.py --apply --only-missing" in dag
    assert "repair_publication_quality.py --apply" in dag
    assert "validate_public_source_urls.py --apply" in dag
    assert "repair_publication_quality >> canonicalize" in dag
    assert "validate_source_urls >> refresh_repost_signals" in dag
    assert "bool_or(status IN ('complete_nonzero','complete_zero'))" in expiry
    assert "ingestion_tenant_runs" in expiry
    assert '_loc.country == "foreign"' in ingest


def test_ghost_model_uses_reposts_and_natural_closures():
    ghost = (ROOT / "sql/vw_ghost_job_index.sql").read_text()
    mart = (ROOT / "dbt/job_analytics_dbt/models/marts/core/mart_ghost_job_index.sql").read_text()
    assert "expired_reason='natural_cron'" in ghost
    assert "reappearance_count" in ghost and "related_posting_count" in ghost
    assert "mv_repost_events_classified" in ghost
    assert "signal_class='individual_repost_signal'" in ghost
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
    assert "job_postings.description_text IS DISTINCT FROM EXCLUDED.description_text" in ingest
    assert "job_postings.loc_country IS DISTINCT FROM EXCLUDED.loc_country" in ingest
    assert "THEN false" in ingest
    assert ingest.count("is_public=false") >= 3


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
    assert "jp.status = 'raw'" in source
    assert "jp.last_seen_at >= now() - interval '7 days'" in source


if __name__ == "__main__":
    test_publication_predicate_and_total_are_consistent()
    test_ghost_model_uses_reposts_and_natural_closures()
    test_api_exposes_repost_warning_contract()
    test_changed_descriptions_invalidate_enrichment()
    test_v2_experience_is_promoted_with_canonical_labels()
    test_canonicalizer_does_not_hold_schema_lock_during_backfill()
    print("publication/ghost pipeline tests passed")
