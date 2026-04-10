# Methodology

## Data Collection

Job postings are collected nightly via public ATS APIs from three sources:

- **Greenhouse** — 1,014 companies monitored, full job description text returned via `/v1/boards/{token}/jobs?content=true`
- **Lever** — 278 companies monitored, structured salary range fields captured where available
- **Ashby** — 71 companies monitored, full `descriptionPlain` text returned via posting API

Only data and analytics roles are ingested — titles must match a target keyword list including data analyst, data engineer, data scientist, analytics engineer, machine learning engineer, business intelligence, and related variants.

## Deduplication

Cross-source deduplication uses a composite key of `(company, role title, normalized location)`. Remote roles collapse to a single key regardless of ATS source. Onsite and hybrid roles use the first city segment of the location string, allowing the same title in different cities to be treated as distinct positions.

## Enrichment

Each Tier 1 posting is processed through a custom NLP pipeline that extracts:

- **Skills** — matched against a 127-skill canonical allowlist with alias expansion. Skills are classified as required, preferred, or nice-to-have based on surrounding language.
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

Hiring intensity is calculated as `active_roles / employee_count * 100`. Employee counts are sourced from Wikipedia API and public records as of early 2026 and are approximate. Only companies with verified headcount data are included in intensity rankings.

## Salary Premium Analysis

Salary premiums are calculated by comparing the average maximum annual salary for postings requiring a given skill against the overall dataset average. Only skills appearing in 30 or more postings with salary data are included. Salaries above $600,000 are excluded from premium calculations to reduce distortion from outlier roles.

## Limitations

This dataset tracks US-based technology companies using Greenhouse, Lever, or Ashby as their applicant tracking system. Large enterprises, consulting firms, healthcare systems, and government employers are underrepresented as they typically use Workday, iCIMS, Taleo, or proprietary ATS platforms not accessible via public API.

Salary data is only available for postings that explicitly include compensation in the job description text or structured ATS salary fields. Approximately 48% of Tier 1 postings contain no salary information. This does not necessarily indicate non-compliance with salary disclosure laws — ranges may exist elsewhere in the hiring process outside of what this pipeline captures.

Experience level is inferred automatically and may misclassify edge cases with non-standard title conventions.

Hiring intensity figures use approximate headcount data and may not reflect current employee counts for fast-growing companies.

All findings reflect active job postings collected in April 2026 and should be interpreted as a point-in-time snapshot. Month-over-month trend data will be available beginning with the May 2026 report.

---

*For questions about methodology or data licensing: jones31luke@gmail.com*
