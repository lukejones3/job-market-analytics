# Methodology

## Data Collection

Job postings are collected nightly via public ATS APIs from six sources:

- **Greenhouse** — 935 companies monitored, full job description text returned via `/v1/boards/{token}/jobs?content=true`
- **Lever** — 231 companies monitored, structured salary range fields captured where available
- **Ashby** — 71 companies monitored, full `descriptionPlain` text returned via posting API
- **Workday** — 70+ enterprise companies monitored including Netflix, Disney, Walmart, Capital One, BlackRock, Northrop Grumman, and CVS Health; full job descriptions retrieved via the Workday CXS API detail endpoint
- **Amazon** — ingested via the public amazon.jobs search API; full descriptions returned in listing response
- **Eightfold** — enterprise companies including Microsoft, Morgan Stanley, and Ford; listings retrieved via PCSX search API, full descriptions via SmartApply detail endpoint

Only data and analytics roles are ingested — titles must match a target keyword list including data analyst, data engineer, data scientist, analytics engineer, machine learning engineer, business intelligence, and related variants. All sources are filtered to US-based roles only at ingestion time.

The pipeline runs nightly in parallel — all six sources ingest simultaneously starting at 7:00am UTC, completing in approximately 50 minutes. Total Tier 1 coverage: 3,000+ postings across 355+ actively hiring companies.

---

## Deduplication

Cross-source deduplication uses a composite key of (company, role title, normalized location). Remote roles collapse to a single key regardless of ATS source. Onsite and hybrid roles use the first city segment of the location string, allowing the same title in different cities to be treated as distinct positions. Zero confirmed duplicates across all six sources as of April 2026.

---

## Enrichment

Each Tier 1 posting is processed through a custom NLP pipeline that extracts:

**Skills** — matched against a 114-skill canonical allowlist with 239 aliases. Skills are classified as required, preferred, or nice-to-have based on surrounding language. Aliases cover dialects, libraries, and common variants (e.g. `pyspark` → Spark, `postgresql` → SQL, `vertex ai` → GCP).

**Salary** — extracted via a 30+ pattern regex engine from structured fields and unstructured description text. Formats handled include: standard ranges, European period separators, HTML entity em-dashes, OTE Minimum/Maximum pairs, zone-based compensation, hourly with USD suffix, truncated numbers, single labeled values, and double dollar signs. Annual values must fall between $15,000 and $1,000,000. Hourly values must fall between $7 and $500. Ambiguous values are rejected. Salary coverage: 65% of Tier 1 postings.

**Experience level** — inferred from title patterns (Senior, Lead, Principal, Staff, Associate, Jr.) and years-of-experience language. 100% coverage achieved via fallback inference on undecorated titles.

**Workplace type** — classified as remote, hybrid, or onsite from description language and structured fields.

---

## Job Lifecycle Tracking

Every Tier 1 posting carries a `last_seen_at` timestamp updated on every ingest cycle. A nightly expiry job (running at 9:15am UTC, after all ingest sources complete) marks postings as expired when they no longer appear in their source ATS. An ingest sanity check prevents false expiry on days with pipeline failures — if fewer than 50 jobs are seen in the prior 4 hours, expiry is skipped entirely.

Expired postings are retained in the database. As longitudinal data accumulates, this enables Posting Longevity Index (average days open before expiry) and Role Velocity (hiring acceleration/deceleration) metrics.

---

## Honesty Score

Every Tier 1 posting is scored 0-100 across five penalty dimensions:

- **Pay penalty** — deducted when no salary range is present in the job description
- **Vague penalty** — deducted for scope language that is unusually broad or undefined
- **Scope penalty** — deducted when required responsibilities appear inconsistent with the stated experience level
- **Consistency penalty** — deducted when required skills conflict with the posted experience level
- **EEO penalty** — deducted when boilerplate EEO language dominates the posting at the expense of substantive content

Scores are refreshed nightly after enrichment runs.

---

## Proprietary Metrics

### Compensation Competitiveness Index
Company average max salary compared to sector median for the same experience level, expressed as a percentage above or below market. Only companies with 3+ roles with verified salary data at the same experience level are included. Salaries outside $50,000–$800,000 are excluded from benchmark calculations.

### Job Description Complexity Score
Average number of required skills per posting, segmented by company and experience level, compared to the sector average for the same level. A Senior role requiring 14 skills when the sector average is 7 signals either unrealistic requirements or genuine technical depth.

### Skill Gap Score
Percentage of a company's required skills that appear in fewer than 5% of all postings in the dataset. High scores indicate reliance on niche or proprietary tooling that few candidates possess — a leading indicator of longer time-to-fill.

### Hiring Difficulty Score
Composite metric combining four dimensions:
- Skill rarity (30%) — niche skill percentage
- Role complexity (25%) — required skills vs sector peers
- Salary competitiveness (30%) — below-market pay predicts fewer qualified applicants
- Posting opacity (15%) — hiding salary reduces applicant pool size

Scored 0-100. Higher scores indicate roles predicted to be harder to fill. Will be validated against Posting Longevity data as longitudinal records accumulate.

### Hiring Intensity
Segmented by company size to ensure meaningful comparisons:

- **Growth-stage companies** (under 5,000 employees) — `active_roles / employee_count * 100`, expressed as roles per 100 employees
- **Enterprise companies** (5,000+ employees) — `active_roles / employee_count * 1,000`, expressed as roles per 1,000 employees

Employee counts sourced from Wikipedia API, public records, and manual research as of early 2026.

### Salary Premium Analysis
Calculated by comparing the average maximum annual salary for postings requiring a given skill against the overall dataset average. Only skills appearing in 30+ postings with salary data are included. Salaries above $600,000 are excluded to reduce distortion from outlier roles.

---

## Sector Classification

26 sectors are assigned directly to the `companies` table and applied across all ATS sources. Sector coverage: 99.7% of Tier 1 postings. Classification is manual and research-based, not ML-inferred.

---

## Limitations

**Salary coverage** — 35% of Tier 1 postings contain no salary information. This does not necessarily indicate non-compliance with salary disclosure laws — ranges may exist elsewhere in the hiring process outside of what this pipeline captures.

**Experience level inference** — automatically inferred and may misclassify non-standard title conventions. Edge cases include multi-level titles ("Senior/Principal"), roman numeral suffixes, and titles without seniority markers defaulted to mid-level.

**Hiring intensity** — figures use approximate headcount data and may not reflect current employee counts for fast-growing companies. Enterprise intensity figures use global headcount as the denominator, which understates US-specific hiring activity for large multinationals.

**Longitudinal metrics** — Role Velocity and Posting Longevity Index require sustained nightly tracking. Meaningful trend data will be available beginning with the May 2026 report. Q2 2026 report will include the first genuine quarter-over-quarter comparison.

**ATS coverage** — the platform tracks six ATS sources. Companies using Taleo, iCIMS, Jobvite, SmartRecruiters, or proprietary career portals are not included in Tier 1 analysis.

---

*For questions about methodology or data licensing: jones31luke@gmail.com*
