# Job Market Analytics

An independent, production-grade labor market intelligence platform tracking data, analytics, and ML hiring across 260+ companies.

The system ingests job postings nightly from Greenhouse, Lever, and Ashby ATS APIs, enriches each posting through a custom NLP pipeline, and scores every posting on a proprietary Honesty Score measuring salary transparency, scope realism, and skill-to-level plausibility.

**[April 2026 Inaugural Report →](https://solstice-stock-6c6.notion.site/APRIL-2026-DATA-ANALYTICS-JOB-MARKET-REPORT-INAUGURAL-EDITION-33c61d18db3480348780dd2c43bbc0d5)**

---

## Current Scale

| Metric | Value |
|---|---|
| Tier 1 job postings (fully enriched) | 1,350+ |
| Companies monitored nightly | 260+ |
| Total companies in monitoring pool | 1,375+ |
| Canonical skills tracked | 127 |
| Sectors classified | 22 |
| Dataset updated | Nightly |

---

## What Makes This Different

**Honesty Score** — every posting is scored 0-100 across 5 penalty dimensions: salary transparency, scope realism, skill-to-level plausibility, internal consistency, and EEO boilerplate dominance. No other job market dataset publishes this.

**Cross-source deduplication** — the same role posted on Greenhouse, Lever, and Ashby is ingested once. Deduplication is location-aware — the same title in San Francisco and New York are treated as distinct positions.

**Hiring intensity** — open data roles normalized against company headcount. Surfaces which companies are making the biggest organizational bet on data talent right now.

**Salary premium analysis** — compensation signal quantified by skill. LLMs carry a +15.2% salary premium. Power BI carries a -34.2% penalty. Updated monthly.

---

## Stack

- **Ingestion** — Python 3.9, multi-source ATS APIs (Greenhouse, Lever, Ashby), nightly cron automation
- **Storage** — PostgreSQL 16 on DigitalOcean Ubuntu 24, pg_dump backups with 7-day retention
- **Enrichment** — custom NLP pipeline: skill extraction from 127-skill canonical allowlist, salary parsing, experience level inference, workplace type classification
- **Transformation** — dbt 13-model layer: staging views, fact tables, dimension tables, analytics marts
- **Scoring** — proprietary Honesty Score via PostgreSQL stored function, refreshed nightly after enrichment

---

## Pipeline Architecture

```
ATS APIs (Greenhouse / Lever / Ashby)
        ↓
Python ingestion + cross-source dedup
        ↓
PostgreSQL 16 (job_postings, companies, roles, skills, locations)
        ↓
Python NLP enrichment (skills, salary, experience, workplace)
        ↓
refresh_job_honesty() — PostgreSQL stored function
        ↓
dbt transformation layer (13 models)
        ↓
analytics_analytics.fct_jobs + dim_companies + mart_skill_demand
```

---

## Reports

| Report | Published | Coverage |
|---|---|---|
| Inaugural Edition | April 2026 | 1,350+ postings, 260+ companies |
| May 2026 | May 1, 2026 | Month-over-month trends |
| Q2 2026 | July 1, 2026 | First quarter-over-quarter comparison |

---

## Data Quality

- Salary cap enforced at parse time and DB constraint level — values above $1M annual rejected
- Cross-source deduplication — zero confirmed duplicates across ATS sources
- Honesty scoring — 100% of Tier 1 postings scored
- Salary coverage — ~52% of Tier 1 postings contain verified salary data
- All pipeline runs logged with insert/skip/error counts

---

## Contact

Open to conversations about data licensing, custom sector analysis, and talent intelligence.

**Luke Jones**
jones31luke@gmail.com
linkedin.com/in/luke-j-78a02121b

---

*Dataset updates nightly. Next report: May 1, 2026.*
