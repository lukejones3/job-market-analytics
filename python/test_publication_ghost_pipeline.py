"""Static regression checks for publication and ghost-pipeline invariants."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_publication_predicate_and_total_are_consistent():
    api = (ROOT / "python/api.py").read_text()
    gate = (ROOT / "python/airflow_quality_gate.py").read_text()
    ingest = (ROOT / "python/ingest_jobs.py").read_text()
    assert api.count("COALESCE(jp.loc_country, 'unknown') IN ('US', 'unknown')") >= 4
    assert '"total":        total' in api
    assert "loc_country,'unknown') IN ('US','unknown')" in gate
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
                  "experience_level_v2=NULL", "DELETE FROM job_skills"):
        assert field in ingest


def test_v2_experience_is_promoted_with_canonical_labels():
    classifier = (ROOT / "python/classify_exp_level_v2.py").read_text()
    assert 'canonical = {"junior": "entry", "lead": "senior"}' in classifier
    assert "experience_level_v2 = %s, experience_level = %s" in classifier


if __name__ == "__main__":
    test_publication_predicate_and_total_are_consistent()
    test_ghost_model_uses_reposts_and_natural_closures()
    test_api_exposes_repost_warning_contract()
    test_changed_descriptions_invalidate_enrichment()
    test_v2_experience_is_promoted_with_canonical_labels()
    print("publication/ghost pipeline tests passed")
