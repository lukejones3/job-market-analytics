# CLAUDE.md — job-market-analytics

Context for Claude Code. Durable facts only. Update when structure changes.

## What this is
Python data pipeline + FastAPI + dbt powering Lander (landerjob.com), a
multi-vertical job-market analytics platform. This repo is the backend/data
layer. The Next.js frontend is a separate repo (`lander`).

## Infrastructure
- Droplet: SSH as `root@208.68.38.249`
  - ALWAYS use `root@`. `lukejones@` prompts for a password — do not use it.
- Repo on droplet: `/opt/job-market-analytics`
- DB credentials: sourced from `/opt/job-market-analytics/.env`
  (PGUSER / PGPASSWORD / PGHOST / PGDATABASE / PGPORT)
- Python venv: `/opt/job-market-analytics/.venv/bin/python` (Python 3.12.3)
- dbt binary: `/opt/job-market-analytics/.venv/bin/dbt`
- FastAPI service: `jma-api.service` (systemd, running), port 8000
- Postgres 16, database `job_analytics`, DB user `lukejones`, host `127.0.0.1`.
- dbt analytics schema: `analytics_analytics`. Marts are fully schema-qualified,
  e.g. `analytics_analytics.mart_ghost_job_index`.

## To run a psql query on the droplet
ssh root@208.68.38.249 "cd /opt/job-market-analytics && set -a && . .env && set +a && psql -c \"<SQL>\""

## Production orchestration
- Airflow 3.3.2 owns the pipeline. The historical cron sequence is retired;
  do not re-enable independent ingestion, enrichment, expiry or publication cron jobs.
- DAG source: `airflow/dags/lander_pipeline.py`; scheduler environment:
  `/etc/airflow/airflow.env`; runtime: `/opt/airflow/venv/bin/airflow`.
- `lander_nightly` starts at midnight America/Chicago, with three active
  tasks and an 18-hour run window. Backup and schema maintenance precede 12
  ATS ingests, optional coverage feeds, ingestion gate, enrichment, stored
  embedding reuse, quality repairs, tenant-safe expiry, source URL validation,
  dbt, publication gate, atomic publication, SEO refresh and notifications.
- Workday uses durable tenant batches through `ingest_workday_resumable.py`.
  SmartRecruiters uses the same runner with `--source smartrecruiters`, four
  concurrent company checkpoints, individual HTTP sessions and bounded crawls.
  Retries skip only clean, committed tenants for the same orchestration ID.
- A completed checkpoint manifest proves every planned tenant has an outcome.
  Provider failures stay visible; failed/partial tenants cannot authorize expiry.
  Database/program failures and unfinished sweeps block publication.
- Enrichment is `--only-missing --no-llm --limit 40000`; job embeddings are
  generated only for rows with missing embeddings. Resume embeddings run every
  five minutes through `lander_resume_embeddings`.
- `lander_daily_brief` runs independently at 08:30 America/Chicago. The nightly
  report also runs after all stages finish, including failures. Central-date
  delivery ledgers and provider idempotency prevent duplicate daily emails.
  A metrics outage produces a warning email rather than suppressing delivery.
- Verify recovery with `publication_runs`, the public `/v1/public/market`
  response, and the report delivery ledger. A healthy API process or passing
  unit tests alone do not establish that ingestion/publication completed.
- October 30 recovery published 54,325 jobs at 2026-10-31 00:37 UTC (October 30
  19:37 Central), including 12,169 activated listings. Isolated Workday tenant
  failures remained visible; the full DAG was deliberately not marked green.

## ML infrastructure (already built — do not rebuild)
- sentence-transformers model: all-MiniLM-L6-v2, 384-dim. Loaded globally in
  the FastAPI app at startup. Reuse this model — do NOT add new transformer deps.
- pgvector with HNSW indexes (vector_cosine_ops) on job and resume embeddings.
- Semantic resume→job matching is live in production. Do not touch the matcher,
  embed_jobs.py, embed_resumes.py, or the FastAPI matching endpoint without an
  explicit instruction.

## CRITICAL: reuse stored embeddings
Job embeddings already exist in the `embedding` column of job_postings.
If a task needs embeddings of the boilerplate-stripped job text, SELECT them
from that column. Do NOT call model.encode() to regenerate them — re-encoding
~25k jobs wastes ~25 min of CPU. Only encode fresh when the input text is
genuinely new (a text variant that was never embedded).

## Boilerplate stripping
embed_jobs.py strips per-company boilerplate before embedding: for companies
with 3+ jobs, text chunks appearing in 40%+ of that company's JDs are removed.
This is why embeddings cluster by role, not company. Relevant any time job
description text is used as model input.

## Pipeline rule: no LLM in the cron path
enrich_job_postings.py runs with --no-llm. Skill extraction is SQL-based.
The experience-level v2 classifier has no LLM fallback. Keep the daily pipeline
LLM-free unless explicitly instructed otherwise.

## Technical gotchas
- Patch files via heredoc with a QUOTED delimiter:
  `cat > /tmp/file.py << 'EOF'` ... `EOF`  (quoted EOF prevents shell expansion)
- Use bytes-mode file I/O where Unicode (curly quotes, en/em dashes) is a risk.
- Anchor edits on unique nearby strings via content.find(), not line numbers.
- Python 3.12 on the droplet.

## How to work in this repo (prompt/workflow preferences)
- Use phased prompts with explicit gates. Report between phases. Do NOT chain
  straight through multiple phases without stopping.
- Show migration SQL before running it. Wait for approval, then apply.
- Flag newly discovered issues separately — do not scope-creep mid-task.
- Back up any file before modifying it.
- New model artifacts go in `/opt/job-market-analytics/models/`.
- For scoping/strategy/cost questions: give direct answers — numbers, options,
  tradeoffs. No "my take" or priority commentary.

## Current state / parked work
- Experience-level classifier (experience_level_v2 column): PARKED.
  Works well for title-certain jobs; ambiguous-title individual-contributor
  roles (e.g. Account Executive, Financial Analyst) lean too senior because
  there is no trained `mid` class. Full status + the three candidate fixes:
  see `models/exp_level_STATUS.md`.
- The old `experience_level` column and `infer_experience_level()` remain the
  production source of truth — untouched. v2 is not yet consumed by anything.
- The 06:55 classify_exp_level_v2 cron is left running; it writes only to the
  unused v2 column.

## Out of scope unless explicitly asked
- Refactors, vulnerability scans, code cleanup — these are separate planned
  sessions, never bundled into a feature task.
