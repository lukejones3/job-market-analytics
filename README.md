# Job Market Analytics

An independent, production-grade labor market intelligence platform tracking data, analytics, and ML hiring across 355+ companies nightly.

The system ingests job postings from Greenhouse, Lever, Ashby, Workday, Amazon, and Eightfold — enriches each posting through a custom NLP pipeline — and scores every posting on a proprietary Honesty Score measuring salary transparency, scope realism, and skill-to-level plausibility.

**[April 2026 Inaugural Report →](https://solstice-stock-6c6.notion.site/APRIL-2026-DATA-ANALYTICS-JOB-MARKET-REPORT-INAUGURAL-EDITION-33c61d18db3480348780dd2c43bbc0d5)**

---

## Current Scale

| Metric | Value |
|---|---|
| Tier 1 job postings (fully enriched) | 3,000+ |
| ATS sources | 6 (Greenhouse, Lever, Ashby, Workday, Amazon, Eightfold) |
| Companies actively monitored | 355+ |
| Greenhouse companies in pool | 935 |
| Workday enterprise companies | 70+ |
| Canonical skills tracked | 114 (with 239 aliases) |
| Sectors classified | 26 |
| Salary transparency coverage | 65% |
| Dataset updated | Nightly (parallelized, ~50 min runtime) |

---

## What Makes This Different

**6-source ingestion** — Greenhouse, Lever, and Ashby cover the venture-backed tech market. Workday covers enterprise companies like Netflix, Disney, Walmart, Capital One, BlackRock, and Northrop Grumman. Amazon and Eightfold (Microsoft, Morgan Stanley, Ford) fill the gaps. No other independent dataset spans all six.

**Honesty Score** — every posting is scored 0-100 across 5 penalty dimensions: salary transparency, scope realism, skill-to-level plausibility, internal consistency, and EEO boilerplate dominance. No other job market dataset publishes this.

**Hiring Difficulty Score** — composite metric combining skill rarity, role complexity vs sector peers, salary competitiveness, and posting opacity. Predicts which roles will be hardest to fill before they go stale.

**Compensation Competitiveness Index** — company average max salary vs sector median, by experience level. Identifies which companies are paying above or below market for specific role types.

**Skill Gap Score** — percentage of a company's required skills appearing in fewer than 5% of all postings. High scores predict longer time-to-fill and recruiting difficulty.

**Job lifecycle tracking** — nightly expiry system marks roles that disappear from ATS boards. Enables posting longevity analysis and role velocity trending as data accumulates.

**Cross-source deduplication** — the same role posted across multiple ATS platforms is ingested once. Zero confirmed duplicates across all 6 sources.

**Salary parsing engine** — custom multi-pattern regex system handling 30+ salary formats: European period separators, HTML entity em-dashes, OTE min/max pairs, zone-based comp, hourly with USD suffix, truncated numbers, and more. 65% salary coverage on Tier 1 postings.

---

## Proprietary Metrics

| Metric | Description | Status |
|---|---|---|
| Honesty Score | 0-100 posting quality score | ✅ Live |
| Compensation Competitiveness Index | % above/below sector median by level | ✅ Live |
| Job Description Complexity Score | Avg required skills vs sector peers | ✅ Live |
| Skill Gap Score | % niche skills (<5% prevalence) | ✅ Live |
| Hiring Difficulty Score | Composite fill-difficulty predictor | ✅ Live |
| Role Velocity | Hiring acceleration/deceleration | 🔄 Building (needs 30d data) |
| Posting Longevity Index | Avg days open before expiry | 🔄 Building (needs 30d data) |

---

## Stack

- **Ingestion** — Python 3.9, 6-source ATS pipeline, parallelized nightly cron (all sources simultaneously)
- **Storage** — PostgreSQL 16 on DigitalOcean Ubuntu 24, pg_dump backups with 7-day retention
- **Enrichment** — custom NLP pipeline: 114-skill extraction with 239 aliases, 30+ salary format parser, experience level inference, workplace type classification
- **Transformation** — dbt 13-model layer: staging views, fact tables, dimension tables, analytics marts
- **Scoring** — proprietary Honesty Score via PostgreSQL stored function, refreshed nightly after enrichment
- **Lifecycle** — nightly expiry system with ingest sanity check, `last_seen_at` tracking on all Tier 1 jobs

---

## Pipeline Architecture
Greenhouse / Lever / Ashby / Workday / Amazon / Eightfold ↓ (parallel, 7:00-7:15am UTC) Python ingestion + cross-source dedup ↓ PostgreSQL 16 (job_postings, companies, roles, skills, locations) ↓ (8:15am) Python NLP enrichment (skills, salary, experience, workplace) ↓ (8:30am) refresh_job_honesty() — PostgreSQL stored function ↓ (8:45am) discover_companies + dedup_sources ↓ (9:15am) expire_jobs — marks roles gone from ATS boards ↓ dbt transformation layer (13 models) ↓ fct_jobs + dim_companies + mart_skill_demand + mart_salary_benchmarks
---

## Transparency Analysis (April 2026)

| Sector | Roles | Salary Transparency |
|---|---|---|
| Autonomous/Robotics | 106 | 90.6% |
| Defense/GovTech | 128 | 81.3% |
| Consumer/Marketplace | 274 | 74.8% |
| Fintech/Payments | 694 | 71.3% |
| AI/ML | 209 | 37.8% |
| Consulting | 176 | 5.1% |

**Most transparent:** Roblox, Samsara, Pinterest, Leidos, Booz Allen, Scale AI (all 100%)
**Least transparent:** OpenAI (0% on 48 roles), Stripe (0% on 15 roles), PwC (0.7% on 142 roles)

---

## Reports

| Report | Published | Coverage |
|---|---|---|
| Inaugural Edition | April 2026 | 3,000 postings, 355 companies, 6 ATS sources |
| May 2026 | May 1, 2026 | Month-over-month trends |
| Q2 2026 | July 1, 2026 | First quarter-over-quarter comparison |

---

## Data Quality

- Salary cap enforced at parse time and DB constraint level — values above $1M annual rejected
- Cross-source deduplication — zero confirmed duplicates across all 6 ATS sources
- 100% experience level coverage on Tier 1 postings
- 99.7% sector coverage on Tier 1 postings
- Honesty scoring — 100% of Tier 1 postings scored
- Salary coverage — 65% of Tier 1 postings contain verified salary data
- All pipeline runs logged with insert/skip/error counts per source
- US-only filter applied at ingestion — international roles excluded

---

## Contact

Open to conversations about data licensing, custom sector analysis, and talent intelligence partnerships.

**Luke Jones**
jones31luke@gmail.com
linkedin.com/in/luke-j-78a02121b

---

*Dataset updates nightly. Next report: May 1, 2026.*
