Job Market Analytics
This project treats the job market as a structured dataset and builds a reproducible pipeline to ingest, normalize, enrich, and analyze real job postings across analytics, business intelligence, and operations-adjacent roles.
Rather than treating job search as a manual or anecdotal process, this system models labor demand as queryable data and tracks trends across skills, experience levels, compensation ranges, and role requirements.

Why I Built This
Most job search tools are designed for browsing, not analysis.
I wanted to answer questions like:
• Which skills consistently correlate with higher compensation
• How requirements change across entry → associate → mid → senior roles
• How frequently core analytics tools (SQL, Excel, BI tools, Python) appear in real postings
• How job requirements shift over time as the dataset grows

Architecture Overview
The project has evolved from manual job tracking into a pipeline-style ingestion and enrichment system.
Current Flow
Manual Safe Capture
→ Batch Dump Ingestion
→ PostgreSQL Storage
→ Python Enrichment + Parsing
→ Skill Extraction + Classification
→ Pipeline Logging + Error Tracking
→ BI Visualization + Insight Generation

Data Model
The dataset is built using normalized relational tables to maintain referential integrity and allow clean downstream analysis.
Core tables:
• job_postings
• companies
• roles
• skills
• job_skills (bridge table)
Supporting pipeline tables:
• pipeline_runs — tracks ingestion/enrichment runs
• pipeline_errors — captures stage-level failures for debugging and reliability

Ingestion Strategy (Intentional Design Decision)
I intentionally avoided scraping or automated site extraction.
Instead:
• Job descriptions are manually captured (copy-safe, platform-safe)
• Batch ingestion scripts parse structured dump files
• Automation happens downstream (cleaning, enrichment, classification)
This keeps the system:
• Platform compliant
• Low risk
• Deterministic
• Easy to debug

Enrichment & Automation
Python enrichment scripts automatically:
• Extract company, role, and location signals
• Infer experience level from text patterns
• Classify skill priority (required vs preferred)
• Parse salary ranges while filtering false positives (ex: “1–2 years” ≠ salary)
• Populate bridge tables for skill relationships

Pipeline Reliability Features
• Run-level logging (pipeline_runs)
• Stage-level error capture (pipeline_errors)
• Batch ingestion with duplicate protection
• Manual-safe delimiter-based ingestion format
• Scheduled database backups for disaster recovery

Tech Stack
Python
PostgreSQL
SQL
Power BI
Git / GitHub

Roadmap
Near Term
• Monthly skill demand snapshots
• Public dataset v1 release
• First production-style dashboard
Long Term
• Trend modeling across skill demand over time
• Compensation band movement tracking
• Market demand shift detection

Status
Active build phase.
The dataset and automation pipeline are expanding weekly as ingestion volume increases and new reliability features are added.


