"""Durable scheduler for private Human Repo maintenance via a narrow broker.

The DAG owns timing, dependencies, retries and run state. Commands and secrets
remain under the humanrepo service identity. No memory text enters XCom/logs.
"""
from datetime import timedelta

import pendulum
from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator

ROOT = "/opt/job-market-analytics"
CLIENT = f"{ROOT}/.venv/bin/python {ROOT}/airflow/human_repo_memory_client.py"
DEFAULT_ARGS = {"owner": "metronome", "retries": 1,
                "retry_delay": timedelta(minutes=10),
                "execution_timeout": timedelta(hours=2)}
START = pendulum.datetime(2026, 10, 31, tz="UTC")


def build_pipeline(dag_id: str, project: str, schedule: str, stages: tuple[str, ...]) -> DAG:
    with DAG(dag_id=dag_id, description=f"Human Repo {project} memory maintenance",
             schedule=schedule, start_date=START, catchup=False,
             max_active_runs=1, max_active_tasks=1, default_args=DEFAULT_ARGS,
             dagrun_timeout=timedelta(hours=3),
             tags=["human-repo", "metronome", "memory-authority"]) as dag:
        previous = None
        for stage in stages:
            task = BashOperator(
                task_id=stage,
                bash_command=(
                    f"{CLIENT} {project} {stage} "
                    "'{{ dag_run.run_id }}' '{{ ti.try_number }}'\n"
                ),
                append_env=True,
            )
            if previous is not None:
                previous >> task
            previous = task
    return dag


GENERAL_STAGES = (
    "admission", "bridge_history", "protect_metadata", "build_spans",
    "build_intentions", "build_graph", "semantic_index", "live_days",
    "validate", "continuity",
)
PRODUCT_STAGES = (
    "sync", "ingest", "index_raw", "materialize", "index_derived",
    "validate", "status",
)

human_repo_general_incremental = build_pipeline(
    "human_repo_general_incremental", "general", "*/30 * * * *", GENERAL_STAGES)
human_repo_product_lander = build_pipeline(
    "human_repo_product_lander", "lander", "37 */6 * * *", PRODUCT_STAGES)
human_repo_product_wyloc = build_pipeline(
    "human_repo_product_wyloc", "wyloc", "42 */6 * * *", PRODUCT_STAGES)
human_repo_product_metronome = build_pipeline(
    "human_repo_product_metronome", "metronome-arthritis", "47 */6 * * *", PRODUCT_STAGES)
