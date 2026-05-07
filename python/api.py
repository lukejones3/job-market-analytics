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
from fastapi import FastAPI, HTTPException, Depends, Request, Query, BackgroundTasks, File, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
from typing import Any, Dict, List
from python.resume import parse_resume, extract_skills, infer_experience_level, match_jobs, find_skill_gaps
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware  # SLOWAPI_MIDDLEWARE_v1
from python.email_templates import (
    FREE_SIGNUP_SUBJECT, free_signup_html, free_signup_plain,
    PRO_WELCOME_SUBJECT, pro_welcome_html, pro_welcome_plain,
)

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
STRIPE_PRICE_ID      = "price_1TS0Qz5EYUntcUuPCtc54NAm"

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

# ── Rate limiter (AI_PROTECTION_v1, CF_REAL_IP_v1) ───────────────────────────
def _get_real_client_ip(request: Request) -> str:
    """Get the real client IP, preferring Cloudflare's CF-Connecting-IP header.

    When traffic goes through Cloudflare, the immediate connection comes from a
    Cloudflare edge IP. The real user IP is in CF-Connecting-IP. Falls back to
    X-Forwarded-For, then to the direct connection IP for direct/local requests.
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        log.debug(f"Rate limit key (CF-Connecting-IP): {cf_ip}")
        return cf_ip
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client = forwarded.split(",")[0].strip()
        log.debug(f"Rate limit key (X-Forwarded-For): {client}")
        return client
    fallback = get_remote_address(request)
    log.debug(f"Rate limit key (fallback get_remote_address): {fallback}")
    return fallback

limiter = Limiter(key_func=_get_real_client_ip)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)  # SLOWAPI_MIDDLEWARE_v1: required for reliable rate limiting

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

    # WEBHOOK_ATTR_ACCESS_v1
    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]

        # Stripe objects support attribute access via __getattr__ (not .get()).
        # Use getattr with safe defaults for everything.
        def _safe_get(obj, key, default=None):
            try:
                v = getattr(obj, key, default)
                return v if v is not None else default
            except Exception:
                return default

        # Try customer_details.email first
        customer_email = ""
        customer_name = "unknown"
        cd = _safe_get(session, "customer_details", None)
        if cd is not None:
            customer_email = (_safe_get(cd, "email", "") or "").strip()
            customer_name = (_safe_get(cd, "name", "") or "").strip() or "unknown"

        # Fallback: top-level customer_email
        if not customer_email:
            customer_email = (_safe_get(session, "customer_email", "") or "").strip()

        # Last resort: fetch from customer object
        if not customer_email:
            customer_id = _safe_get(session, "customer", "")
            if customer_id:
                try:
                    cust = stripe.Customer.retrieve(customer_id)
                    customer_email = (_safe_get(cust, "email", "") or "").strip()
                    if customer_name == "unknown":
                        customer_name = (_safe_get(cust, "name", "") or "").strip() or "unknown"
                    log.info(f"webhook: fetched email from Customer obj: {customer_email or '(empty)'}")
                except Exception as _e:
                    log.warning(f"webhook: stripe.Customer.retrieve({customer_id}) failed: {_e}")

        log.info(f"webhook: extracted customer_email={customer_email!r} customer_name={customer_name!r}")

        if customer_email:
            # WEBHOOK_KEYID_FIX_v1: generate key_id (was missing — schema requires NOT NULL)
            raw_key    = secrets.token_urlsafe(32)
            key_hash   = hashlib.sha256(raw_key.encode()).hexdigest()
            key_prefix = raw_key[:8]
            key_id     = "K" + secrets.token_hex(8)

            conn = pool.getconn()
            try:
                with conn.cursor() as cur:
                    cur.execute("""
                        INSERT INTO api_keys
                            (key_id, client_name, client_email, api_key_hash, api_key_prefix,
                             tier, active, created_at, expires_at)
                        VALUES (%s, %s, %s, %s, %s, 'pro', true, NOW(), NOW() + INTERVAL '30 days')
                    """, (key_id, customer_name, customer_email, key_hash, key_prefix))
                    # FREEMIUM_BACKEND_v1: mark free_signups as upgraded
                    cur.execute("""
                        UPDATE free_signups
                        SET upgraded_to_paid_at = NOW()
                        WHERE email = %s
                    """, (customer_email,))
                    conn.commit()
                log.info(f"New subscriber: {customer_email} — token prefix: {key_prefix}")

                # WEBHOOK_EMAIL_LINESWAP_v1: send Pro welcome email via Resend
                lander_base = os.environ.get("LANDER_BASE_URL", "https://landerjob.com")
                access_url = f"{lander_base}/auth/verify?token={raw_key}"
                try:
                    import requests as _rq
                    _resend_key = os.getenv("RESEND_API_KEY", "")
                    _from = os.getenv("RESEND_FROM", "Lander <onboarding@resend.dev>")
                    _r = _rq.post(
                        "https://api.resend.com/emails",
                        headers={"Authorization": f"Bearer {_resend_key}", "Content-Type": "application/json"},
                        json={
                            "from": _from,
                            "to": [customer_email],
                            "subject": PRO_WELCOME_SUBJECT,
                            "html": pro_welcome_html(access_url),
                            "text": pro_welcome_plain(access_url),
                        },
                        timeout=10,
                    )
                    if _r.status_code in (200, 201):
                        log.info(f"webhook: Pro welcome email sent via Resend to {customer_email}")
                    else:
                        log.warning(f"webhook: Resend error {_r.status_code} for {customer_email}: {_r.text[:200]}")
                except Exception as _e:
                    log.warning(f"webhook: failed Pro welcome email to {customer_email}: {_e}")

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


# ── Free Signup (email gate) ──────────────────────────────────────────────────
# FREEMIUM_BACKEND_v1
import re as _re_email

EMAIL_RE = _re_email.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

# ── Disposable email domain blocklist (AI_PROTECTION_v1) ──────────────────────
DISPOSABLE_EMAIL_DOMAINS = frozenset({
    "10minutemail.com", "guerrillamail.com", "guerrillamail.net", "guerrillamail.org",
    "mailinator.com", "mailinator.net", "trashmail.com", "trashmail.net",
    "tempmail.com", "temp-mail.org", "tempmailo.com", "tempmail.io",
    "yopmail.com", "throwawaymail.com", "fakeinbox.com", "maildrop.cc",
    "getairmail.com", "dispostable.com", "mintemail.com", "spamgourmet.com",
    "moakt.com", "emailondeck.com", "tempr.email", "luxusmail.org",
    "anonbox.net", "tempinbox.com", "discard.email", "mohmal.com",
    "trbvm.com", "sharklasers.com", "spam4.me", "armyspy.com",
    "cuvox.de", "dayrep.com", "einrot.com", "fleckens.hu",
    "gustr.com", "jourrapide.com", "rhyta.com", "superrito.com",
    "teleworm.us", "minutemail.com", "mt2014.com", "onetimeemail.com",
    "tempmailaddress.com", "tempmailo.org", "throwaway.email", "tmpeml.info",
    "33mail.com", "burnermail.io", "dropmail.me", "mailcatch.com",
    "mailtrap.io", "incognitomail.org", "mvrht.net", "spambox.us",
    "spamspot.com", "trashymail.com", "mytemp.email", "sneakemail.com",
})


# RESEND_EMAIL_v1
def _send_free_signup_email(email: str, access_url: str):
    """Send welcome email via Resend API (HTTPS — no SMTP port blocking)."""
    import requests as _requests

    api_key = os.getenv("RESEND_API_KEY", "")
    if not api_key:
        log.warning(f"RESEND_API_KEY not set — cannot send email to {email}")
        return

    # Use onboarding@resend.dev until a sending domain is verified.
    # Once landerjob.com is verified at resend.com/domains, change to:
    #   RESEND_FROM='Lander <hello@landerjob.com>'
    from_addr = os.getenv("RESEND_FROM", "Lander <onboarding@resend.dev>")

    try:
        r = _requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "from": from_addr,
                "to": [email],
                "subject": FREE_SIGNUP_SUBJECT,
                "html": free_signup_html(access_url),
                "text": free_signup_plain(access_url),
            },
            timeout=10,
        )
        if r.status_code in (200, 201):
            log.info(f"Free signup email sent via Resend: {email}")
        else:
            log.warning(f"Resend error {r.status_code} for {email}: {r.text[:200]}")
    except Exception as e:
        log.warning(f"Failed to send Resend email to {email}: {e}")


# FREEMIUM_BACKEND_v1_reorder
@app.post("/auth/free-signup")
@limiter.limit("3/minute")  # AI_PROTECTION_v1: max 3 signup attempts per IP per minute
# FREEMIUM_BACKEND_v1_async
async def free_signup(request: Request, background_tasks: BackgroundTasks):
    body = await request.json()
    email = (body.get("email") or "").strip().lower()
    referral = (body.get("ref") or "")[:50]
    user_agent = request.headers.get("user-agent", "")[:200]

    if not email or not EMAIL_RE.match(email):
        raise HTTPException(status_code=400, detail="Invalid email address")

    # AI_PROTECTION_v1: reject disposable email domains
    email_domain = email.split("@")[-1].lower()
    if email_domain in DISPOSABLE_EMAIL_DOMAINS:
        log.info(f"Rejected disposable email signup: {email}")
        raise HTTPException(status_code=400, detail="Please use a permanent email address")

    conn = pool.getconn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT api_key_id FROM free_signups WHERE email = %s",
                (email,),
            )
            existing = cur.fetchone()

            raw_key    = secrets.token_urlsafe(32)
            key_hash   = hashlib.sha256(raw_key.encode()).hexdigest()
            key_prefix = raw_key[:8]

            if existing and existing[0]:
                # Returning user: refresh the key hash on the EXISTING row (preserves tier)
                cur.execute("""
                    UPDATE api_keys
                    SET api_key_hash = %s, api_key_prefix = %s,
                        active = true, expires_at = NOW() + INTERVAL '30 days'
                    WHERE key_id = %s
                """, (key_hash, key_prefix, existing[0]))
                cur.execute("""
                    UPDATE free_signups SET last_seen_at = NOW(), user_agent = %s
                    WHERE email = %s
                """, (user_agent, email))
            else:
                # New user: create api_keys row and free_signups row
                key_id = "K" + secrets.token_hex(8)
                cur.execute("""
                    INSERT INTO api_keys
                        (key_id, client_name, client_email, api_key_hash, api_key_prefix,
                         tier, active, created_at, expires_at)
                    VALUES (%s, %s, %s, %s, %s, 'free', true, NOW(), NOW() + INTERVAL '30 days')
                """, (key_id, email.split("@")[0], email, key_hash, key_prefix))
                cur.execute("""
                    INSERT INTO free_signups
                        (email, api_key_id, referral_source, user_agent)
                    VALUES (%s, %s, %s, %s)
                """, (email, key_id, referral, user_agent))

            conn.commit()

        # Send welcome email asynchronously (don't block the response)
        lander_base = os.environ.get("LANDER_BASE_URL", "https://landerjob.com")
        access_url = f"{lander_base}/auth/verify?token={raw_key}"
        background_tasks.add_task(_send_free_signup_email, email, access_url)

        return {"status": "ok", "message": "Check your email for access link"}

    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"Free signup error: {e}")
        raise HTTPException(status_code=500, detail="Signup failed. Please try again.")
    finally:
        pool.putconn(conn)


@app.get("/auth/verify")
async def verify_token(token: str):
    if not token or len(token) < 20:
        raise HTTPException(status_code=400, detail="Invalid token")

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    conn = pool.getconn()
    try:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute("""
                SELECT
                    ak.key_id,
                    ak.client_email AS email,
                    ak.tier,
                    ak.active,
                    ak.expires_at
                FROM api_keys ak
                WHERE ak.api_key_hash = %s
                LIMIT 1
            """, (token_hash,))
            row = cur.fetchone()

            if not row:
                raise HTTPException(status_code=401, detail="Invalid or expired token")

            if not row["active"]:
                raise HTTPException(status_code=401, detail="Token deactivated")

            if row["expires_at"] and row["expires_at"] < datetime.now(timezone.utc):
                raise HTTPException(status_code=401, detail="Token expired")

            cur.execute("""
                UPDATE free_signups
                SET last_seen_at = NOW()
                WHERE api_key_id = %s
            """, (row["key_id"],))
            conn.commit()

            return {
                "api_key": token,
                "email": row["email"],
                "tier": row["tier"],
            }

    except HTTPException:
        raise
    except Exception as e:
        log.warning(f"Token verify error: {e}")
        raise HTTPException(status_code=500, detail="Verification failed")
    finally:
        pool.putconn(conn)


# ── Stripe Checkout Session ───────────────────────────────────────────────────
@app.post("/stripe/create-checkout")
async def create_checkout(request: Request):
    body = await request.json()
    email = body.get("email", "")
    lander_base = os.environ.get("LANDER_BASE_URL", "https://landerjob.com")
    success_url = body.get("success_url", f"{lander_base}/upgrade-success?session_id={{CHECKOUT_SESSION_ID}}")
    cancel_url = body.get("cancel_url", f"{lander_base}/pricing")
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": body.get("price_id", STRIPE_PRICE_ID), "quantity": 1}],
            customer_email=email or None,
            success_url=success_url,
            cancel_url=cancel_url,
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


# ============================================================
# RESUME UPLOAD ENDPOINT — added 2026-05-06
# ============================================================

MAX_RESUME_BYTES = 5 * 1024 * 1024
ALLOWED_RESUME_EXTS = {".pdf", ".docx", ".txt"}


async def verify_api_key_or_preview(request: Request, conn=Depends(get_conn)) -> dict:
    """Wraps verify_api_key but allows a special preview key for local dev."""
    raw_key = request.headers.get("X-API-Key") or request.query_params.get("api_key")
    if raw_key == "preview":
        return {
            "key_id": "preview",
            "tier": "free",
            "client_name": "preview-local",
            "active": True,
        }
    return await verify_api_key(request=request, conn=conn)


def _skills_payload(skills_dict):
    out = []
    for _, item in (skills_dict or {}).items():
        out.append({
            "name": item.get("name"),
            "confidence": item.get("confidence"),
            "source": item.get("source"),
        })
    return out


@app.post("/v1/resume/upload", tags=["Resume"])
async def upload_resume_v1(
    file: UploadFile = File(...),
    key: dict = Depends(verify_api_key_or_preview),
    conn=Depends(get_conn),
):
    filename = file.filename or ""
    ext = Path(filename).suffix.lower()

    if ext not in ALLOWED_RESUME_EXTS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only PDF, DOCX, and TXT files are supported",
        )

    file_bytes = await file.read()
    if len(file_bytes) > MAX_RESUME_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="File must be under 5MB",
        )

    try:
        text = parse_resume(file_bytes, filename)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Failed to parse resume: {str(e)}",
        )

    if not text or not text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Resume parsing returned empty text",
        )

    cur = None
    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)
        log.info(
            "resume_upload_debug: cursor_type=%s filename=%r",
            type(cur).__name__,
            filename,
        )
        log.info(
            "resume_upload_debug: text_len=%d text_preview=%r",
            len(text),
            text[:1500],
        )
        cur.execute("SELECT 1 AS test")
        probe = cur.fetchone()
        log.info(
            "resume_upload_debug: db_cursor_probe=%s probe_repr_type=%s",
            probe,
            type(probe).__name__,
        )
        try:
            skills = extract_skills(text, cur) or {}
        except Exception:
            log.exception("resume_upload_debug: extract_skills raised")
            raise
        log.info(
            "resume_upload_debug: extract_skills_skill_count=%d",
            len(skills),
        )
        exp_level = infer_experience_level(text) or "mid"
        matched_jobs = match_jobs(
            resume_skills=skills,
            exp_level=exp_level,
            salary_floor=0,
            db_cursor=cur,
            top_n=50,
        ) or []

        tier = key.get("tier", "free") if isinstance(key, dict) else "free"
        is_pro = tier == "pro"

        if is_pro:
            skill_gaps = find_skill_gaps(
                resume_skills=skills,
                exp_level=exp_level,
                db_cursor=cur,
                top_n=5,
            ) or []
            jobs_out = matched_jobs[:50]
        else:
            skill_gaps = []
            jobs_out = matched_jobs[:3]

        return {
            "ok": True,
            "skills_found": _skills_payload(skills),
            "experience_level_inferred": exp_level,
            "matched_jobs": jobs_out,
            "skill_gaps": skill_gaps,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Resume matching failed: {str(e)}",
        )
    finally:
        if cur is not None:
            cur.close()
