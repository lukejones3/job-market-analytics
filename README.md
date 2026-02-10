# Job Market Analytics

This project treats the job market as a structured dataset and builds a reproducible pipeline to ingest, normalize, enrich, and analyze real job postings across analytics, business intelligence, and operations-adjacent roles.

Rather than treating job search as a manual or anecdotal process, this system models labor demand as queryable data and tracks trends across skills, experience levels, compensation ranges, and role requirements.

The long-term vision is to transform raw job postings into structured, decision-ready labor market intelligence.

---

## Why I Built This

Most job search tools are optimized for browsing, not analysis.

I wanted to answer questions like:

- Which skills consistently correlate with higher compensation  
- How requirements change across entry → associate → mid → senior roles  
- How frequently core analytics tools (SQL, Excel, BI tools, Python) appear in real postings  
- How labor demand signals evolve as the dataset grows  

This project started as a personal job tracking system and evolved into a structured data pipeline designed to scale ingestion, improve reliability, and enable repeatable market analysis.

---

## System Philosophy

This project is intentionally designed to be:

- **Platform-safe** — No scraping or automated site extraction  
- **Deterministic** — Manual capture, automated downstream processing  
- **Auditable** — Pipeline logging and error tracking built in  
- **Extensible** — New enrichment logic and analytics layers can be added without breaking ingestion  

---

## Architecture Overview

The system has evolved from manual job tracking into a pipeline-style ingestion and enrichment architecture.

### Current Flow

Manual Safe Capture
→ Batch Dump Ingestion
→ PostgreSQL Storage
→ Python Enrichment + Parsing
→ Skill Extraction + Classification
→ Pipeline Logging + Error Tracking
→ BI Visualization + Insight Generation
---

## Data Model

The dataset is built using normalized relational tables to maintain referential integrity and enable clean downstream analytics.

### Core Domain Tables

- `job_postings`
- `companies`
- `roles`
- `skills`
- `job_skills` (bridge table)

### Pipeline Observability Tables

- `pipeline_runs`  
  Tracks ingestion/enrichment execution metrics and outcomes  

- `pipeline_errors`  
  Captures stage-level failures for debugging and reliability monitoring  

---

## Ingestion Strategy (Intentional Design Decision)

I intentionally avoided scraping or automated site extraction.

Instead:

- Job descriptions are manually captured (copy-safe, platform-safe)  
- Batch ingestion scripts parse structured dump files  
- Automation happens downstream (cleaning, enrichment, classification)  

This ensures:

- Platform compliance  
- Low operational risk  
- High data quality control  
- Easy debugging and reproducibility  

---

## Enrichment & Automation Layer

Python enrichment scripts automatically:

- Extract company, role, and location signals  
- Infer experience level from text patterns  
- Classify skill priority (required vs preferred)  
- Parse salary ranges while filtering false positives (ex: “1–2 years” ≠ salary)  
- Populate bridge tables linking jobs → skills  

The enrichment layer is designed to be modular and continuously improved as new parsing edge cases are discovered.

---

## Pipeline Reliability & Observability

The system includes production-style pipeline safety features:

- Run-level logging (`pipeline_runs`)  
- Stage-level error capture (`pipeline_errors`)  
- Batch ingestion with duplicate protection  
- Delimiter-based ingestion format for safe manual capture  
- Scheduled database backups for disaster recovery  

---

## Tech Stack

- Python  
- PostgreSQL  
- SQL  
- Power BI  
- Git / GitHub  

---

## Roadmap

### Near Term

- Monthly skill demand snapshot tables  
- Public Dataset v1 release  
- First production-style dashboard (skill demand + salary + experience segmentation)  

### Mid Term

- Skill demand trend modeling over time  
- Compensation band movement tracking  
- Demand shift detection by role category  

### Long Term Vision

Transform this dataset into a continuously updated labor market intelligence layer capable of supporting:

- Candidate decision tooling  
- Workforce planning insights  
- Skills-to-compensation benchmarking  
- Market demand forecasting  

---

## Status

Active build phase.

The dataset and automation pipeline are expanding weekly as ingestion volume increases and new reliability and observability features are added.
