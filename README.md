# Lander

**Job intelligence built for seekers, not employers.**

🌐 [landerjob.com](https://landerjob.com)

---

Lander pulls job postings directly from company applicant tracking systems — Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Eightfold, Workable, iCIMS, Taleo, and Amazon's hiring API — and layers structured intelligence on top of the raw data. No LinkedIn, no aggregator spam, no scraping grey area.

Every mainstream job platform (LinkedIn, Indeed, ZipRecruiter) makes its money from employers — the seeker is the product. Lander inverts that: free for casual browsers, paid for active job hunters, with the entire product designed around the person looking for work.

**This repository is the backend / data layer** — ingestion, enrichment, classification, the analytics warehouse, and the FastAPI service. The user-facing Next.js frontend lives in a separate repo (`lander`).

---

## What it does

- **Harvests 10 ATS systems nightly** — Greenhouse, Lever, Ashby, Workday, SmartRecruiters, Eightfold, Workable, iCIMS, Taleo, and Amazon
- **Ghost Job Index** — scores every posting on ghost-job probability from time-to-close patterns, re-post frequency, and lifecycle signals, then buckets it fresh / low / medium / high
- **Honesty scores** — a per-posting signal built from freshness, salary disclosure, and posting behavior
- **Semantic resume → job matching** — upload a resume, get top matches by meaning (not keywords) with skill-gap analysis, powered by pgvector + sentence-transformers
- **Salary intelligence** — parses, annualizes, and benchmarks pay across roles, sectors, and companies; tracks salary-transparency coverage by source
- **Company & role intelligence** — scorecards, hiring difficulty, skill demand, and sector benchmarks

## Current scale

Live figures from the production warehouse (US-focused, multi-vertical):

| Metric | Value |
|---|---|
| Active roles (US) | 54,000+ |
| Roles scored & indexed | 82,000+ |
| Companies tracked | 8,000+ |
| Companies hiring now | 2,800+ |
| ATS sources | 10 |
| Salary transparency | ~45% |
| Ghost Index | ~36% flagged high-risk |
| Refresh cadence | Nightly |

Began with data & ML roles; now spans multiple verticals (engineering, finance, marketing, ops) via a shared skill/role taxonomy.

## Stack

- **Backend:** Python 3.12, FastAPI, PostgreSQL 16
- **Data layer:** dbt — ~18 transformation models (staging → core marts), ~60 tables in the `analytics_analytics` schema
- **Ingestion:** custom harvesters for 10 ATS APIs, Airflow-orchestrated with source-ID identity and a company blocklist
- **Enrichment (LLM-free in the cron path):** regex/heuristic salary parsing & annualization, experience inference, role classification, SQL-based skill extraction, location normalization, hiring-contact mapping
- **ML:** `all-MiniLM-L6-v2` (384-dim) sentence embeddings with per-company boilerplate stripping; pgvector + HNSW indexes for semantic resume→job matching, live in production
- **Frontend:** Next.js on Vercel (separate `lander` repo)
- **Infrastructure:** DigitalOcean droplet, nginx + Let's Encrypt, Cloudflare edge, `jma-api.service` (systemd) on port 8000
- **Billing & email:** Stripe live subscriptions, Resend transactional email

## Repository layout

| Path | Contents |
|---|---|
| `python/` | Ingestion harvesters, enrichment, classifiers, discovery, the FastAPI app (`api.py`), and the resume matcher (`python/resume/`) |
| `dbt/job_analytics_dbt/` | dbt project — staging models + core marts (ghost index, honesty, salary/sector benchmarks, scorecards, skill demand) |
| `sql/` | Standalone SQL: salary annualization, honesty refresh, dedup, discovery sync |
| `models/` | ML model artifacts and experiment status notes |
| `scripts/` | Operational and one-off maintenance scripts |
| `eval/` | Classifier / parser evaluation harnesses |

## Daily pipeline (UTC)

| Time | Step |
|---|---|
| 05:00 | `pg_dump` backup + prune backups older than 7 days |
| 05:00 onward | Airflow serially ingests 10 ATS sources, then gates enrichment and publishing |
| 06:15 | Domain reclassification (last 24h) |
| 06:20 | Enforce company blocklist |
| 06:30 | Annualize salaries + enrich (`--no-llm`, regex/heuristics) + SQL skill extraction |
| 06:45 | Embed new jobs |
| 06:55 | Experience-level v2 classifier |
| 07:20 | Refresh honesty scores + company discovery |
| 07:30 | Cross-source dedup |
| 07:40 | Expire stale jobs |
| 08:00 | `dbt run` — ~18 models |
| 08:30 | Morning report (email via Resend) |
| every 5 min | Embed new resumes |

The daily cron path is intentionally **LLM-free** — classification runs on regex, heuristics, and a cached label store.
Airflow is the production orchestrator. Its weekly discovery DAG continuously
expands the tenant universe before validation and activation.

## API

The FastAPI service (`python/api.py`) exposes a versioned `/v1` REST API behind API-key auth and rate limiting. Highlights:

- `GET /v1/market/overview` · `/market/roles` · `/market/skills` · `/market/sectors` · `/market/ghost-index`
- `GET /v1/companies` · `/companies/{slug}` (+ `/roles`, `/skills`)
- `GET /v1/roles` · `/roles/{job_id}`
- `POST /v1/resume/upload` — resume parse + semantic match
- Stripe checkout / portal / webhook + magic-link auth flow

Interactive docs at `/docs` when the service is running.

## Tests

Salary-parser regression fixtures live in `python/test_salary_parser.py`:

```
python python/test_salary_parser.py
```

CI runs them automatically (`.github/workflows/test-salary-parser.yml`) on any push or PR touching `enrich_job_postings.py` or the test file. Add a fixture by appending a `(name, text, exp_min, exp_max, exp_period)` tuple to the `TESTS` list.

## Status

Public freemium launch: **May 2, 2026.**

Built solo by [Luke Jones](https://linkedin.com/in/luke-j-78a02121b) — a finance major who learned Python, SQL, and infrastructure from scratch to build it. The product is a deliberate correction to a job-search experience that's optimized for everyone except the person looking for work.

## Contact

[jones31luke@gmail.com](mailto:jones31luke@gmail.com)
