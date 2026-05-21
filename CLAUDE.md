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

## Daily cron pipeline (UTC)
- 05:00  pg_dump backup + prune backups older than 7 days
- 06:00  ingest — 9 ATS sources launch simultaneously (greenhouse, lever,
         ashby, workday, eightfold, amazon, smartrecruiters, workable, icims)
- 06:15  reclassify_domains.py (--since-hours 24)
- 06:20  enforce_blocklist.py
- 06:30  annualize salaries (SQL) + enrich_job_postings.py (regex/heuristics,
         --no-llm, limit 5000) + extract_skills_sql.py
- 06:45  embed_jobs.py — embeddings for new jobs
- 06:55  classify_exp_level_v2.py --apply — writes experience_level_v2
- 07:20  refresh_job_honesty() + discover_companies.py
- 07:30  dedup_sources.py
- 07:40  expire_jobs.py
- 07:50  sync_discovered.sql
- 08:00  dbt run — 18 models
- 08:30  morning_report.py
- every 5 min: embed_resumes.py

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
