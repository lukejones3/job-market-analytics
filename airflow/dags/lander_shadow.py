"""Read-only deployment checks used before cron cutover."""
from datetime import timedelta

import pendulum
from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator

ROOT = "/opt/job-market-analytics"
PYTHON = f"{ROOT}/.venv/bin/python"
with DAG(dag_id="lander_shadow_validation",
    description="Read-only production environment and database validation", schedule=None,
    start_date=pendulum.datetime(2026, 7, 24, tz="America/Chicago"), catchup=False, max_active_runs=1,
    default_args={"owner": "lander", "retries": 0,
                  "execution_timeout": timedelta(minutes=10)},
    tags=["lander", "validation"]) as shadow:
    environment = BashOperator(task_id="environment", bash_command=(
        f"set -euo pipefail; cd {ROOT}; test -r .env; test -x .venv/bin/python; "
        "test -r python/ingest_jobs.py; test -r dbt/job_analytics_dbt/dbt_project.yml"))
    database = BashOperator(task_id="database", bash_command=(
        f"set -euo pipefail; cd {ROOT}; set -a; source ./.env; set +a; "
        f"{PYTHON} python/airflow_quality_gate.py shadow"))
    environment >> database
