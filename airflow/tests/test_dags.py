from pathlib import Path
from airflow.models import DagBag

def dag_bag():
    return DagBag(dag_folder=str(Path(__file__).parents[1] / "dags"))

def test_dags_import_cleanly():
    bag = dag_bag()
    assert bag.import_errors == {}
    assert {"lander_nightly", "lander_resume_embeddings", "lander_shadow_validation"} <= set(bag.dags)

def test_nightly_is_bounded_and_serializes_ingest_writers():
    dag = dag_bag().get_dag("lander_nightly")
    assert dag.max_active_runs == 1
    assert len(dag.tasks) == 26
    sources = ("greenhouse", "lever", "ashby", "workday", "eightfold", "amazon",
               "smartrecruiters", "workable", "icims")
    for source, successor in zip(sources, sources[1:]):
        assert f"ingest_{successor}" in dag.get_task(f"ingest_{source}").downstream_task_ids
