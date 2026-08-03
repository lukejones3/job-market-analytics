from pathlib import Path
from airflow.models import DagBag

def dag_bag():
    return DagBag(dag_folder=str(Path(__file__).parents[1] / "dags"))

def test_dags_import_cleanly():
    bag = dag_bag()
    assert bag.import_errors == {}
    assert {"lander_nightly", "lander_resume_embeddings", "lander_shadow_validation",
            "lander_ats_discovery"} <= set(bag.dags)

def test_nightly_is_bounded_and_serializes_ingest_writers():
    dag = dag_bag().get_dag("lander_nightly")
    assert dag.max_active_runs == 1
    assert len(dag.tasks) == 26
    sources = ("greenhouse", "lever", "ashby", "workday", "eightfold", "amazon",
               "smartrecruiters", "workable", "icims", "taleo")
    for source, successor in zip(sources, sources[1:]):
        assert f"ingest_{successor}" in dag.get_task(f"ingest_{source}").downstream_task_ids

def test_nightly_has_complete_safe_publish_path():
    dag = dag_bag().get_dag("lander_nightly")
    assert dag.dagrun_timeout.total_seconds() == 18 * 60 * 60
    assert "ingest_quality_gate" in dag.get_task("ingest_taleo").downstream_task_ids
    assert "dbt_build" in dag.get_task("expire_jobs").downstream_task_ids
    assert "canonicalize_opportunities" in dag.task_ids
    assert "canonicalize_opportunities" in dag.get_task("deduplicate_sources").downstream_task_ids
    assert "publish_quality_gate" in dag.get_task("dbt_build").downstream_task_ids
    assert not any("sync_discovered.sql" in (getattr(t, "bash_command", "") or "") for t in dag.tasks)

def test_discovery_pipeline_is_ordered():
    dag = dag_bag().get_dag("lander_ats_discovery")
    chain = ("discover_ats_tenants", "validate_ats_tenants", "integrate_ats_tenants",
             "ats_discovery_health")
    for task, successor in zip(chain, chain[1:]):
        assert successor in dag.get_task(task).downstream_task_ids
