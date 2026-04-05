# Data Collection & Methodology

This document describes how data is collected, enriched, scored, and maintained in the Job Market Analytics platform. It is intended for due diligence, academic reference, and transparency with data consumers.

---

## 1. Data Sources

### Tier 1 — Greenhouse ATS (Primary)
Job postings are retrieved via the Greenhouse public job board API (`https://api.greenhouse.io/v1/boards/{token}/jobs`). This API is intentionally public — Greenhouse exposes it so candidates can discover open roles. No authentication, scraping, or terms-of-service violation is involved.

Each company requires a known board token. Tokens are discovered manually or via the `--discover` flag in the ingestion pipeline, which probes candidate tokens and retains those that return valid responses.

Greenhouse postings include full job descriptions, enabling complete skill extraction, salary parsing, experience level inference, and honesty scoring.

### Tier 2 — Adzuna API (Market Coverage)
Adzuna is a job aggregation platform with an official developer API (`api.adzuna.com/v1/api/jobs`). API access requires registration and key-based authentication. Adzuna postings provide structured salary fields and broad market coverage but typically contain truncated descriptions. Tier 2 records are used for salary benchmarking and market coverage reporting only — they are excluded from skill demand and honesty scoring analysis.

### Tier 3 — Manual Captures (Reference Only)
A subset of 621 records were manually captured from LinkedIn in February–March 2026 prior to the automated pipeline being established. These records lack posting dates and are excluded from all primary analytics. They are retained in the database for reference and flagged with `data_quality = 'manual_capture'`.

---

## 2. Collection Process

### Ingestion Pipeline
The primary ingestion script (`python/ingest_jobs.py`) runs nightly at 07:00 UTC via cron on a DigitalOcean Ubuntu 24.04 server.

Each run:
1. Loads all enabled companies from the `discovered_companies` table
2. Queries each company's job board API
3. Filters results against a curated list of 109 role title phrases covering data analyst, data engineer, data scientist, analytics engineer, ML engineer, and adjacent roles
4. Generates a content hash (MD5) for each posting using company, role title, and description
5. Skips any posting whose hash already exists in the database
6. Inserts net-new postings with `ingested_at` timestamp

### Deduplication
Deduplication is hash-based at ingestion time. The same job posting appearing across multiple runs is inserted only once. If a job closes and a new version is posted, it is treated as a new record.

### Company Discovery
Companies are tracked in the `discovered_companies` table with ATS source, board token, active role count, and last seen date. New companies are added via manual research or the `--discover` flag. The `discover_companies.py` script refreshes active role counts nightly at 08:00 UTC.

---

## 3. Enrichment Pipeline

Each Tier 1 posting is processed by `python/enrich_job_postings.py` immediately after ingestion.

### Skill Extraction
Skills are extracted using a two-pass pattern matching approach:

**Pass 1 — Section-aware extraction:** The pipeline identifies structured sections within job descriptions (Requirements, Qualifications, Skills, What You'll Need, etc.) using regex boundary detection. Skills are extracted from within these sections using compiled regex patterns built from a canonical allowlist.

**Pass 2 — Alias matching:** A fallback alias dictionary maps common variants to canonical skill names (e.g. "sklearn" → "Scikit-learn", "pyspark" → "Spark"). Skills extracted in Pass 2 are tagged with lower confidence.

The canonical skill allowlist contains 90+ skills covering programming languages, data tools, cloud platforms, ML frameworks, BI tools, and statistical methods. Skills not in the allowlist are not extracted — this is intentional to prevent false positives.

Skill priority (required vs preferred) is inferred from section context and qualifying language ("must have", "nice to have", "preferred", etc.).

### Salary Parsing
Salary ranges are extracted from description text and structured salary fields using regex patterns covering:
- Annual ranges: "$120,000 - $160,000"
- Hourly rates: "$45 - $65/hour" (annualized at 2,080 hours)
- Single values: "up to $180,000"

False positives are filtered by range plausibility checks (minimum $20K, maximum $600K annual). Salary period (annual, hourly) is stored separately. The `salary_max_annual` field is the primary salary signal used in analytics.

### Experience Level Inference
Experience level is inferred from two signals:

**Title patterns:** Role titles are matched against patterns for entry (Junior, New Grad, Associate I), associate (Associate, Level 1-2), mid (II, Mid-Level), senior (Senior, Sr., Lead, Staff, Principal), and manager/director levels.

**Years of experience:** The description is scanned for patterns like "3+ years", "5-7 years of experience". Extracted years are mapped to experience buckets.

Title signal takes precedence over years-of-experience signal when both are present.

### Workplace Type Classification
Workplace type (remote, hybrid, onsite) is inferred from title and description text using keyword matching. Remote-first language, office location requirements, and hybrid scheduling language are detected via regex.

---

## 4. Honesty Scoring

Each Tier 1 posting receives a proprietary Honesty Score from 0 to 100 calculated by the `refresh_job_honesty()` PostgreSQL function defined in `sql/job_honesty.sql`.

### Scoring Dimensions

| Dimension | Max Penalty | Description |
|-----------|-------------|-------------|
| Salary transparency | 25 pts | No salary disclosed, vague language (DOE, competitive), missing pay period, implausibly wide range |
| Scope realism | 25 pts | Excessive required skills for experience level, years requirement language |
| Skill-to-level plausibility | 25 pts | Senior-level tech stack requirements in entry/associate postings |
| Internal consistency | 15 pts | Remote job with onsite language, entry title with senior role name |
| EEO boilerplate dominance | 10 pts | EEO language comprising >40% of a long posting |

**Score = 100 minus sum of all applicable penalties, floored at 0.**

### Skill Group Weights
The plausibility dimension uses a weighted skill group system. Skills are assigned to groups (Data Engineering Core, Cloud/Infra, ML/Data Science, BI Stack, Software Engineering) with weights that vary by experience level. An entry-level posting requiring cloud infrastructure and ML framework skills is penalized more heavily than a senior posting with the same requirements.

### Scoring Frequency
Honesty scores are refreshed nightly at 07:30 UTC after ingestion completes. All Tier 1 postings within the last 12 months are rescored on each run to reflect any schema or weight changes.

### Company-Level Scores
Company scores reflect the average Honesty Score across all scored postings for that company. Companies with fewer than 8 scored postings are excluded from company-level reporting to ensure statistical stability.

---

## 5. Data Quality Controls

### Stale Listing Detection
Adzuna postings with posting dates prior to June 2025 are flagged as `data_quality = 'stale_listing'` and excluded from all analytics marts. These represent listings that aggregators failed to remove after the role closed.

### Data Tier Filtering
All dbt analytics marts filter by data tier explicitly:
- `mart_skill_demand` — Tier 1 only
- `mart_salary_benchmarks` — Tier 1 and 2
- `mart_market_coverage` — Tier 1 and 2
- All marts exclude `data_quality != 'ok'` records

### Pipeline Observability
Every ingestion run is logged to the `pipeline_runs` table with start time, insert count, skip count, error count, and status. The `morning_check.py` script produces a daily health report summarizing pipeline activity, database state, and active company counts.

---

## 6. Infrastructure

| Component | Details |
|-----------|---------|
| Server | DigitalOcean Droplet, Ubuntu 24.04, NYC1 region |
| Database | PostgreSQL 16 |
| Transformation | dbt 1.10, 13 models |
| Backups | Nightly pg_dump, 7-day retention |
| Version control | GitHub — github.com/lukejones3/job-market-analytics |

---

## 7. Limitations

- **ATS coverage bias:** The dataset skews toward US-based technology and fintech companies using Greenhouse or Lever. Large enterprises, consulting firms, healthcare systems, and government employers are underrepresented as they typically use Workday, iCIMS, Taleo, or proprietary ATS platforms.
- **Salary data availability:** 48.3% of Tier 1 postings contain no salary information. Salary analytics reflect only the subset of companies that voluntarily disclose compensation.
- **Experience level inference accuracy:** Experience level is inferred automatically and may misclassify postings with non-standard title conventions or ambiguous language. Estimated accuracy is 85-90% based on manual spot checks.
- **Skill extraction scope:** Only skills present in the canonical allowlist are extracted. Emerging tools not yet added to the allowlist will be underrepresented until discovered via the `discover_skills.py` co-occurrence analysis and promoted.
- **Snapshot nature:** All findings reflect postings observed during the collection period. Closed roles that were never ingested are not represented. The dataset captures a sample of the market, not the complete market.
- **Collection start date:** Automated nightly collection began April 1, 2026. Records ingested prior to this date were collected via manual or semi-manual processes with less consistent coverage.

---

## 8. Contact

Luke Jones
jones31luke@gmail.com
github.com/lukejones3/job-market-analytics

*For data licensing inquiries, methodology questions, or research collaboration please reach out via email.*
