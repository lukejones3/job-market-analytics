# Job Market Analytics

An independent, production-grade labor market intelligence platform tracking data, analytics, and ML hiring across 1,000+ companies nightly.

The system ingests job postings from Greenhouse, Lever, Ashby, Workday, Amazon, and Eightfold — enriches each posting through a custom NLP pipeline — and scores every posting on proprietary metrics measuring salary transparency, role complexity, hiring difficulty, and posting quality.

**[April 2026 Inaugural Report →](https://solstice-stock-6c6.notion.site/APRIL-2026-DATA-ANALYTICS-JOB-MARKET-REPORT-INAUGURAL-EDITION-33c61d18db3480348780dd2c43bbc0d5)**

---

## Current Scale

| Metric | Value |
|---|---|
| Tier 1 job postings (fully enriched) | 6,100+ |
| Active Tier 1 roles (live on ATS) | 4,500+ |
| ATS sources | 6 (Greenhouse, Lever, Ashby, Workday, Amazon, Eightfold) |
| Companies actively hiring | 1,000+ |
| Total companies in monitoring pool | 2,700+ |
| Canonical skills tracked | 114 (with 239 aliases) |
| Sectors classified | 28 |
| Salary transparency coverage | 55% |
| Dataset updated | Nightly (parallelized, ~50 min runtime) |

---

## What Makes This Different

**6-source ingestion** — Greenhouse (1,800+ companies) and Lever (629+ companies) cover the venture-backed tech market. Workday covers enterprise companies like Netflix, Capital One, Boeing, Citi, Allstate, and Warner Bros. Ashby covers modern AI-native startups like Perplexity, Ramp, and Cohere. Amazon and Eightfold fill the gaps. No other independent dataset spans all six.

**Honesty Score** — every posting is scored 0-100 across 5 penalty dimensions: salary transparency, scope realism, skill-to-level plausibility, internal consistency, and EEO boilerplate dominance. No other job market dataset publishes this.

**Hiring Difficulty Score** — composite metric combining skill rarity, role complexity vs sector peers, salary competitiveness, and posting opacity. Predicts which roles will be hardest to fill before they go stale. Normalized 0-100 across the dataset.

**Ghost Job Index** — sigmoid probability model scoring each active posting's likelihood of being a ghost job, based on days open relative to sector median. Available for Greenhouse, Lever, and Ashby sources.

**Compensation Competitiveness Index** — company average max salary vs sector median by experience level. Identifies which companies pay above or below market for specific role types.

**Job lifecycle tracking** — nightly expiry system marks roles that disappear from ATS boards. Cross-source dedup reactivates previously expired jobs when re-confirmed live. Enables posting longevity analysis and role velocity trending as longitudinal data accumulates.

**Salary parsing engine** — custom 30+ pattern regex system handling European separators, HTML entity dashes, OTE pairs, zone-based comp, hourly rates, and truncated numbers. 55% salary coverage on Tier 1 postings.

**Cross-source deduplication** — the same role posted across multiple ATS platforms is ingested once. Zero confirmed duplicates across all 6 sources.

---

## Proprietary Metrics

| Metric | Description | Status |
|---|---|---|
| Honesty Score | 0-100 posting quality score across 5 dimensions | Live |
| Hiring Difficulty Score | Composite fill-difficulty predictor, normalized 0-100 | Live |
| Ghost Job Index | Sigmoid probability a posting is no longer actively hiring | Live |
| Compensation Competitiveness Index | % above/below sector median by experience level | Live |
| Skill Gap Score | % niche skills (<5% prevalence) per company | Live |
| Job Description Complexity Score | Required skills vs sector peers | Live |
| Salary Premium by Skill | % compensation lift per canonical skill | Live |
| Role Velocity | Hiring acceleration/deceleration | Building (needs 30d data) |
| Posting Longevity Index | Avg days open before expiry | Building (needs 30d data) |

---

## Key Findings (April 2026)

**Salary premiums by skill:**
- Reinforcement Learning: +33.9% ($257K avg max)
- PyTorch: +19.2% ($229K avg max)
- Kubernetes: +17.8% ($227K avg max)
- Large Language Models: +16.5% ($224K avg max)
- Causal Inference: +13.9% ($219K avg max)

**Sector transparency:**
- HR Tech: 100% | Defense/GovTech: 80% | Autonomous/Robotics: 73%
- Consulting: 14% (worst) | AI/ML: 56% | SaaS/Enterprise: 49%

**Hiring difficulty:**
- Hardest to fill: Gartner (100), Deel (95), Spotify (92), Cohere (88)
- Easiest to fill: Definitive Healthcare (0), Brex (3), Discord (3), Chime (3)

**Sector pay (median max salary):**
- AI/ML: $247K | Gaming: $220K | Autonomous/Robotics: $204K
- Logistics: $137K | Space Tech: $120K | Restaurant Tech: $138K

---

## Stack

- **Ingestion** — Python 3.9, 6-source ATS pipeline, parallelized nightly cron (all sources simultaneously, ~50 min)
- **Discovery** — Serper.dev Google dorking across 40+ query angles targeting role titles, tech stack signals, industry verticals, and funding stage; 2,700+ companies discovered and validated
- **Storage** — PostgreSQL 16 on DigitalOcean (4GB RAM, 2 vCPU), pg_dump backups with 7-day retention
- **Enrichment** — custom NLP pipeline: 114-skill extraction with 239 aliases, 30+ salary format parser, experience level inference, workplace type classification
- **Scoring** — Honesty Score via PostgreSQL stored function; Hiring Difficulty Score and Ghost Job Index via SQL views; all refreshed nightly
- **Transformation** — dbt 1.11 with 18 models: 5 staging views, 4 dimension/fact tables, 9 analytics marts
- **Lifecycle** — nightly expiry system with ingest sanity check; cross-source dedup reactivates previously expired jobs when re-confirmed live

---

## Pipeline Architecture

Greenhouse (1,800+) / Lever (629+) / Ashby (69) / Workday (185) / Amazon / Eightfold | v (parallel, 7:00-7:15am UTC) Python ingestion + cross-source dedup + job lifecycle reactivation | v PostgreSQL 16 (job_postings, companies, roles, skills, locations) | v (8:15am) Python NLP enrichment (skills, salary, experience, workplace) | v (8:30am) refresh_job_honesty() — PostgreSQL stored function | v (8:45am) discover_companies + dedup_sources | v (9:15am) expire_jobs — marks roles gone from ATS boards | v (9:30am) sync discovered_companies active_roles counts | v (9:45am) dbt run — 18 models across staging, dimensions, facts, and marts | v analytics_analytics schema: fct_jobs + dim_companies + mart_company_scorecard + mart_skill_demand + mart_salary_benchmarks + mart_ghost_job_index + mart_hiring_difficulty + mart_honesty_scores + mart_sector_benchmarks

---

## dbt Marts

| Mart | Description | Rows |
|---|---|---|
| `fct_jobs` | Core fact table — one row per job posting | 16,000+ |
| `dim_companies` | Company dimension with sector, headcount, hiring intensity | 4,700+ |
| `mart_company_scorecard` | One row per company — all metrics combined | 476 |
| `mart_skill_demand` | Skill × experience level demand and salary | 739 |
| `mart_salary_benchmarks` | Salary percentiles by source, level, state | 158 |
| `mart_ghost_job_index` | Ghost probability per active job | 2,163 |
| `mart_hiring_difficulty` | Company-level difficulty score breakdown | 254 |
| `mart_honesty_scores` | Company-level honesty score aggregates | 280 |
| `mart_sector_benchmarks` | Sector-level rollup of all key metrics | 20 |

---

## Sector Coverage

| Sector | Active Roles | Companies | Transparency | Median Max Salary |
|---|---|---|---|---|
| Fintech/Payments | 840 | 76 | 58% | $192K |
| Healthcare Tech | 404 | 72 | 67% | $182K |
| SaaS/Enterprise | 383 | 91 | 49% | $194K |
| Consulting | 348 | 26 | 14% | $162K |
| Consumer/Marketplace | 343 | 40 | 55% | $200K |
| AI/ML | 183 | 34 | 56% | $247K |
| Defense/GovTech | 237 | 20 | 80% | $192K |
| Autonomous/Robotics | 154 | 16 | 73% | $204K |
| Gaming | 85 | 9 | 58% | $220K |

---

## Reports

| Report | Published | Coverage |
|---|---|---|
| Inaugural Edition | April 2026 | 6,100+ postings, 1,000+ companies, 6 ATS sources |
| May 2026 | May 1, 2026 | Month-over-month trends |
| Q2 2026 | July 1, 2026 | First quarter-over-quarter comparison |

---

## Data Quality

- Salary cap enforced at parse time and DB constraint — values above $1M annual rejected
- Cross-source deduplication — zero confirmed duplicates across all 6 ATS sources
- 86% experience level coverage on Tier 1 postings
- 85%+ sector coverage on active Tier 1 postings
- Honesty scoring — 100% of Tier 1 postings scored via PostgreSQL stored function
- Salary coverage — 55% of Tier 1 active postings contain verified salary data
- All pipeline runs logged with insert/skip/error counts per source
- US-only filter applied at ingestion — 40+ international city/country patterns rejected
- Job lifecycle tracking — last_seen_at updated on every ingest; expire_jobs runs nightly at 9:15am UTC
- OOM protection — 4GB RAM server handles 1,800+ Greenhouse company ingest without memory kill

---

## Contact

Open to conversations about data licensing, custom sector analysis, and talent intelligence partnerships.

**Luke Jones**
jones31luke@gmail.com
linkedin.com/in/luke-j-78a02121b

---

*Dataset updates nightly. Next report: May 1, 2026.*
