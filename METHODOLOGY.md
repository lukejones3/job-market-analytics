# Methodology

## Data Collection

Job postings are collected nightly via public ATS APIs from six sources:

- **Greenhouse** — 1,014 companies monitored, full job description text returned via `/v1/boards/{token}/jobs?content=true`
- **Lever** — 278 companies monitored, structured salary range fields captured where available
- **Ashby** — 71 companies monitored, full `descriptionPlain` text returned via posting API
- **Workday** — 26 enterprise companies monitored including Netflix, Adobe, PayPal, Boeing, and CVS Health; full job descriptions retrieved via the Workday CXS API detail endpoint
- **Amazon** — ingested via the public `amazon.jobs` search API; full descriptions returned in listing response
- **Eightfold** — enterprise companies including Microsoft and Morgan Stanley; listings retrieved via PCSX search API, full descriptions via SmartApply detail endpoint

Only data and analytics roles are ingested — titles must match a target keyword list including data analyst, data engineer, data scientist, analytics engineer, machine learning engineer, business intelligence, and related variants. All sources are filtered to US-based roles only at ingestion time.

## Deduplication

Cross-source deduplication uses a composite key of `(company, role title, normalized location)`. Remote roles collapse to a single key regardless of ATS source. Onsite and hybrid roles use the first city segment of the location string, allowing the same title in different cities to be treated as distinct positions.

## Enrichment

Each Tier 1 posting is processed through a custom NLP pipeline that extracts:

- **Skills** — matched against a 130-skill canonical allowlist with alias expansion. Skills are classified as required, preferred, or nice-to-have based on surrounding language.
- **Salary** — extracted via regex from structured fields and unstructured description text. Annual values must fall between $15,000 and $1,000,000. Hourly values must fall between $7 and $500. Ambiguous values below $30,000 are rejected.
- **Experience level** — inferred from title patterns and years-of-experience language. Estimated accuracy 85-90% based on manual spot checks.
- **Workplace type** — classified as remote, hybrid, or onsite from description language and structured fields.

## Honesty Score

Every Tier 1 posting is scored 0-100 across five penalty dimensions:

- **Pay penalty** — deducted when no salary range is present in the job description
- **Vague penalty** — deducted for scope language that is unusually broad or undefined
- **Scope penalty** — deducted when required responsibilities appear inconsistent with the stated experience level
- **Consistency penalty** — deducted when required skills conflict with the posted experience level
- **EEO penalty** — deducted when boilerplate EEO language dominates the posting at the expense of substantive content

Scores are refreshed nightly after enrichment runs.

## Hiring Intensity

Hiring intensity is segmented by company size to ensure meaningful comparisons:

- **Growth-stage companies (under 5,000 employees)** — calculated as `active_roles / employee_count * 100`, expressed as roles per 100 employees. These companies are predominantly US-focused, making global headcount a reliable denominator.
- **Enterprise companies (5,000+ employees)** — calculated as `active_roles / employee_count * 1,000`, expressed as roles per 1,000 employees. These figures use global headcount and should be interpreted as approximate — a large multinational with 200,000 global employees will show lower intensity than its US-only hiring activity would suggest.

Employee counts are sourced from Wikipedia API, public records, and manual research as of early 2026 and are approximate.

## Salary Premium Analysis

Salary premiums are calculated by comparing the average maximum annual salary for postings requiring a given skill against the overall dataset average. Only skills appearing in 30 or more postings with salary data are included. Salaries above $600,000 are excluded from premium calculations to reduce distortion from outlier roles.

## Limitations

Salary data is only available for postings that explicitly include compensation in the job description text or structured ATS salary fields. Approximately 48% of Tier 1 postings contain no salary information. This does not necessarily indicate non-compliance with salary disclosure laws — ranges may exist elsewhere in the hiring process outside of what this pipeline captures.

Experience level is inferred automatically and may misclassify edge cases with non-standard title conventions.

Hiring intensity figures use approximate headcount data and may not reflect current employee counts for fast-growing companies. Enterprise intensity figures use global headcount as the denominator.

All findings reflect active job postings collected in April 2026 and should be interpreted as a point-in-time snapshot. Month-over-month trend data will be available beginning with the May 2026 report.

---

*For questions about methodology or data licensing: jones31luke@gmail.com*
