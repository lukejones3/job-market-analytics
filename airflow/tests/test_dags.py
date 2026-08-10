from pathlib import Path
from airflow.models import DagBag

def dag_bag():
    return DagBag(dag_folder=str(Path(__file__).parents[1] / "dags"))

def dag(dag_id: str):
    """Return the parsed DAG without requiring an initialized metadata DB."""
    return dag_bag().dags[dag_id]

def test_dags_import_cleanly():
    bag = dag_bag()
    assert bag.import_errors == {}
    assert {"lander_nightly", "lander_resume_embeddings", "lander_shadow_validation",
            "lander_ats_discovery", "lander_career_host_engine"} <= set(bag.dags)

def test_nightly_is_bounded_and_parallelizes_ingest_writers():
    nightly = dag("lander_nightly")
    assert nightly.max_active_runs == 1
    assert nightly.max_active_tasks == 3
    sources = ("greenhouse", "lever", "ashby", "workday", "eightfold", "amazon",
               "smartrecruiters", "workable", "icims", "taleo", "jobvite", "bamboohr")
    for source in sources:
        task = nightly.get_task(f"ingest_{source}")
        assert task.upstream_task_ids == {"ensure_observability_schema"}
        assert "ingest_quality_gate" in task.downstream_task_ids
    workday = nightly.get_task("ingest_workday")
    assert workday.execution_timeout.total_seconds() == 8 * 60 * 60
    assert "ingest_workday_resumable.py" in workday.bash_command

def test_nightly_has_complete_safe_publish_path():
    nightly = dag("lander_nightly")
    assert nightly.dagrun_timeout.total_seconds() == 18 * 60 * 60
    assert "refresh_repost_signals" in nightly.get_task("expire_jobs").downstream_task_ids
    assert "dbt_build" in nightly.get_task("refresh_repost_signals").downstream_task_ids
    assert "canonicalize_opportunities" in nightly.task_ids
    assert "canonicalize_opportunities" in nightly.get_task("deduplicate_sources").downstream_task_ids
    assert "repair_missing_crawl_tenants" in nightly.get_task("canonicalize_opportunities").downstream_task_ids
    assert "dag_run.run_id" in nightly.get_task("expire_jobs").bash_command
    assert "publish_quality_gate" in nightly.get_task("dbt_build").downstream_task_ids
    assert "extract_skills" in nightly.get_task("enrich_jobs").downstream_task_ids
    assert "extract_skills" not in nightly.get_task("annualize_salaries").downstream_task_ids
    assert not any("sync_discovered.sql" in (getattr(t, "bash_command", "") or "") for t in nightly.tasks)

def test_discovery_pipeline_is_ordered():
    discovery = dag("lander_ats_discovery")
    chain = ("discover_ats_tenants", "validate_ats_tenants", "integrate_ats_tenants",
             "ats_discovery_health")
    for task, successor in zip(chain, chain[1:]):
        assert successor in discovery.get_task(task).downstream_task_ids

def test_career_host_engine_integrates_fast_path_before_direct_crawl():
    coverage = dag("lander_career_host_engine")
    assert coverage.max_active_runs == 1
    assert coverage.max_active_tasks == 1
    assert coverage.get_task("seed_employer_universe").upstream_task_ids == {"ensure_career_host_schema"}
    assert coverage.get_task("route_supported_career_ats").upstream_task_ids == {"resolve_official_career_hosts"}
    assert "validate_routed_career_ats" in coverage.get_task("route_supported_career_ats").downstream_task_ids
    assert coverage.get_task("integrate_routed_career_ats").upstream_task_ids == {"validate_routed_career_ats"}
    assert coverage.get_task("crawl_direct_career_hosts").upstream_task_ids == {"integrate_routed_career_ats"}


def test_workday_expansion_ingest_has_priority_over_broad_sources():
    nightly = dag("lander_nightly")
    assert nightly.get_task("ingest_workday").priority_weight > nightly.get_task("ingest_amazon").priority_weight
