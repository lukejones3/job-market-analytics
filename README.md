# Job Market Analytics

This project analyzes real-world job postings to identify trends in skills, salary ranges, and role requirements across analytics, strategy, and operations-adjacent roles.

## Purpose
I built this project to move beyond anecdotal job searching and instead treat the labor market as a dataset. The goal is to understand:
- Which skills materially affect compensation
- How requirements differ by experience level
- How often “SQL”, “Excel”, and BI tools actually appear in postings

## Data Modeling Approach

Job data was initially logged manually in Google Sheets to ensure consistent definitions and controlled data quality. Each job posting was assigned a unique `job_id`, and related attributes (companies, roles, skills) were tracked using stable ID values rather than free-text fields.

This allowed the dataset to be structured into normalized, relational tables:

- `job_postings`
- `companies`
- `roles`
- `skills`
- `job_skills` (bridge table)

Designing the data this way ensured referential integrity from the start and made it straightforward to migrate the dataset into PostgreSQL without rework.


## Data Pipeline
- Job postings are collected and logged into structured tables
- Python is used for ingestion, cleaning, and automation
- PostgreSQL serves as the analytical database
- Power BI is used for visualization and insight generation

## Tech Stack
- Python
- PostgreSQL
- Power BI
- SQL
- Git / GitHub

## Status
This project is actively evolving. New job data, queries, and dashboards are added iteratively as the dataset grows.

