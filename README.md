# Job Market Analytics

An independent data pipeline and market intelligence platform tracking real-time hiring trends across data, analytics, and machine learning roles.

The system ingests job postings nightly from Greenhouse and Lever ATS APIs across 165+ actively hiring companies, enriches each posting through a multi-pass NLP pipeline, and produces structured labor market intelligence covering skill demand, salary signals, and job posting quality.

**Q1 2026 Data & Analytics Job Market Report** — published April 2026. Based on 950 fully enriched job postings across 159 companies.

---

## What This Builds

A longitudinal dataset of data and analytics job postings with:

- Skill extraction against a curated allowlist of 90+ canonical skills with alias matching
- Salary parsing from structured and unstructured text fields
- Experience level inference from title patterns and years-of-experience language
- A proprietary **Honesty Score** (0–100) measuring salary transparency, scope realism, and skill-to-level plausibility for each posting
- Company-level transparency benchmarking across salary disclosure rates and requirement realism

---

## Architecture
```
Greenhouse API ──┐
Lever API ───────┼──▶ ingest_jobs.py ──▶ PostgreSQL ──▶ enrich_job_postings.py ──▶ dbt ──▶ Power BI
Adzuna API ──────┘                                            │
                                                              ▼
                                                    refresh_job_honesty()
```

**Ingestion** — `python/ingest_jobs.py` queries all tracked company job boards nightly, deduplicates via content hash, and inserts net-new postings. Supports Greenhouse, Lever, and Adzuna sources.

**Enrichment** — `python/enrich_job_postings.py` runs a two-pass NLP pipeline extracting skills, parsing salary, inferring experience level, and classifying workplace type.

**Honesty Scoring** — `sql/job_honesty.sql` defines a PostgreSQL function scoring each posting across five penalty dimensions: salary transparency, scope realism, skill-to-level plausibility, internal consistency, and EEO boilerplate dominance.

**Transformation** — dbt models in `dbt/job_analytics_dbt/` produce staging views, fact tables, dimension tables, and analytics marts.

**Infrastructure** — DigitalOcean Ubuntu server with nightly cron pipeline, automated daily backups, and 7-day retention.

---

## Data Model

**Core tables:** `job_postings`, `companies`, `roles`, `skills`, `job_skills`, `locations`

**Pipeline observability:** `pipeline_runs`, `pipeline_errors`

**Intelligence layer:** `job_honesty`, `discovered_companies`, `skill_candidates`

**dbt marts:** `mart_skill_demand`, `mart_salary_benchmarks`, `mart_market_coverage`

---

## Data Tiers

| Tier | Source | Description | Used For |
|------|--------|-------------|----------|
| 1 | Greenhouse, Lever | Full descriptions, skill extraction, honesty scoring | All analytics |
| 2 | Adzuna | Structured salary, market breadth | Salary benchmarks, market coverage |
| 3 | Manual captures | LinkedIn snapshots, no posted date | Reference only |

---

## Key Findings (Q1 2026)

- Python appears in 64.8% of tech company data job postings — ahead of SQL at 55.6%
- Machine Learning skills carry a +10.2% salary premium vs dataset baseline
- 48.3% of Tier 1 postings disclose no salary information
- Large Language Models appear in 23.7% of postings with a +13.4% salary premium
- Power BI correlates with a -28.4% salary penalty vs baseline
- Airbnb and Waymo hire 90% and 85% senior respectively — almost no junior pipeline

---

## Tech Stack

- **Python** — ingestion, enrichment, discovery scripts
- **PostgreSQL 16** — primary data store (DigitalOcean, Ubuntu 24.04)
- **dbt** — transformation layer (13 models)
- **SQL** — honesty scoring functions, analytics queries
- **Power BI** — reporting and visualization
- **Git / GitHub** — version control

---

## Nightly Pipeline (UTC)
```
06:00  pg_dump backup + 7-day retention cleanup
07:00  ingest_jobs.py --apply --discover
07:30  refresh_job_honesty()
08:00  discover_companies.py --apply --source refresh
```

---

## Repository Structure
```
python/
  ingest_jobs.py              # Multi-source job ingestion
  enrich_job_postings.py      # NLP enrichment pipeline
  discover_companies.py       # Company discovery and refresh
  discover_skills.py          # Skill candidate discovery via co-occurrence
  morning_check.py            # Daily pipeline health check

sql/
  job_honesty.sql             # Honesty scoring function and schema
  skill_intel.sql             # Skill intelligence queries

dbt/job_analytics_dbt/
  models/staging/             # Source-aligned staging views
  models/marts/core/          # Fact tables, dimensions, analytics marts
```

---

## License

Copyright (c) 2026 Luke Jones. All rights reserved.

This source code is made available for viewing and educational purposes only. Commercial use, redistribution, or derivative works require explicit written permission from the copyright holder.

Contact: jones31luke@gmail.com

---

*Nightly ingestion active. Dataset grows automatically. Q2 2026 report forthcoming.*
