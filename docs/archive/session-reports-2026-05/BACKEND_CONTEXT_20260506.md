# job-market-analytics — Backend Context (May 6, 2026)

## What this repo is

The data pipeline + FastAPI backend for Lander (formerly DataHiringIQ).

- **Hosted at:** DigitalOcean droplet `208.68.38.249`
- **Production folder:** `/opt/job-market-analytics/`
- **Service:** `jma-api.service` (systemd) running uvicorn
- **Public URL:** https://api.datahiringiq.com (will rebrand to api.landerjob.com Sunday)
- **Frontend that consumes this:** the Lander Next.js app at `~/github/lander/` (separate repo)

## Tech stack

- **Language:** Python 3.12
- **Framework:** FastAPI + uvicorn
- **DB:** PostgreSQL 16 (also on the droplet, port 5432)
- **Virtual env:** `/opt/job-market-analytics/.venv/`
- **Service config:** `deploy/systemd/jma-api.service` (in this repo for reference; actual file is at `/etc/systemd/system/jma-api.service` on the droplet)
- **ExecStart:** `/opt/job-market-analytics/.venv/bin/uvicorn python.api:app --host 0.0.0.0 --port 8000 --workers 2`

## Deployment workflow (what we use NOW)

```
1. Edit code locally in this repo (Cursor)
2. git commit + git push origin main
3. SSH to droplet: ssh root@208.68.38.249
4. cd /opt/job-market-analytics && git pull
5. systemctl restart jma-api
6. Verify: journalctl -u jma-api --since "30 sec ago" --no-pager | tail -10
```

NEVER edit live files on the droplet. Everything goes through this repo.

## Repo structure

```
python/
├── api.py                    # FastAPI app, all endpoints (1228 lines)
├── ingest_jobs.py            # Daily cron job that ingests from ATS sources
├── enrich_job_postings.py    # Parses descriptions, extracts skills/salary
├── resume/                   # Resume parsing module
│   ├── __init__.py
│   ├── parser.py             # parse_resume(file_bytes, filename) -> str
│   ├── skill_extractor.py    # extract_skills(text, db_cursor) -> dict
│   ├── matcher.py            # match_jobs(...), find_skill_gaps(...)
│   └── exp_inferrer.py       # infer_experience_level(text) -> str
├── nurture_email.py          # Day-3 nurture email cron
├── morning_report.py         # Daily ingestion report
└── ...
sql/                          # SQL schema files
dbt/                          # dbt models for analytics
scripts/                      # One-off operational scripts
deploy/
└── systemd/
    └── jma-api.service       # Reference copy of the systemd unit
requirements.txt
```

## Database schema (selected highlights)

### Job data
- `job_postings` (~22K rows total, ~5,900 active Tier 1) — main job table
- `companies` — 1,000+ companies, 28 sectors classified
- `roles`, `skills`, `job_skills` (bridge), `locations`
- `company_contacts` (77% coverage) — hiring manager LinkedIn data
- `skill_aliases` — 274 aliases mapping common terms to skill_ids

### User data
- `free_signups` — email signups (111+ as of May 6)
- `api_keys` — auth tokens with tier (free/pro)
- `user_applied_jobs` (NEW, May 6) — tracks Lander apply clicks
- `user_saved_jobs` (NEW, May 6) — tracks Lander bookmarks
- `resumes` — only test data so far

### Analytics (dbt-built)
- `analytics_analytics.mart_skill_demand`
- `analytics_analytics.mart_company_scorecard`
- `analytics_analytics.mart_ghost_job_index`
- `analytics_analytics.mart_honesty_scores`
- ... and more

## Existing endpoints (api.py)

```
GET  /health
GET  /
GET  /v1/me                       (auth)
GET  /v1/market/overview          (auth)
GET  /v1/market/roles             (auth)
GET  /v1/market/skills            (auth)
GET  /v1/market/sectors           (auth)
GET  /v1/market/ghost-index       (auth)
GET  /v1/companies                (auth)
GET  /v1/companies/{slug}         (auth)
GET  /v1/companies/{slug}/roles   (auth)
POST /auth/free-signup            (public, rate-limited 3/min)
GET  /auth/verify                 (public, magic link)
POST /stripe/webhook              (Stripe-only)
POST /stripe/create-checkout      (public)
POST /v1/resume/upload            (auth, NEW May 6 — CURRENTLY BROKEN)
```

## Auth pattern

The `verify_api_key` dependency reads the `X-API-Key` header (or `?api_key=` query param), hashes it, looks up in `api_keys` table, returns dict with `key_id`, `tier`, `client_name`, etc.

For the Lander frontend's preview/dev mode, we wrapped it with `verify_api_key_or_preview` which special-cases `X-API-Key: preview` to return a fake free-tier user.

## CURRENT BUG (the reason this Cursor session is happening)

The `POST /v1/resume/upload` endpoint at the bottom of `api.py` exists but is broken. Inside the endpoint, we pass `conn` (a psycopg2 connection) to `extract_skills`, `match_jobs`, and `find_skill_gaps`. But those functions expect a `db_cursor`, NOT a connection.

**Confirmed in isolation testing:** when called with a proper RealDictCursor, `extract_skills` works correctly (found 6 skills in test data).

**The fix needed:**

1. Inside `upload_resume_v1`, create a cursor from the connection:
   ```python
   cur = conn.cursor(cursor_factory=RealDictCursor)
   ```

2. Pass the cursor (not the conn) to:
   - `extract_skills(text, cur)`
   - `match_jobs(resume_skills=skills, exp_level=exp_level, salary_floor=0, db_cursor=cur, top_n=50)`  ← note: param is `resume_skills` not `skills`, and `db_cursor` not `conn`
   - `find_skill_gaps(resume_skills=skills, exp_level=exp_level, db_cursor=cur, top_n=5)`

3. `infer_experience_level(text)` does NOT need a cursor (just text).

4. `parse_resume(file_bytes, filename)` does NOT need a cursor.

5. Properly close the cursor in a `finally` block.

The function signatures (verified from the actual matcher.py):

```python
match_jobs(
    resume_skills: Dict[str, Dict[str, Any]],
    exp_level: str,
    salary_floor: Optional[float],
    db_cursor,
    top_n: int = 50,
) -> List[Dict[str, Any]]

find_skill_gaps(
    resume_skills: Dict[str, Dict[str, Any]],
    exp_level: str,
    db_cursor,
    top_n: int = 5,
    min_unlock_jobs: int = 3,
) -> List[Dict[str, Any]]
```

## What NOT to do

- Do NOT edit live files on the droplet via SSH/sed
- Do NOT install packages directly with pip on the droplet — add to `requirements.txt`, push, then pull + install on droplet
- Do NOT skip running with PYTHONPATH when testing locally — the `python` package only resolves when CWD is `/opt/job-market-analytics` OR PYTHONPATH includes it
- Do NOT use system Python — always use `/opt/job-market-analytics/.venv/bin/python` on the droplet

## Testing locally

To run the test script that verifies extract_skills works:

```bash
cd ~/github/job-market-analytics
PYTHONPATH=. python -c "from python.resume import extract_skills; print('ok')"
```

For DB-backed tests, you need an SSH tunnel to the production DB:

```bash
ssh -L 5432:127.0.0.1:5432 root@208.68.38.249 -N
```

Leave that tunnel running in another terminal, then DB connections to `127.0.0.1:5432` work.
