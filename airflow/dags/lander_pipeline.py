"""Production orchestration for Lander's nightly data platform."""
from datetime import datetime, timedelta
from airflow.sdk import DAG
from airflow.providers.standard.operators.bash import BashOperator

ROOT = "/opt/job-market-analytics"
PYTHON = f"{ROOT}/.venv/bin/python"
DBT = f"{ROOT}/.venv/bin/dbt"
DEFAULT_ARGS = {"owner": "lander", "retries": 1, "retry_delay": timedelta(minutes=10),
                "execution_timeout": timedelta(hours=3)}

def command(task_id: str, body: str, **kwargs) -> BashOperator:
    return BashOperator(task_id=task_id,
        bash_command=f"set -euo pipefail; cd {ROOT}; set -a; source ./.env; set +a; {body}\n",
        append_env=True, **kwargs)

with DAG(dag_id="lander_nightly",
    description="Backup, ingest, enrich, validate, publish, and report Lander data",
    schedule="0 5 * * *", start_date=datetime(2026, 7, 25), catchup=False,
    max_active_runs=1, max_active_tasks=3, default_args=DEFAULT_ARGS,
    dagrun_timeout=timedelta(hours=18),
    tags=["lander", "production"]) as nightly:
    backup = command("verified_backup", "scripts/airflow_backup.sh")
    previous = backup
    for source in ("greenhouse", "lever", "ashby", "workday", "eightfold", "amazon",
                   "smartrecruiters", "workable", "icims", "taleo"):
        ingest = command(f"ingest_{source}",
            f"{PYTHON} python/ingest_jobs.py --apply --source {source}")
        previous >> ingest
        previous = ingest
    ingest_gate = command("ingest_quality_gate",
        f"{PYTHON} python/airflow_quality_gate.py ingest --since '{{{{ dag_run.start_date.isoformat() }}}}'")
    reclassify = command("reclassify_domains",
        f"{PYTHON} python/reclassify_domains.py --apply --since-hours 24")
    blocklist = command("enforce_blocklist", f"{PYTHON} python/enforce_blocklist.py")
    annualize = command("annualize_salaries",
        """psql -v ON_ERROR_STOP=1 -c "UPDATE job_postings SET salary_min_annual=salary_min,
        salary_max_annual=salary_max WHERE salary_period='year' AND salary_max_annual IS NULL
        AND salary_max <= 1000000;" """)
    enrich = command("enrich_jobs",
        f"{PYTHON} python/enrich_job_postings.py --apply --only-missing --no-llm --limit 5000")
    skills = command("extract_skills", f"{PYTHON} python/extract_skills_sql.py --apply")
    embeddings = command("embed_jobs", f"{PYTHON} python/embed_jobs.py")
    experience = command("classify_experience",
        f"{PYTHON} python/classify_exp_level_v2.py --apply")
    honesty = command("refresh_honesty",
        'psql -v ON_ERROR_STOP=1 -c "SELECT refresh_job_honesty();"')
    discover = command("discover_companies",
        f"{PYTHON} python/discover_companies.py --apply --source refresh")
    dedup = command("deduplicate_sources", f"{PYTHON} python/dedup_sources.py --apply")
    expiry = command("expire_jobs",
        f"{PYTHON} python/expire_jobs.py --since '{{{{ dag_run.start_date.isoformat() }}}}'")
    dbt_build = command("dbt_build",
        f"cd {ROOT}/dbt/job_analytics_dbt; {DBT} build --no-use-colors")
    publish_gate = command("publish_quality_gate",
        f"{PYTHON} python/airflow_quality_gate.py publish")
    report = command("morning_report", f"{PYTHON} python/morning_report.py")
    previous >> ingest_gate >> reclassify >> blocklist >> annualize
    annualize >> [enrich, skills]
    enrich >> embeddings >> experience
    [experience, skills] >> honesty
    honesty >> discover >> dedup >> expiry >> dbt_build >> publish_gate >> report

with DAG(dag_id="lander_ats_discovery",
    description="Discover, validate, and activate new ATS tenants",
    schedule="0 12 * * 0", start_date=datetime(2026, 7, 26), catchup=False,
    max_active_runs=1, max_active_tasks=1, default_args=DEFAULT_ARGS,
    dagrun_timeout=timedelta(hours=12), tags=["lander", "discovery"]) as ats_discovery:
    discover_tenants = command("discover_ats_tenants",
        f"{PYTHON} python/discover_ats_aggressive.py --source all --apply")
    validate_tenants = command("validate_ats_tenants",
        f"{PYTHON} python/validate_ats_candidates.py --apply")
    integrate_tenants = command("integrate_ats_tenants",
        f"{PYTHON} python/integrate_ats_candidates.py --apply")
    health_report = command("ats_discovery_health",
        f"{PYTHON} python/ats_discovery_health.py --report")
    discover_tenants >> validate_tenants >> integrate_tenants >> health_report

with DAG(dag_id="lander_resume_embeddings",
    description="Process newly uploaded resumes without overlapping workers",
    schedule="*/5 * * * *", start_date=datetime(2026, 7, 24), catchup=False,
    max_active_runs=1, max_active_tasks=1,
    default_args={**DEFAULT_ARGS, "execution_timeout": timedelta(minutes=20)},
    tags=["lander", "production"]) as resume_embeddings:
    command("embed_resumes", f"{PYTHON} python/embed_resumes.py")
