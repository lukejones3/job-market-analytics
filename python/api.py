"""
Job Market Analytics API
FastAPI application serving labor market intelligence data.

Deploy on your droplet:
    pip install fastapi uvicorn python-jose passlib python-multipart
    uvicorn api:app --host 0.0.0.0 --port 8000 --workers 4

Generate a key:
    python3 api.py --generate-key "Acme Recruiting" acme@recruiting.com pro
"""

import hashlib
import hmac
import logging
import os
import secrets
import time
import argparse
from datetime import datetime, timezone, date
from typing import Optional, List
from contextlib import asynccontextmanager

import psycopg2
import psycopg2.pool
import stripe
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Depends, Request, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

# ── DB config ─────────────────────────────────────────────────────────────────
DB_CONFIG = dict(
    host=os.getenv("PGHOST"),
    port=int(os.getenv("PGPORT", 5432)),
    dbname=os.getenv("PGDATABASE", "job_analytics"),
    user=os.getenv("PGUSER"),
    password=os.getenv("PGPASSWORD"),
)

# ── Stripe config ────────────────────────────────────────────────────────────
STRIPE_SECRET_KEY    = os.getenv("STRIPE_SECRET_KEY", "")
STRIPE_WEBHOOK_SECRET = os.getenv("STRIPE_WEBHOOK_SECRET", "")
STRIPE_PRICE_ID      = "price_1TP72k5EYUntcUuPzCr6ym84"

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ── Connection pool ───────────────────────────────────────────────────────────
pool: psycopg2.pool.ThreadedConnectionPool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = psycopg2.pool.ThreadedConnectionPool(2, 10, **DB_CONFIG)
    log.info("DB pool initialized")
    yield
    pool.closeall()
    log.info("DB pool closed")

def get_conn():
    conn = pool.getconn()
    try:
        yield conn
    finally:
        pool.putconn(conn)

# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Job Market Analytics API",
    description="Labor market intelligence for data & ML hiring. Nightly-updated from 6 ATS sources.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Role family SQL ───────────────────────────────────────────────────────────
ROLE_FAMILY_SQL = """
CASE
    WHEN lower(r.role_name) ~ 'director|head of|vp |vice president|chief data|chief analytics|manager, data|manager, analytics|manager, ml|manager, machine|data science manager|analytics manager' THEN 'Leadership'
    WHEN lower(r.role_name) ~ 'data engineer|analytics engineer|data architect|data platform engineer|mlops|data science engineer' THEN 'Data Engineer'
    WHEN lower(r.role_name) ~ 'machine learning|ml engineer|ai/ml|computer vision|applied scientist|applied researcher|ai researcher' THEN 'ML Engineer'
    WHEN lower(r.role_name) ~ 'ai engineer|applied ai|llm engineer|ai specialist' THEN 'AI Engineer'
    WHEN lower(r.role_name) ~ 'data scientist|quantitative researcher|research scientist|data science' THEN 'Data Scientist'
    WHEN lower(r.role_name) ~ 'data analyst|business analyst|bi analyst|financial analyst|fp&a|reporting analyst|business intelligence|product analyst|marketing analyst|fraud analyst|growth analyst|risk analyst|pricing analyst|compensation analyst|credit analyst|actuarial|clinical data|data quality analyst|analytics consultant|analytics lead|data analytics' THEN 'Data Analyst'
    WHEN lower(r.role_name) ~ 'sales op|revenue op|marketing op|operations analyst|operations manager|operations lead|operations specialist|operations director|revops' THEN 'Revenue/Ops'
    ELSE 'Other'
END
"""

# ── Auth ──────────────────────────────────────────────────────────────────────
TIER_LIMITS = {
    "free":       100,
    "pro":        2000,
    "enterprise": 50000,
}

def hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()

def get_key_prefix(raw_key: str) -> str:
    return raw_key[:12]

async def verify_api_key(request: Request, conn=Depends(get_conn)) -> dict:
    raw_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if not raw_key:
        raise HTTPException(status_code=401, detail="API key required. Pass as X-API-Key header or api_key query param.")

    key_hash = hash_key(raw_key)
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT key_id, client_name, tier, rate_limit_day,
               requests_today, total_requests, last_reset_date, active
        FROM api_keys
        WHERE api_key_hash = %s
    """, (key_hash,))
    key = cur.fetchone()

    if not key:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    if not key["active"]:
        raise HTTPException(status_code=403, detail="API key has been deactivated.")

    # Reset daily counter if new day
    today = date.today()
    if key["last_reset_date"] != today:
        cur.execute("""
            UPDATE api_keys SET requests_today = 0, last_reset_date = %s WHERE key_id = %s
        """, (today, key["key_id"]))
        conn.commit()
        key["requests_today"] = 0

    # Rate limit check
    if key["requests_today"] >= key["rate_limit_day"]:
        raise HTTPException(
            status_code=429,
            detail=f"Daily rate limit of {key['rate_limit_day']} requests reached. Resets at midnight UTC."
        )

    # Increment counters
    cur.execute("""
        UPDATE api_keys
        SET requests_today = requests_today + 1,
            total_requests  = total_requests + 1,
            last_used_at    = now()
        WHERE key_id = %s
    """, (key["key_id"],))
    conn.commit()

    return dict(key)

# ── Usage logging middleware ──────────────────────────────────────────────────
@app.middleware("http")
async def log_requests(request: Request, call_next):
    start = time.time()
    response = await call_next(request)
    ms = int((time.time() - start) * 1000)
    log.info(f"{request.method} {request.url.path} → {response.status_code} ({ms}ms)")
    return response

# ── Health ────────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}

@app.get("/", tags=["System"])
def root():
    return {
        "name": "Job Market Analytics API",
        "version": "1.0.0",
        "docs": "/docs",
        "endpoints": [
            "GET /v1/market/overview",
            "GET /v1/market/roles",
            "GET /v1/market/skills",
            "GET /v1/market/sectors",
            "GET /v1/market/ghost-index",
            "GET /v1/companies",
            "GET /v1/companies/{slug}",
            "GET /v1/companies/{slug}/roles",
            "GET /v1/companies/{slug}/skills",
            "GET /v1/roles",
            "GET /v1/roles/{job_id}",
            "GET /v1/me",
        ]
    }

# ── Me ────────────────────────────────────────────────────────────────────────
@app.get("/v1/me", tags=["Auth"])
def me(key: dict = Depends(verify_api_key)):
    return {
        "client_name":    key["client_name"],
        "tier":           key["tier"],
        "rate_limit_day": key["rate_limit_day"],
        "requests_today": key["requests_today"],
        "total_requests": key["total_requests"],
    }

# ── Market Overview ───────────────────────────────────────────────────────────
@app.get("/v1/market/overview", tags=["Market Intelligence"])
def market_overview(key: dict = Depends(verify_api_key), conn=Depends(get_conn)):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT
            COUNT(*)                                                                    as total_active_roles,
            COUNT(DISTINCT jp.company_id)                                               as companies_hiring,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL
                THEN 1.0 ELSE 0.0 END) * 100, 1)                                       as salary_transparency_pct,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))           as avg_max_salary,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))           as median_max_salary,
            ROUND(AVG(jh.honesty_score), 1)                                             as avg_honesty_score,
            MAX(jp.ingested_at)                                                         as last_updated
        FROM job_postings jp
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE jp.data_tier = 1 AND jp.status = 'raw'
    """)
    overview = dict(cur.fetchone())

    cur.execute("""
        SELECT ghost_tier, COUNT(*) as count,
            ROUND(AVG(ghost_probability)::numeric, 1) as avg_probability
        FROM vw_ghost_job_index
        GROUP BY ghost_tier
        ORDER BY avg_probability DESC
    """)
    ghost = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT source, COUNT(*) as active_roles
        FROM job_postings
        WHERE data_tier = 1 AND status = 'raw'
        GROUP BY source ORDER BY active_roles DESC
    """)
    sources = [dict(r) for r in cur.fetchall()]

    return {
        "overview":      overview,
        "ghost_index":   ghost,
        "sources":       sources,
        "generated_at":  datetime.now(timezone.utc).isoformat(),
    }

# ── Market Roles ──────────────────────────────────────────────────────────────
@app.get("/v1/market/roles", tags=["Market Intelligence"])
def market_roles(
    family: Optional[str] = Query(None, description="Filter by role family e.g. 'Data Engineer'"),
    experience: Optional[str] = Query(None, description="Filter by level: entry, associate, mid, senior"),
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    cur = conn.cursor(cursor_factory=RealDictCursor)

    where = ["jp.data_tier = 1", "jp.status = 'raw'"]
    params = []

    if family:
        where.append(f"({ROLE_FAMILY_SQL}) = %s")
        params.append(family)
    if experience:
        where.append("jp.experience_level = %s")
        params.append(experience)

    cur.execute(f"""
        SELECT
            {ROLE_FAMILY_SQL}                                                           as role_family,
            jp.experience_level,
            COUNT(DISTINCT jp.job_id)                                                   as active_roles,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))           as avg_max_salary,
            ROUND(AVG(jp.salary_min_annual)
                FILTER (WHERE jp.salary_min_annual BETWEEN 30000 AND 500000))           as avg_min_salary,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))           as median_max_salary,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL
                THEN 1.0 ELSE 0.0 END) * 100, 1)                                       as transparency_pct,
            ROUND(AVG(CASE WHEN jp.workplace_type = 'remote'
                THEN 1.0 ELSE 0.0 END) * 100)                                          as remote_pct
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        WHERE {' AND '.join(where)}
        GROUP BY 1, 2
        ORDER BY 1, CASE jp.experience_level
            WHEN 'entry' THEN 1 WHEN 'associate' THEN 2
            WHEN 'mid' THEN 3 WHEN 'senior' THEN 4 ELSE 5 END
    """, params)

    return {
        "filters":        {"family": family, "experience": experience},
        "results":        [dict(r) for r in cur.fetchall()],
        "generated_at":   datetime.now(timezone.utc).isoformat(),
    }

# ── Market Skills ─────────────────────────────────────────────────────────────
@app.get("/v1/market/skills", tags=["Market Intelligence"])
def market_skills(
    family: Optional[str] = Query(None, description="Filter by role family"),
    min_jobs: int = Query(20, description="Minimum jobs requiring skill"),
    limit: int = Query(50, le=200),
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    cur = conn.cursor(cursor_factory=RealDictCursor)

    family_filter = f"AND ({ROLE_FAMILY_SQL}) = %(family)s" if family else ""

    cur.execute(f"""
        SELECT s.skill_name,
            COUNT(DISTINCT jp.job_id)                                                   as jobs_requiring,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))           as avg_max_salary,
            ROUND((AVG(jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000) -
                (SELECT AVG(salary_max_annual) FROM job_postings
                 WHERE data_tier=1 AND status='raw'
                   AND salary_max_annual BETWEEN 50000 AND 500000)) /
                NULLIF((SELECT AVG(salary_max_annual) FROM job_postings
                 WHERE data_tier=1 AND status='raw'
                   AND salary_max_annual BETWEEN 50000 AND 500000), 0) * 100, 1)        as salary_premium_pct,
            ROUND(AVG(CASE WHEN js.skill_priority = 'required'
                THEN 1.0 ELSE 0.0 END) * 100)                                          as required_pct
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        JOIN job_postings jp ON jp.job_id = js.job_id
        JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.data_tier = 1 AND jp.status = 'raw'
          AND s.difficulty_relevant = true
          {family_filter}
        GROUP BY s.skill_name
        HAVING COUNT(DISTINCT jp.job_id) >= %(min_jobs)s
        ORDER BY jobs_requiring DESC
        LIMIT %(limit)s
    """, {"family": family, "min_jobs": min_jobs, "limit": limit})

    return {
        "filters":      {"family": family, "min_jobs": min_jobs},
        "results":      [dict(r) for r in cur.fetchall()],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Market Sectors ────────────────────────────────────────────────────────────
@app.get("/v1/market/sectors", tags=["Market Intelligence"])
def market_sectors(key: dict = Depends(verify_api_key), conn=Depends(get_conn)):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT c.sector,
            COUNT(DISTINCT jp.job_id)                                                   as active_roles,
            COUNT(DISTINCT c.company_id)                                                as companies,
            ROUND(AVG(jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))           as avg_max_salary,
            ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY jp.salary_max_annual)
                FILTER (WHERE jp.salary_max_annual BETWEEN 50000 AND 500000))           as median_max_salary,
            ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL
                THEN 1.0 ELSE 0.0 END) * 100, 1)                                       as transparency_pct,
            ROUND(AVG(jh.honesty_score), 1)                                             as avg_honesty_score
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE jp.data_tier = 1 AND jp.status = 'raw' AND c.sector IS NOT NULL
        GROUP BY c.sector
        HAVING COUNT(DISTINCT jp.job_id) >= 10
        ORDER BY active_roles DESC
    """)
    return {
        "results":      [dict(r) for r in cur.fetchall()],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Ghost Index ───────────────────────────────────────────────────────────────
@app.get("/v1/market/ghost-index", tags=["Market Intelligence"])
def ghost_index(key: dict = Depends(verify_api_key), conn=Depends(get_conn)):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT ghost_tier,
            COUNT(*)                                            as job_count,
            ROUND(AVG(ghost_probability)::numeric, 1)          as avg_probability,
            ROUND(MIN(ghost_probability)::numeric, 1)          as min_probability,
            ROUND(MAX(ghost_probability)::numeric, 1)          as max_probability
        FROM vw_ghost_job_index
        GROUP BY ghost_tier
        ORDER BY avg_probability DESC
    """)
    tiers = [dict(r) for r in cur.fetchall()]

    cur.execute("""
        SELECT g.job_id, r.role_name, c.company_name, c.sector,
            ROUND(g.ghost_probability::numeric, 1) as ghost_probability,
            g.ghost_tier
        FROM vw_ghost_job_index g
        JOIN job_postings jp ON jp.job_id = g.job_id
        LEFT JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN companies c ON c.company_id = jp.company_id
        WHERE g.ghost_tier = 'high'
        ORDER BY g.ghost_probability DESC
        LIMIT 20
    """)
    top_ghost = [dict(r) for r in cur.fetchall()]

    return {
        "summary":      tiers,
        "top_ghost_roles": top_ghost,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Companies List ────────────────────────────────────────────────────────────
@app.get("/v1/companies", tags=["Company Intelligence"])
def list_companies(
    sector: Optional[str]   = Query(None),
    min_roles: int          = Query(3),
    sort_by: str            = Query("active_roles", description="active_roles | difficulty_score | avg_ghost_probability | transparency_pct"),
    limit: int              = Query(50, le=200),
    offset: int             = Query(0),
    key: dict               = Depends(verify_api_key),
    conn                    = Depends(get_conn),
):
    valid_sorts = {"active_roles", "difficulty_score", "avg_ghost_probability", "transparency_pct", "avg_max_salary", "avg_honesty_score"}
    if sort_by not in valid_sorts:
        raise HTTPException(status_code=400, detail=f"sort_by must be one of: {', '.join(valid_sorts)}")

    cur = conn.cursor(cursor_factory=RealDictCursor)
    where = ["active_roles >= %s"]
    params = [min_roles]

    if sector:
        where.append("sector = %s")
        params.append(sector)

    params += [limit, offset]

    cur.execute(f"""
        SELECT company_name, sector, active_roles, avg_max_salary,
            transparency_pct, avg_honesty_score, difficulty_score,
            rarity_score, complexity_score, salary_below_score,
            avg_ghost_probability, ghost_rate_pct,
            primary_level, primary_workplace,
            left(top_skills, 200) as top_skills
        FROM analytics_analytics.mart_company_scorecard
        WHERE {' AND '.join(where)}
        ORDER BY {sort_by} DESC NULLS LAST
        LIMIT %s OFFSET %s
    """, params)

    results = [dict(r) for r in cur.fetchall()]

    cur.execute(f"""
        SELECT COUNT(*) as total
        FROM analytics_analytics.mart_company_scorecard
        WHERE {' AND '.join(where[:-0] if where else ['1=1'])}
    """, params[:-2])
    total = cur.fetchone()["total"]

    return {
        "total":        total,
        "limit":        limit,
        "offset":       offset,
        "results":      results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Company Detail ────────────────────────────────────────────────────────────
@app.get("/v1/companies/{slug}", tags=["Company Intelligence"])
def company_detail(
    slug: str,
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT *
        FROM analytics_analytics.mart_company_scorecard
        WHERE lower(replace(company_name, ' ', '')) = lower(replace(%s, ' ', ''))
           OR lower(company_name) LIKE lower(%s)
        LIMIT 1
    """, (slug, f"%{slug}%"))

    company = cur.fetchone()
    if not company:
        raise HTTPException(status_code=404, detail=f"Company '{slug}' not found.")

    company = dict(company)

    # Action flags
    flags = []
    if company.get("salary_below_score") and company["salary_below_score"] > 30:
        flags.append({"type": "underpaying", "message": f"Underpaying ~{int(company['salary_below_score'])}% vs sector median"})
    if company.get("complexity_score") and company["complexity_score"] > 70:
        flags.append({"type": "overspecified", "message": "Over-specified role requirements"})
    if company.get("avg_ghost_probability") and company["avg_ghost_probability"] > 60:
        flags.append({"type": "ghost_risk", "message": f"Ghost job risk {int(company['avg_ghost_probability'])}%"})
    if company.get("transparency_pct") == 0:
        flags.append({"type": "opaque", "message": "0% salary transparency"})
    if company.get("rarity_score") and company["rarity_score"] > 15:
        flags.append({"type": "rare_skills", "message": "Niche skill requirements"})

    company["action_flags"] = flags
    company["generated_at"] = datetime.now(timezone.utc).isoformat()

    return company

# ── Company Roles ─────────────────────────────────────────────────────────────
@app.get("/v1/companies/{slug}/roles", tags=["Company Intelligence"])
def company_roles(
    slug: str,
    experience: Optional[str] = Query(None),
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # Get company_id first
    cur.execute("""
        SELECT company_id FROM companies
        WHERE lower(company_name) LIKE lower(%s)
        LIMIT 1
    """, (f"%{slug}%",))
    co = cur.fetchone()
    if not co:
        raise HTTPException(status_code=404, detail=f"Company '{slug}' not found.")

    where = ["jp.company_id = %s", "jp.data_tier = 1", "jp.status = 'raw'"]
    params = [co["company_id"]]

    if experience:
        where.append("jp.experience_level = %s")
        params.append(experience)

    cur.execute(f"""
        SELECT jp.job_id, r.role_name,
            {ROLE_FAMILY_SQL}                                                           as role_family,
            jp.experience_level, jp.workplace_type, jp.employment_type,
            jp.salary_min_annual, jp.salary_max_annual, jp.salary_period,
            jp.job_url, jp.posted_date, jp.ingested_at,
            l.location, l.state,
            g.ghost_probability, g.ghost_tier,
            jh.honesty_score
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN locations l ON l.location_id = jp.location_id
        LEFT JOIN vw_ghost_job_index g ON g.job_id = jp.job_id
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE {' AND '.join(where)}
        ORDER BY jp.ingested_at DESC
    """, params)

    return {
        "company": slug,
        "count":   cur.rowcount,
        "results": [dict(r) for r in cur.fetchall()],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Company Skills ────────────────────────────────────────────────────────────
@app.get("/v1/companies/{slug}/skills", tags=["Company Intelligence"])
def company_skills(
    slug: str,
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT company_id FROM companies
        WHERE lower(company_name) LIKE lower(%s) LIMIT 1
    """, (f"%{slug}%",))
    co = cur.fetchone()
    if not co:
        raise HTTPException(status_code=404, detail=f"Company '{slug}' not found.")

    cur.execute("""
        SELECT s.skill_name, js.skill_priority,
            COUNT(DISTINCT jp.job_id) as job_count,
            ROUND(COUNT(DISTINCT jp.job_id)::numeric /
                NULLIF((SELECT COUNT(*) FROM job_postings
                 WHERE company_id = %s AND data_tier=1 AND status='raw'), 0) * 100, 1) as pct_of_roles
        FROM job_postings jp
        JOIN job_skills js ON js.job_id = jp.job_id
        JOIN skills s ON s.skill_id = js.skill_id
        WHERE jp.company_id = %s AND jp.data_tier = 1 AND jp.status = 'raw'
        GROUP BY s.skill_name, js.skill_priority
        ORDER BY job_count DESC
        LIMIT 30
    """, (co["company_id"], co["company_id"]))

    return {
        "company":      slug,
        "results":      [dict(r) for r in cur.fetchall()],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Roles List ────────────────────────────────────────────────────────────────
@app.get("/v1/roles", tags=["Role Intelligence"])
def list_roles(
    family:     Optional[str] = Query(None),
    experience: Optional[str] = Query(None),
    sector:     Optional[str] = Query(None),
    remote:     Optional[bool]= Query(None),
    min_salary: Optional[int] = Query(None),
    max_salary: Optional[int] = Query(None),
    ghost_tier: Optional[str] = Query(None, description="fresh | low | medium | high"),
    limit:      int           = Query(50, le=200),
    offset:     int           = Query(0),
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    cur = conn.cursor(cursor_factory=RealDictCursor)

    where = ["jp.data_tier = 1", "jp.status = 'raw'"]
    params = {}

    if family:
        where.append(f"({ROLE_FAMILY_SQL}) = %(family)s")
        params["family"] = family
    if experience:
        where.append("jp.experience_level = %(experience)s")
        params["experience"] = experience
    if sector:
        where.append("c.sector = %(sector)s")
        params["sector"] = sector
    if remote is not None:
        where.append("jp.workplace_type = %(workplace)s")
        params["workplace"] = "remote" if remote else "onsite"
    if min_salary:
        where.append("jp.salary_max_annual >= %(min_salary)s")
        params["min_salary"] = min_salary
    if max_salary:
        where.append("jp.salary_max_annual <= %(max_salary)s")
        params["max_salary"] = max_salary
    if ghost_tier:
        where.append("g.ghost_tier = %(ghost_tier)s")
        params["ghost_tier"] = ghost_tier

    params["limit"]  = limit
    params["offset"] = offset

    cur.execute(f"""
        SELECT jp.job_id, r.role_name,
            {ROLE_FAMILY_SQL}                   as role_family,
            c.company_name, c.sector,
            jp.experience_level, jp.workplace_type,
            jp.salary_min_annual, jp.salary_max_annual,
            jp.job_url, jp.posted_date,
            l.location, l.state,
            g.ghost_probability, g.ghost_tier,
            jh.honesty_score
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN companies c ON c.company_id = jp.company_id
        LEFT JOIN locations l ON l.location_id = jp.location_id
        LEFT JOIN vw_ghost_job_index g ON g.job_id = jp.job_id
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE {' AND '.join(where)}
        ORDER BY jp.ingested_at DESC
        LIMIT %(limit)s OFFSET %(offset)s
    """, params)

    results = [dict(r) for r in cur.fetchall()]
    return {
        "filters":      {k: v for k, v in params.items() if k not in ("limit","offset")},
        "count":        len(results),
        "limit":        limit,
        "offset":       offset,
        "results":      results,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

# ── Role Detail ───────────────────────────────────────────────────────────────
@app.get("/v1/roles/{job_id}", tags=["Role Intelligence"])
def role_detail(
    job_id: str,
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute("""
        SELECT jp.job_id, r.role_name,
            c.company_name, c.sector,
            jp.experience_level, jp.workplace_type, jp.employment_type,
            jp.salary_min_annual, jp.salary_max_annual, jp.salary_period,
            jp.job_url, jp.posted_date, jp.ingested_at, jp.source,
            l.location, l.state,
            g.ghost_probability, g.ghost_tier,
            jh.honesty_score
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN companies c ON c.company_id = jp.company_id
        LEFT JOIN locations l ON l.location_id = jp.location_id
        LEFT JOIN vw_ghost_job_index g ON g.job_id = jp.job_id
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE jp.job_id = %s AND jp.data_tier = 1
    """, (job_id,))

    role = cur.fetchone()
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{job_id}' not found.")

    role = dict(role)

    # Skills for this role
    cur.execute("""
        SELECT s.skill_name, js.skill_priority
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        WHERE js.job_id = %s
        ORDER BY CASE js.skill_priority WHEN 'required' THEN 1 ELSE 2 END, s.skill_name
    """, (job_id,))
    role["skills"] = [dict(r) for r in cur.fetchall()]
    role["generated_at"] = datetime.now(timezone.utc).isoformat()

    return role

# ── Key generation CLI ────────────────────────────────────────────────────────
def generate_key(client_name: str, client_email: str, tier: str = "free"):
    import psycopg2
    conn = psycopg2.connect(**DB_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()

    raw_key = "jma_" + secrets.token_urlsafe(32)
    key_hash = hash_key(raw_key)
    prefix = get_key_prefix(raw_key)
    key_id = "K" + secrets.token_hex(8)
    rate_limit = TIER_LIMITS.get(tier, 100)

    cur.execute("""
        INSERT INTO api_keys
            (key_id, client_name, client_email, api_key_hash, api_key_prefix, tier, rate_limit_day)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
    """, (key_id, client_name, client_email, key_hash, prefix, tier, rate_limit))

    print(f"\n✅ API key generated")
    print(f"   Client:     {client_name}")
    print(f"   Email:      {client_email}")
    print(f"   Tier:       {tier} ({rate_limit} req/day)")
    print(f"   Key ID:     {key_id}")
    print(f"   API Key:    {raw_key}")
    print(f"\n⚠️  Save this key — it cannot be recovered (only the hash is stored)\n")
    conn.close()



# ── Stripe Webhook ────────────────────────────────────────────────────────────
@app.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")

    try:
        event = stripe.Webhook.construct_event(
            payload, sig_header, STRIPE_WEBHOOK_SECRET
        )
    except Exception as e:
        log.warning(f"Stripe webhook error: {e}")
        raise HTTPException(status_code=400, detail=str(e))

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session.get("customer_details", {}).get("email", "")
        customer_name  = session.get("customer_details", {}).get("name", "unknown")

        if customer_email:
            # Generate access token
            raw_key   = secrets.token_urlsafe(32)
            key_hash  = hashlib.sha256(raw_key.encode()).hexdigest()
            key_prefix = raw_key[:8]

            conn = pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO api_keys
                            (client_name, client_email, api_key_hash, api_key_prefix,
                             tier, active, created_at)
                        VALUES (%s, %s, %s, %s, 'pro', true, NOW())
                    """, (customer_name, customer_email, key_hash, key_prefix))
                    conn.commit()
                log.info(f"New subscriber: {customer_email} — token prefix: {key_prefix}")

                # Send welcome email via Gmail
                import smtplib
                from email.mime.text import MIMEText
                from email.mime.multipart import MIMEMultipart

                access_url = f"https://job-market-analytics-nyz8zrrujh8bafgniqhjyw.streamlit.app/?token={raw_key}"

                msg = MIMEMultipart("alternative")
                msg["Subject"] = "Your DataHiringIQ Access"
                msg["From"]    = "jones31luke@gmail.com"
                msg["To"]      = customer_email

                html = f"""
                <div style="font-family:monospace;background:#080810;color:#d4d4d8;padding:32px;max-width:560px">
                    <div style="font-size:1.5rem;color:#e2ff5d;margin-bottom:8px">DATAHIRINGIQ</div>
                    <p>Hi {customer_name},</p>
                    <p>Your recruiter intelligence feed is ready. Click below to access:</p>
                    <a href="{access_url}"
                       style="display:inline-block;background:#e2ff5d;color:#080810;
                              padding:12px 24px;text-decoration:none;font-weight:bold;
                              margin:16px 0">
                        Access Your Feed →
                    </a>
                    <p style="color:#666;font-size:0.8rem">
                        Bookmark this link — it's your personal access URL.<br>
                        Questions? Reply to this email.
                    </p>
                    <p style="color:#444;font-size:0.75rem">datahiringiq.com</p>
                </div>
                """
                msg.attach(MIMEText(html, "html"))

                gmail_pass = os.getenv("GMAIL_APP_PASSWORD", "")
                if gmail_pass:
                    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as s:
                        s.login("jones31luke@gmail.com", gmail_pass)
                        s.sendmail("jones31luke@gmail.com", customer_email, msg.as_string())
                    log.info(f"Welcome email sent to {customer_email}")

            except Exception as e:
                conn.rollback()
                log.error(f"Error provisioning access: {e}")
            finally:
                pool.putconn(conn)

    elif event["type"] == "customer.subscription.deleted":
        # Deactivate key when subscription cancelled
        session = event["data"]["object"]
        customer_id = session.get("customer")
        try:
            customer = stripe.Customer.retrieve(customer_id)
            email = customer.get("email", "")
            if email:
                conn = pool.getconn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE api_keys SET active=false WHERE client_email=%s",
                            (email,)
                        )
                        conn.commit()
                    log.info(f"Deactivated access for {email}")
                finally:
                    pool.putconn(conn)
        except Exception as e:
            log.error(f"Error deactivating: {e}")

    return {"status": "ok"}


# ── Stripe Checkout Session ───────────────────────────────────────────────────
@app.post("/stripe/create-checkout")
async def create_checkout(request: Request):
    body = await request.json()
    email = body.get("email", "")
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            customer_email=email or None,
            success_url="https://job-market-analytics-nyz8zrrujh8bafgniqhjyw.streamlit.app/?payment=success",
            cancel_url="https://job-market-analytics-nyz8zrrujh8bafgniqhjyw.streamlit.app/",
        )
        return {"checkout_url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--generate-key", nargs=3,
                        metavar=("CLIENT_NAME", "EMAIL", "TIER"),
                        help="Generate a new API key")
    args = parser.parse_args()

    if args.generate_key:
        generate_key(*args.generate_key)
