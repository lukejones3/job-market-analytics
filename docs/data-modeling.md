# From Google Sheets to a Relational Database

## Why Google Sheets First
The dataset began in Google Sheets to allow fast iteration, manual validation, and controlled decisions while the data model was still evolving.

Rather than treating Sheets as a flat log, the data was structured intentionally with relational concepts in mind.

## ID-Driven Design
Each entity was assigned a stable ID at entry time:

- Job postings → `job_id`
- Companies → `company_id`
- Locations → `location_id`
- Roles → `role_id`
- Skills → `skill_id`
- Job Skills → `job_skill_id`

This prevented downstream issues caused by inconsistent naming (e.g. “SQL”, “sql”, “SQL experience”) and made joins fixed.

## Relational Structure
The model mirrors a production-style schema:

- One job posting belongs to one company and one role
- Each job posting can reference multiple skills
- Skills are stored once and linked via a bridge table (`job_skills`)

This structure enabled:
- Clean joins
- Aggregation by skill, role, or experience level
- Future automation without schema changes

## Migration to PostgreSQL
Because the data was modeled relationally from the start, migrating from Google Sheets to PostgreSQL required minimal transformation beyond type enforcement and indexing.

