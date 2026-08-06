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
    observability_schema = command("ensure_observability_schema",
        "psql -v ON_ERROR_STOP=1 -f sql/ingestion_observability.sql "
        "-f sql/domain_classification_observability.sql "
        "-f sql/ingestion_publication_funnel.sql "
        "-f sql/publication_boundary.sql "
        "-f sql/feed_performance_indexes.sql")
    backup >> observability_schema
    ingests = []
    for source in ("greenhouse", "lever", "ashby", "workday", "eightfold", "amazon",
                   "smartrecruiters", "workable", "icims", "taleo", "jobvite", "bamboohr"):
        # Taleo's formerly configured enterprise tenants have migrated away
        # from their legacy *.taleo.net hosts. Keep the source observable and
        # discovery-ready, but do not block all healthy ingestion while there
        # are no validated live tenants.
        empty_flag = " --accept-empty" if source in ("jobvite", "bamboohr", "taleo") else ""
        ingest = command(f"ingest_{source}",
            f"{PYTHON} python/ingest_jobs.py --apply --source {source} "
            # A DagRun's start_date changes when a failed run is cleared. Its
            # run_id does not, so use the latter as the durable crawl identity.
            f"--orchestration-run-id '{{{{ dag_run.run_id }}}}'{empty_flag}")
        observability_schema >> ingest
        ingests.append(ingest)
    ingest_gate = command("ingest_quality_gate",
        f"{PYTHON} python/airflow_quality_gate.py ingest --since '{{{{ dag_run.run_id }}}}'",
        trigger_rule="all_done")
    scope_report = command("role_scope_report",
        f"{PYTHON} python/role_scope_report.py", trigger_rule="all_done")
    scope_backfill = command("backfill_missing_role_scope",
        f"{PYTHON} python/backfill_role_scope.py --apply --only-missing")
    reclassify = command("reclassify_domains",
        f"{PYTHON} python/reclassify_domains.py --apply --since-hours 24")
    blocklist = command("enforce_blocklist", f"{PYTHON} python/enforce_blocklist.py")
    annualize = command("annualize_salaries",
        """psql -v ON_ERROR_STOP=1 -c "UPDATE job_postings SET salary_min_annual=salary_min,
        salary_max_annual=salary_max WHERE salary_period='year' AND salary_max_annual IS NULL
        AND salary_max <= 1000000;" """)
    extract_salaries = command("extract_salaries",
        f"{PYTHON} python/extract_salaries.py --apply --since-hours 72")
    enrich = command("enrich_jobs",
        f"{PYTHON} python/enrich_job_postings.py --apply --only-missing --no-llm --limit 5000")
    # A 72-hour overlap catches retries without rescanning the same 15k
    # legitimately skill-less descriptions every night.
    skills = command("extract_skills",
        f"{PYTHON} python/extract_skills_sql.py --apply --since-hours 72")
    embeddings = command("embed_jobs", f"{PYTHON} python/embed_jobs.py")
    experience = command("classify_experience",
        f"{PYTHON} python/classify_exp_level_v2.py --apply")
    honesty = command("refresh_honesty",
        'psql -v ON_ERROR_STOP=1 -c "SELECT refresh_job_honesty();"')
    discover = command("discover_companies",
        f"{PYTHON} python/discover_companies.py --apply --source refresh")
    dedup = command("deduplicate_sources", f"{PYTHON} python/dedup_sources.py --apply")
    canonicalize = command("canonicalize_opportunities",
        f"{PYTHON} python/canonicalize_opportunities.py --apply")
    expiry = command("expire_jobs",
        f"{PYTHON} python/expire_jobs.py --since '{{{{ dag_run.start_date.isoformat() }}}}'")
    dbt_build = command("dbt_build",
        f"cd {ROOT}/dbt/job_analytics_dbt; "
        f"{DBT} build --profiles-dir {ROOT}/dbt/job_analytics_dbt "
        f"--log-path {ROOT}/logs/dbt --target-path /opt/airflow/dbt-target "
        "--no-use-colors")
    publish_gate = command("publish_quality_gate",
        f"{PYTHON} python/airflow_quality_gate.py publish")
    publish_snapshot = command("publish_snapshot",
        f"{PYTHON} python/publish_snapshot.py --apply")
    refresh_seo_index = command("refresh_seo_collection_index",
        f"{PYTHON} python/refresh_seo_collection_index.py")
    notify_google_indexing = command("notify_google_indexing",
        f"{PYTHON} python/notify_google_indexing.py")
    report = command("morning_report", f"{PYTHON} python/morning_report.py")
    funnel_report = command("ingestion_funnel_report",
        f"{PYTHON} python/ingestion_funnel_report.py")
    ingests >> ingest_gate >> scope_backfill >> reclassify >> blocklist >> annualize >> extract_salaries
    ingests >> scope_report
    # Domain classification must commit before domain-aware skill extraction.
    # The oldest-first enrichment batch drains backlog without starving rows.
    extract_salaries >> enrich >> skills
    enrich >> embeddings >> experience
    [experience, skills] >> honesty
    honesty >> discover >> dedup >> canonicalize >> expiry >> dbt_build >> publish_gate >> publish_snapshot
    publish_snapshot >> refresh_seo_index >> notify_google_indexing >> [report, funnel_report]

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
    discover_workday_crawl = command("discover_workday_commoncrawl",
        f"{PYTHON} python/discover_workday_tenants.py --source commoncrawl --apply")
    validate_workday_crawl = command("validate_workday_commoncrawl",
        f"{PYTHON} python/validate_workday_tenants.py --apply")
    integrate_workday_crawl = command("integrate_workday_commoncrawl",
        f"{PYTHON} python/validate_workday_tenants.py --integrate")
    discover_tenants >> validate_tenants >> integrate_tenants
    discover_workday_crawl >> validate_workday_crawl >> integrate_workday_crawl
    [integrate_tenants, integrate_workday_crawl] >> health_report

with DAG(dag_id="lander_ats_discovery_daily",
    description="Daily broad-domain ATS discovery and stale-candidate recovery",
    schedule="0 11 * * *", start_date=datetime(2026, 8, 5), catchup=False,
    max_active_runs=1, max_active_tasks=1, default_args=DEFAULT_ARGS,
    dagrun_timeout=timedelta(hours=4), tags=["lander", "discovery"]) as ats_discovery_daily:
    discover_daily = command("discover_serper_daily",
        f"{PYTHON} python/discover_ats_aggressive.py --source serper --apply")
    validate_daily = command("validate_daily_candidates",
        f"{PYTHON} python/validate_ats_candidates.py --apply --limit 500 --workers 8 --retry-stale-hours 168")
    integrate_daily = command("integrate_daily_candidates",
        f"{PYTHON} python/integrate_ats_candidates.py --apply")
    report_daily = command("ats_discovery_health_daily",
        f"{PYTHON} python/ats_discovery_health.py --report")
    discover_daily >> validate_daily >> integrate_daily >> report_daily

with DAG(dag_id="lander_resume_embeddings",
    description="Process newly uploaded resumes without overlapping workers",
    schedule="*/5 * * * *", start_date=datetime(2026, 7, 24), catchup=False,
    max_active_runs=1, max_active_tasks=1,
    default_args={**DEFAULT_ARGS, "execution_timeout": timedelta(minutes=20)},
    tags=["lander", "production"]) as resume_embeddings:
    command("embed_resumes", f"{PYTHON} python/embed_resumes.py")
