# Job Market Analytics

An independent, production-grade labor market intelligence platform tracking data, analytics, and ML hiring across 300+ companies nightly.

The system ingests job postings from Greenhouse, Lever, Ashby, Workday, Amazon, and Eightfold — enriches each posting through a custom NLP pipeline — and scores every posting on a proprietary Honesty Score measuring salary transparency, scope realism, and skill-to-level plausibility.

**[April 2026 Inaugural Report →](https://solstice-stock-6c6.notion.site/APRIL-2026-DATA-ANALYTICS-JOB-MARKET-REPORT-INAUGURAL-EDITION-33c61d18db3480348780dd2c43bbc0d5)**

---

## Current Scale

| Metric | Value |
|---|---|
| Tier 1 job postings (fully enriched) | 1,800+ |
| ATS sources | 6 (Greenhouse, Lever, Ashby, Workday, Amazon, Eightfold) |
| Companies monitored nightly | 300+ |
| Total companies in monitoring pool | 1,400+ |
| Canonical skills tracked | 130 |
| Sectors classified | 29 |
| Dataset updated | Nightly |

---

## What Makes This Different

**6-source ingestion** — Greenhouse, Lever, and Ashby cover the venture-backed tech market. Workday covers enterprise companies like Netflix, Adobe, PayPal, Boeing, and CVS Health. Amazon and Eightfold (Microsoft, Morgan Stanley, Ford) fill the gaps. No other independent dataset spans all six.

**Honesty Score** — every posting is scored 0-100 across 5 penalty dimensions: salary transparency, scope realism, skill-to-level plausibility, internal consistency, and EEO boilerplate dominance. No other job market dataset publishes this.

**Cross-source deduplication** — the same role posted across multiple ATS platforms is ingested once. Deduplication is location-aware — the same title in San Francisco and New York are treated as distinct positions.

**Hiring intensity (segmented)** — open data roles normalized against company headcount, split into growth-stage (roles per 100 employees) and enterprise (roles per 1,000 employees). Surfaces which companies are making the biggest organizational bet on data talent right now.

**Salary premium analysis** — compensation signal quantified by skill. LLMs carry a +15.2% salary premium. Power BI carries a -34.2% penalty. Updated monthly.

---

## Stack

- **Ingestion** — Python 3.9, 6-source ATS pipeline (Greenhouse, Lever, Ashby, Workday, Amazon, Eightfold), nightly cron automation
- **Storage** — PostgreSQL 16 on DigitalOcean Ubuntu 24, pg_dump backups with 7-day retention
- **Enrichment** — custom NLP pipeline: skill extraction from 130-skill canonical allowlist, salary parsing, experience level inference, workplace type classification
- **Transformation** — dbt 13-model layer: staging views, fact tables, dimension tables, analytics marts
- **Scoring** — proprietary Honesty Score via PostgreSQL stored function, refreshed nightly after enrichment

---

## Pipeline Architecture

```
Greenhouse / Lever / Ashby / Workday / Amazon / Eightfold
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
fct_jobs + dim_companies + mart_skill_demand + mart_salary_benchmarks
```

---

## Reports

| Report | Published | Coverage |
|---|---|---|
| Inaugural Edition | April 2026 | 1,800+ postings, 300+ companies, 6 ATS sources |
| May 2026 | May 1, 2026 | Month-over-month trends |
| Q2 2026 | July 1, 2026 | First quarter-over-quarter comparison |

---

## Data Quality

- Salary cap enforced at parse time and DB constraint level — values above $1M annual rejected
- Cross-source deduplication — zero confirmed duplicates across ATS sources
- Honesty scoring — 100% of Tier 1 postings scored
- Salary coverage — ~52% of Tier 1 postings contain verified salary data
- All pipeline runs logged with insert/skip/error counts
- US-only filter applied at ingestion — international roles excluded from all analysis

---

## Contact

Open to conversations about data licensing, custom sector analysis, and talent intelligence.

**Luke Jones**
jones31luke@gmail.com
linkedin.com/in/luke-j-78a02121b

---

*Dataset updates nightly. Next report: May 1, 2026.*
