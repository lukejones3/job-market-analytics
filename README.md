# DataHiringIQ

**Job intelligence platform built for seekers, not employers.**

🌐 [datahiringiq.com](https://datahiringiq.com)

---

DataHiringIQ pulls job postings directly from company applicant tracking systems — Greenhouse, Lever, Workday, Ashby, SmartRecruiters, Eightfold, and Amazon's hiring API — and layers structured intelligence on top of the raw data. Built for the seeker, not the recruiter.

Every job platform today (LinkedIn, Indeed, ZipRecruiter) makes money from employers. The seeker is the product. DataHiringIQ inverts that: free for casual browsers, $19/mo for active job hunters, with the entire product designed around the person looking for work.

---

## What it does

- **Scrapes 7 ATS systems nightly** — bypasses LinkedIn entirely, no scraping legal grey area
- **Flags ghost jobs** — currently 22% of active data/ML postings flagged at >70% probability based on time-to-close patterns and re-post frequency
- **Maps hiring manager LinkedIn for every role** — skip the resume black hole, reach the actual person hiring
- **Traffic-light signal on every posting** — green/yellow/red based on freshness, salary disclosure, contact availability, ghost probability, and lifecycle patterns
- **Resume-to-job match scoring** — top-50 matches with skill-gap analysis (Pro)

## Current scale

| Metric | Value |
|---|---|
| Active job postings | 5,400+ |
| US companies tracked | 1,200+ |
| ATS sources | 7 |
| Salary transparency | 64% |
| Hiring contact coverage | 77% |
| Ghost jobs flagged | 22% of active |
| Refresh cadence | Nightly |

Currently focused on data and ML roles. Multi-vertical expansion (finance, marketing, engineering, ops) on the roadmap.

## Stack

- **Backend:** Python 3.12, PostgreSQL 16, FastAPI
- **Data layer:** dbt with 18+ transformation models, ~60 tables
- **Ingestion:** Custom harvesters for 7 ATS APIs, cron-scheduled with cross-source dedup
- **NLP:** Salary parsing, experience inference, role classification, skills extraction, hiring contact mapping
- **AI:** Claude Haiku 4.5 for role categorization (cached, ~80% LLM call reduction)
- **Frontend:** Streamlit Cloud (interim — full Next.js rebuild planned)
- **Infrastructure:** DigitalOcean, nginx + Let's Encrypt, Cloudflare edge protection
- **Billing:** Stripe live subscriptions, Resend transactional email

## Status

Public freemium launch: **May 2, 2026.**

Built solo by [Luke Jones](https://linkedin.com/in/luke-j-78a02121b) — finance major who learned Python, SQL, and infrastructure from scratch in 4 months specifically to build this. The product itself is a system improvement on the broken job search experience.

## Ops Notes

### Enrichment pipeline

The enrichment cron (`enrich_job_postings.py`) runs **daily at 06:30 UTC** with `--no-llm --limit 5000`.
LLM role classification is disabled. The pipeline uses regex + heuristics + the `cat_cache` (46k+ entries) for classification.

**To re-enable LLM** (when Anthropic credits are restored):
1. Remove `--no-llm` from the cron line in `crontab.txt`
2. Add `ENRICH_MAX_LLM_CALLS=10000` before the command, or export it in the environment
3. Install new crontab on the droplet: `crontab crontab.txt`

### Salary fixtures

Salary parser regression tests live in `python/test_salary_parser.py`. Run locally with:

```
python python/test_salary_parser.py
```

CI runs these automatically (via `.github/workflows/test-salary-parser.yml`) on every push or PR that touches `enrich_job_postings.py` or the test file. To add a new fixture, append a `(name, text, exp_min, exp_max, exp_period)` tuple to the `TESTS` list in `test_salary_parser.py`.

### Morning report

Sent daily at 08:30 UTC via Resend. Includes a "Salary Coverage — Last 24h by Source" table and a day-over-day delta on the salary-gap indicator (jobs with `$` in description but no salary parsed). A >20% rise in that indicator is flagged as a likely new ATS format or regex gap.

## Contact

[jones31luke@gmail.com](mailto:jones31luke@gmail.com)
