#!/usr/bin/env bash
set -euo pipefail

# -------- CONFIG --------
DB_NAME="${DB_NAME:-job_analytics}"
DB_USER="${DB_USER:-lukejones}"
JOB_ID_SAMPLE="${JOB_ID_SAMPLE:-}"
OUTFILE="${OUTFILE:-job_analytics_inspection.txt}"

# -------- HELPERS --------
section() {
  printf "\n\n============================================================\n" >> "$OUTFILE"
  printf "%s\n" "$1" >> "$OUTFILE"
  printf "============================================================\n" >> "$OUTFILE"
}

safe_cmd() {
  {
    echo "\$ $*"
    eval "$@"
  } >> "$OUTFILE" 2>&1 || true
}

have_cmd() {
  command -v "$1" >/dev/null 2>&1
}

RG="grep -RInE"
if have_cmd rg; then
  RG="rg -n --hidden -S"
fi

echo "Writing inspection output to: $OUTFILE"
: > "$OUTFILE"

section "0) ENVIRONMENT"
safe_cmd "pwd"
safe_cmd "printf 'DB_NAME=%s\nDB_USER=%s\nJOB_ID_SAMPLE=%s\n' \"$DB_NAME\" \"$DB_USER\" \"$JOB_ID_SAMPLE\""
safe_cmd "find . -maxdepth 2 -type f | sed 's#^\./##' | sort | head -300"

section "1) LIKELY INGESTION / PIPELINE FILES"
safe_cmd "find . -type f \\( -name '*.py' -o -name '*.sql' -o -name '*.sh' -o -name '*.yaml' -o -name '*.yml' -o -name '*.toml' \\) | sed 's#^\./##' | sort | grep -Ei 'ingest|pipeline|greenhouse|workday|lever|ashby|adzuna|eightfold|amazon|jobs|extract|load|etl|enrich|honesty|score|snapshot|resume|skill'"

section "2) JOB_ID LOGIC"
safe_cmd "$RG 'job_id|source_id|desc_hash|hashlib|md5\\(|sha1\\(|sha256\\(|uuid|uuid4|ON CONFLICT|upsert|insert into job_postings|merge into job_postings' ."

section "3) DEDUP / UPSERT / CONFLICT LOGIC"
safe_cmd "$RG 'ON CONFLICT|DO UPDATE|DO NOTHING|upsert|dedup|duplicate|desc_hash|unique_descriptions|conflict|existing job|skip if exists|already exists' ."

section "4) INGESTION SOURCE-SPECIFIC LOGIC"
safe_cmd "$RG 'greenhouse|workday|lever|ashby|adzuna|eightfold|amazon' ."

section "5) PIPELINE FLOW / ORCHESTRATION"
safe_cmd "$RG 'def main\\(|if __name__ == .__main__.|argparse|click\\.command|typer|subprocess|pipeline_runs|started_at|finished_at|status|run_id|refresh_job_honesty|refresh_skill_demand_monthly_snapshots|job_snapshots' ."

section "6) HONESTY SCORING TRIGGER"
safe_cmd "$RG 'refresh_job_honesty|job_honesty|honesty_score|plausibility_penalty|vague_penalty|scope_penalty|consistency_penalty|eeo_penalty' ."

section "7) SNAPSHOT / LIFECYCLE LOGIC"
safe_cmd "$RG 'job_snapshots|snapshot_date|first_seen|last_seen|days_open|days_seen|lifecycle' ."

section "8) SQL FILES THAT TOUCH CORE TABLES"
safe_cmd "$RG 'job_postings|job_skills|skills|companies|roles|job_honesty|job_snapshots|pipeline_runs|pipeline_errors' --glob '*.sql' ."

section "9) DB SCHEMA QUICK VIEW"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c '\\dt public.*'"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c '\\d+ public.job_postings'"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c '\\d+ public.job_skills'"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c '\\d+ public.job_honesty'"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c '\\d+ public.job_snapshots'"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c '\\d+ public.pipeline_runs'"

section "10) JOB COUNT / DUPLICATE SANITY CHECKS"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT COUNT(*) AS total_jobs FROM job_postings;\""
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT ingestion_source, COUNT(*) AS jobs FROM job_postings GROUP BY 1 ORDER BY 2 DESC;\""
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT COUNT(*) AS null_honesty_rows FROM job_postings jp LEFT JOIN job_honesty jh ON jp.job_id = jh.job_id WHERE jh.job_id IS NULL;\""
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT company_id, role_id, COUNT(*) AS posting_count, COUNT(DISTINCT desc_hash) AS unique_desc FROM job_postings GROUP BY 1,2 HAVING COUNT(*) > 1 ORDER BY posting_count DESC LIMIT 25;\""

section "11) PIPELINE RUN HEALTH"
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT run_id, started_at, finished_at, status, jobs_seen, jobs_inserted, jobs_updated, jobs_skipped, jobs_errored, skills_inserted, errors_count FROM pipeline_runs ORDER BY started_at DESC LIMIT 25;\""
safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT run_id, stage, COUNT(*) AS error_count FROM pipeline_errors GROUP BY 1,2 ORDER BY MAX(created_at) DESC LIMIT 25;\""

section "12) SAMPLE JOB TRACE"
if [[ -z "$JOB_ID_SAMPLE" ]]; then
  JOB_ID_SAMPLE="$(psql -U "$DB_USER" -d "$DB_NAME" -t -A -c "SELECT job_id FROM job_postings ORDER BY ingested_at DESC NULLS LAST LIMIT 1;" 2>/dev/null || true)"
fi

safe_cmd "printf 'JOB_ID_SAMPLE=%s\n' \"$JOB_ID_SAMPLE\""

if [[ -n "${JOB_ID_SAMPLE:-}" ]]; then
  safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT * FROM job_postings WHERE job_id = '$JOB_ID_SAMPLE';\""
  safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT * FROM job_skills WHERE job_id = '$JOB_ID_SAMPLE' ORDER BY confidence DESC NULLS LAST, skill_id;\""
  safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT * FROM job_honesty WHERE job_id = '$JOB_ID_SAMPLE';\""
  safe_cmd "psql -U \"$DB_USER\" -d \"$DB_NAME\" -c \"SELECT * FROM job_snapshots WHERE job_id = '$JOB_ID_SAMPLE' ORDER BY snapshot_date;\""
else
  echo "Could not auto-select a JOB_ID_SAMPLE." >> "$OUTFILE"
fi

section "13) SHOW TOP FILE HITS FOR QUICK READING"
if have_cmd rg; then
  safe_cmd "rg -l --hidden -S 'job_id|desc_hash|ON CONFLICT|refresh_job_honesty|job_snapshots|pipeline_runs|greenhouse|workday|lever|ashby|adzuna|eightfold' . | sort"
else
  safe_cmd "grep -RIl 'job_id\\|desc_hash\\|ON CONFLICT\\|refresh_job_honesty\\|job_snapshots\\|pipeline_runs\\|greenhouse\\|workday\\|lever\\|ashby\\|adzuna\\|eightfold' . | sort"
fi

echo
echo "Done. Open: $OUTFILE"
echo "Then paste the most relevant parts here."
