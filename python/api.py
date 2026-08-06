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
import asyncio
from datetime import datetime, timezone, date
from typing import Optional, List
from contextlib import asynccontextmanager

import psycopg2
import psycopg2.pool
import stripe
from psycopg2.extras import RealDictCursor
from fastapi import FastAPI, HTTPException, Depends, Request, Response, Query, BackgroundTasks, File, UploadFile, status
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
STRIPE_PRICE_ID      = os.getenv("STRIPE_PRICE_ID", "")

if STRIPE_SECRET_KEY:
    stripe.api_key = STRIPE_SECRET_KEY

# ── Connection pool ───────────────────────────────────────────────────────────
pool: psycopg2.pool.ThreadedConnectionPool = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = psycopg2.pool.ThreadedConnectionPool(2, 10, **DB_CONFIG)
    log.info("DB pool initialized")
    # Load the semantic model before traffic arrives. Previously the first resume
    # request downloaded/initialized it inside Cloudflare's request timeout.
    if os.getenv("PRELOAD_RESUME_MODEL", "1") == "1":
        try:
            from sentence_transformers import SentenceTransformer

            def load_resume_model():
                model = SentenceTransformer("all-MiniLM-L6-v2")
                model.max_seq_length = 256
                return model

            app.state.resume_embedding_model = await asyncio.to_thread(load_resume_model)
            log.info("Resume embedding model preloaded")
        except Exception:
            log.exception("Resume model preload failed; skill matching remains available")
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
    description="Multi-domain job intelligence sourced directly from major ATS ecosystems.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in os.getenv(
        "LANDER_CORS_ORIGINS", "https://www.landerjob.com,https://landerjob.com"
    ).split(",") if origin.strip()],
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

# Keep this public-market scope aligned with the Lander Browse All feed. The
# marketing site consumes this endpoint so its headline count is measured from
# the product universe, not a manually maintained number.
PUBLIC_US_STATES = (
    "AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID",
    "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA", "MI", "MN", "MS",
    "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK",
    "OR", "PA", "RI", "SC", "SD", "TN", "TX", "UT", "VT", "VA", "WA", "WV",
    "WI", "WY", "DC", "PR", "VI", "GU", "AS", "MP",
)
PUBLIC_BLOCKED_COMPANY_FRAGMENTS = (
    "cgsfederal", "accenture federal", "booz allen", "mantech", "saic", "caci",
    "prosidian", "guidehouse", "gdit", "leidos", "northrop grumman",
    "parsons federal", "serco federal", "deloitte federal", "parsons",
    "invisible agency", "cermaticom", "jobs for humanity", "devoteam", "canonical",
    "nxp semiconductors", "relx", "bosch group", "about you se", "sixt",
    "scalablegmbh",
)

PUBLIC_FEED_WHERE = """
    jp.data_tier = 1
    AND jp.is_public = true
    AND jp.domain IS NOT NULL
    AND (jp.loc_country IS NULL OR jp.loc_country <> 'foreign')
    AND NOT EXISTS (
        SELECT 1
        FROM unnest(%(blocked_companies)s::text[]) AS blocked_company(match)
        WHERE LOWER(c.company_name) LIKE '%%' || blocked_company.match || '%%'
    )
    AND (
        (
            jp.loc_country IN ('US', 'United States', 'USA')
            AND (jp.loc_state IS NULL OR UPPER(jp.loc_state) = ANY(%(us_states)s::text[]))
        )
        OR (jp.loc_country IS NULL AND UPPER(COALESCE(jp.loc_state, '')) = ANY(%(us_states)s::text[]))
        OR (
            jp.loc_country = 'unknown'
            AND LOWER(COALESCE(jp.ingestion_source, '')) IN ('greenhouse', 'lever', 'ashby')
        )
        OR (
            jp.loc_country = 'unknown'
            AND LOWER(COALESCE(jp.ingestion_source, '')) = 'workday'
            AND LOWER(COALESCE(jp.workplace_type, '')) = 'remote'
        )
    )
"""

# ── Auth ──────────────────────────────────────────────────────────────────────
TIER_LIMITS = {
    "free":       100,
    "pro":        2000,
    "enterprise": 50000,
}

LANDER_INTERNAL_API_KEY = os.getenv("LANDER_INTERNAL_API_KEY", "")


async def verify_resume_client(request: Request, conn=Depends(get_conn)) -> dict:
    """Authenticate browser-server resume requests without treating user ids as API keys."""
    supplied = request.headers.get("X-Lander-Internal-Key", "")
    if LANDER_INTERNAL_API_KEY and supplied and hmac.compare_digest(supplied, LANDER_INTERNAL_API_KEY):
        requested_tier = request.headers.get("X-Lander-User-Tier", "free").lower()
        tier = requested_tier if requested_tier in {"free", "pro"} else "free"
        return {"key_id": "lander-web", "tier": tier, "client_name": "lander-web", "active": True}
    return await verify_api_key(request=request, conn=conn)

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

# ── Public Market Pulse ──────────────────────────────────────────────────────
@app.get("/v1/public/market", tags=["Public"])
@limiter.limit("60/minute")
def public_market(request: Request, response: Response, conn=Depends(get_conn)):
    """Exact Browse All counts plus a small fresh-job preview for landerjob.com."""
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
    params = {
        "us_states": list(PUBLIC_US_STATES),
        "blocked_companies": list(PUBLIC_BLOCKED_COMPANY_FRAGMENTS),
    }
    cur = conn.cursor(cursor_factory=RealDictCursor)
    cur.execute(f"""
        SELECT
            COUNT(*)::int AS active_jobs,
            COUNT(DISTINCT jp.company_id)::int AS companies,
            COUNT(DISTINCT NULLIF(LOWER(BTRIM(jp.ingestion_source)), ''))::int AS ats_sources,
            COUNT(DISTINCT jp.domain)::int AS verticals,
            COALESCE(
                AVG((jp.salary_min_annual IS NOT NULL OR jp.salary_max_annual IS NOT NULL)::int),
                0
            )::double precision AS salary_disclosure_rate,
            COUNT(*) FILTER (
                WHERE COALESCE(jp.posted_date::timestamptz, jp.date_found::timestamptz)
                    >= NOW() - INTERVAL '24 hours'
            )::int AS fresh_today,
            MAX(jp.date_found) AS indexed_at
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        WHERE {PUBLIC_FEED_WHERE}
    """, params)
    stats = dict(cur.fetchone())

    cur.execute(f"""
        SELECT
            jp.job_id::text AS job_id,
            r.role_name AS title,
            c.company_name,
            COALESCE(NULLIF(BTRIM(l.location), ''), 'United States') AS location,
            jp.workplace_type,
            jp.experience_level,
            jp.salary_min_annual::double precision,
            jp.salary_max_annual::double precision,
            jp.posted_date,
            jp.job_url
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN locations l ON l.location_id = jp.location_id
        WHERE {PUBLIC_FEED_WHERE}
          AND (jp.salary_min_annual IS NOT NULL OR jp.salary_max_annual IS NOT NULL)
        ORDER BY COALESCE(jp.posted_date, jp.date_found) DESC NULLS LAST, jp.job_id
        LIMIT 6
    """, params)
    jobs = [dict(row) for row in cur.fetchall()]

    cur.execute(f"""
        SELECT c.company_slug AS slug
        FROM job_postings jp
        JOIN companies c ON c.company_id = jp.company_id
        WHERE {PUBLIC_FEED_WHERE}
          AND c.company_slug IS NOT NULL
        GROUP BY c.company_id, c.company_slug
        HAVING COUNT(*) >= 5
        ORDER BY COUNT(*) DESC, c.company_slug
        LIMIT 100
    """, params)
    top_company_slugs = [row["slug"] for row in cur.fetchall()]

    cur.execute(f"""
        SELECT LOWER(BTRIM(sk.skill_slug)) AS slug
        FROM skills sk
        JOIN job_skills js ON js.skill_id = sk.skill_id
        JOIN job_postings jp ON jp.job_id = js.job_id
        JOIN companies c ON c.company_id = jp.company_id
        WHERE {PUBLIC_FEED_WHERE}
          AND sk.skill_slug IS NOT NULL
        GROUP BY LOWER(BTRIM(sk.skill_slug))
        HAVING COUNT(DISTINCT jp.job_id) >= 5
        ORDER BY COUNT(DISTINCT jp.job_id) DESC, slug
        LIMIT 50
    """, params)
    top_skill_slugs = [row["slug"] for row in cur.fetchall()]

    return {
        "stats": stats,
        "jobs": jobs,
        "top_company_slugs": top_company_slugs,
        "top_skill_slugs": top_skill_slugs,
        "generated_at": datetime.now(timezone.utc),
    }


PUBLIC_INSIGHT_SLUGS = {
    "companies-with-lowest-ghost-rates",
    "highest-paying-ai-engineering-jobs",
    "remote-data-jobs-with-disclosed-salaries",
    "sectors-with-most-salary-transparency",
    "skill-salary-premiums",
}


@app.get("/v1/public/insights/{insight_slug}", tags=["Public"])
@limiter.limit("60/minute")
def public_insight(insight_slug: str, request: Request, response: Response, conn=Depends(get_conn)):
    response.headers["Cache-Control"] = "public, max-age=60, s-maxage=900, stale-while-revalidate=1800"
    if insight_slug not in PUBLIC_INSIGHT_SLUGS:
        raise HTTPException(status_code=404, detail="Unknown public insight")

    params = {
        "us_states": list(PUBLIC_US_STATES),
        "blocked_companies": list(PUBLIC_BLOCKED_COMPANY_FRAGMENTS),
    }
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if insight_slug == "companies-with-lowest-ghost-rates":
        cur.execute(f"""
            WITH company_agg AS (
                SELECT
                    c.company_name,
                    SUM(CASE WHEN (
                        CASE
                            WHEN mgi.ghost_probability::double precision > 1
                                THEN mgi.ghost_probability::double precision / 100.0
                            ELSE mgi.ghost_probability::double precision
                        END
                    ) > 0.7 THEN 1 ELSE 0 END)::double precision AS ghost_hits,
                    COUNT(*)::int AS active_postings
                FROM analytics_analytics.mart_ghost_job_index mgi
                JOIN job_postings jp ON jp.job_id = mgi.job_id
                JOIN companies c ON c.company_id = jp.company_id
                WHERE {PUBLIC_FEED_WHERE}
                GROUP BY c.company_name
                HAVING COUNT(*) >= 20
            )
            SELECT company_name, ghost_hits / NULLIF(active_postings, 0) AS ghost_rate,
                   active_postings
            FROM company_agg
            ORDER BY ghost_rate ASC NULLS LAST, active_postings DESC
            LIMIT 25
        """, params)
    elif insight_slug == "highest-paying-ai-engineering-jobs":
        cur.execute(f"""
            SELECT r.role_name AS title, c.company_name, jp.role_category,
                   (jp.salary_min_annual::double precision + jp.salary_max_annual::double precision) / 2 AS mid_salary,
                   jp.workplace_type
            FROM job_postings jp
            JOIN companies c ON c.company_id = jp.company_id
            JOIN roles r ON r.role_id = jp.role_id
            WHERE {PUBLIC_FEED_WHERE}
              AND jp.domain IN ('data_ml', 'engineering')
              AND LOWER(BTRIM(jp.role_category)) = ANY(%(role_categories)s::text[])
              AND jp.salary_min_annual IS NOT NULL AND jp.salary_max_annual IS NOT NULL
            ORDER BY mid_salary DESC NULLS LAST
            LIMIT 25
        """, {**params, "role_categories": ["ml_engineering", "ai_research", "data_science", "analytics_engineering", "data_engineering"]})
    elif insight_slug == "remote-data-jobs-with-disclosed-salaries":
        cur.execute(f"""
            SELECT r.role_name AS title, c.company_name,
                   jp.salary_min_annual AS salary_min, jp.salary_max_annual AS salary_max,
                   jp.posted_date::text AS posted_date
            FROM job_postings jp
            JOIN companies c ON c.company_id = jp.company_id
            JOIN roles r ON r.role_id = jp.role_id
            WHERE {PUBLIC_FEED_WHERE}
              AND jp.domain = 'data_ml'
              AND LOWER(TRIM(COALESCE(jp.workplace_type, ''))) = 'remote'
              AND jp.salary_min_annual IS NOT NULL AND jp.salary_max_annual IS NOT NULL
            ORDER BY COALESCE(jp.posted_date::timestamptz, jp.date_found::timestamptz) DESC NULLS LAST
            LIMIT 30
        """, params)
    elif insight_slug == "sectors-with-most-salary-transparency":
        cur.execute(f"""
            SELECT COALESCE(NULLIF(TRIM(c.sector), ''), 'Unknown') AS sector,
                   AVG(CASE WHEN jp.salary_min_annual IS NOT NULL AND jp.salary_max_annual IS NOT NULL
                       THEN 1.0 ELSE 0.0 END)::double precision AS disclosure_rate,
                   COUNT(*)::int AS job_count
            FROM job_postings jp
            JOIN companies c ON c.company_id = jp.company_id
            WHERE {PUBLIC_FEED_WHERE}
            GROUP BY 1
            HAVING COUNT(*) >= 25
            ORDER BY disclosure_rate DESC NULLS LAST, job_count DESC
            LIMIT 15
        """, params)
    else:
        cur.execute(f"""
            WITH scoped_jobs AS (
                SELECT jp.job_id,
                       (jp.salary_min_annual::numeric + jp.salary_max_annual::numeric) / 2 AS mid_salary
                FROM job_postings jp
                JOIN companies c ON c.company_id = jp.company_id
                WHERE {PUBLIC_FEED_WHERE}
                  AND jp.salary_min_annual IS NOT NULL AND jp.salary_max_annual IS NOT NULL
            )
            SELECT s.skill_name,
                   percentile_cont(0.5) WITHIN GROUP (ORDER BY sj.mid_salary)::numeric AS median_salary_annual,
                   COUNT(*)::int AS sample_size
            FROM scoped_jobs sj
            JOIN job_skills js ON js.job_id = sj.job_id
            JOIN skills s ON s.skill_id = js.skill_id
            GROUP BY s.skill_name
            HAVING COUNT(*) >= 10
            ORDER BY median_salary_annual DESC NULLS LAST
            LIMIT 20
        """, params)

    return {
        "slug": insight_slug,
        "rows": [dict(row) for row in cur.fetchall()],
        "generated_at": datetime.now(timezone.utc),
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
        WHERE jp.data_tier = 1 AND jp.is_public = true
          AND jp.role_id IS NOT NULL
          AND COALESCE(jp.loc_country, 'unknown') IN ('US', 'unknown')
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
        WHERE data_tier = 1 AND is_public = true
          AND role_id IS NOT NULL
          AND COALESCE(loc_country, 'unknown') IN ('US', 'unknown')
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

    where = ["jp.data_tier = 1", "jp.is_public = true", "jp.role_id IS NOT NULL",
             "COALESCE(jp.loc_country, 'unknown') IN ('US', 'unknown')"]
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
                 WHERE data_tier=1 AND is_public=true
                   AND salary_max_annual BETWEEN 50000 AND 500000)) /
                NULLIF((SELECT AVG(salary_max_annual) FROM job_postings
                 WHERE data_tier=1 AND is_public=true
                   AND salary_max_annual BETWEEN 50000 AND 500000), 0) * 100, 1)        as salary_premium_pct,
            ROUND(AVG(CASE WHEN js.skill_priority = 'required'
                THEN 1.0 ELSE 0.0 END) * 100)                                          as required_pct
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        JOIN job_postings jp ON jp.job_id = js.job_id
        JOIN roles r ON r.role_id = jp.role_id
        WHERE jp.data_tier = 1 AND jp.is_public = true
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
        WHERE jp.data_tier = 1 AND jp.is_public = true AND c.sector IS NOT NULL
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
        WHERE {' AND '.join(where) if where else '1=1'}
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

    where = ["jp.company_id = %s", "jp.data_tier = 1", "jp.is_public = true"]
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
            g.ghost_probability, g.ghost_tier, g.score_confidence,
            g.age_risk, g.reappearance_count, g.related_posting_count,
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
        "results": [_add_repost_warning(dict(r)) for r in cur.fetchall()],
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
                 WHERE company_id = %s AND data_tier=1 AND is_public=true), 0) * 100, 1) as pct_of_roles
        FROM job_postings jp
        JOIN job_skills js ON js.job_id = jp.job_id
        JOIN skills s ON s.skill_id = js.skill_id
        WHERE jp.company_id = %s AND jp.data_tier = 1 AND jp.is_public = true
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
def _add_repost_warning(role: dict) -> dict:
    """Add a stable frontend contract for a verified disappear/reappear cycle."""
    count = int(role.get("reappearance_count") or 0)
    role["repost_warning"] = None if count == 0 else {
        "label": "Reposted",
        "message": "This requisition disappeared and returned with a newer published date.",
        "count": count,
    }
    return role


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

    where = ["jp.data_tier = 1", "jp.is_public = true", "jp.role_id IS NOT NULL",
             "COALESCE(jp.loc_country, 'unknown') IN ('US', 'unknown')"]
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

    count_params = {k: v for k, v in params.items() if k not in ("limit", "offset")}
    cur.execute(f"""SELECT COUNT(*) AS total
        FROM job_postings jp
        JOIN roles r ON r.role_id=jp.role_id
        LEFT JOIN companies c ON c.company_id=jp.company_id
        LEFT JOIN vw_ghost_job_index g ON g.job_id=jp.job_id
        WHERE {' AND '.join(where)}""", count_params)
    total = cur.fetchone()["total"]

    cur.execute(f"""
        SELECT jp.job_id, r.role_name,
            {ROLE_FAMILY_SQL}                   as role_family,
            c.company_name, c.sector,
            jp.experience_level, jp.workplace_type,
            jp.salary_min_annual, jp.salary_max_annual,
            jp.job_url, jp.posted_date,
            l.location, l.state,
            g.ghost_probability, g.ghost_tier, g.score_confidence,
            g.age_risk, g.reappearance_count, g.related_posting_count,
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

    results = [_add_repost_warning(dict(r)) for r in cur.fetchall()]
    return {
        "filters":      {k: v for k, v in params.items() if k not in ("limit","offset")},
        "count":        len(results),
        "total":        total,
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
            g.ghost_probability, g.ghost_tier, g.score_confidence,
            g.age_risk, g.reappearance_count, g.related_posting_count,
            jh.honesty_score
        FROM job_postings jp
        JOIN roles r ON r.role_id = jp.role_id
        LEFT JOIN companies c ON c.company_id = jp.company_id
        LEFT JOIN locations l ON l.location_id = jp.location_id
        LEFT JOIN vw_ghost_job_index g ON g.job_id = jp.job_id
        LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
        WHERE jp.job_id = %s AND jp.data_tier = 1 AND jp.is_public = true
          AND jp.role_id IS NOT NULL
          AND COALESCE(jp.loc_country, 'unknown') IN ('US', 'unknown')
    """, (job_id,))

    role = cur.fetchone()
    if not role:
        raise HTTPException(status_code=404, detail=f"Role '{job_id}' not found.")

    role = _add_repost_warning(dict(role))

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
                    # FREEMIUM_BACKEND_v1: mark free_signups as upgraded and point
                    # api_key_id at the new Pro key so future free-signup magic
                    # links refresh the Pro key, not a stale free key.
                    cur.execute("""
                        UPDATE free_signups
                        SET upgraded_to_paid_at = NOW(), api_key_id = %s
                        WHERE email = %s
                    """, (key_id, customer_email,))
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


def _migrate_rotating_credential_rows(cur, key_id: str, previous_hash: str | None) -> None:
    """Move rows owned by the previous raw credential onto the stable key id.

    Raw tokens cannot be reversed, but the prior token can be identified safely by
    hashing candidate ownership values and comparing them to api_keys.api_key_hash.
    This runs immediately before that hash rotates.
    """
    if not previous_hash:
        return
    legacy_ids: set[str] = set()
    for table in ("user_applied_jobs", "user_saved_jobs", "career_campaigns", "career_profiles", "resumes"):
        cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
        if not cur.fetchone()[0]:
            continue
        cur.execute(f"SELECT DISTINCT user_id::text FROM {table} WHERE user_id IS NOT NULL")
        for (candidate,) in cur.fetchall():
            if candidate != key_id and hashlib.sha256(candidate.encode()).hexdigest() == previous_hash:
                legacy_ids.add(candidate)

    for legacy in legacy_ids:
        cur.execute("SELECT to_regclass('public.user_applied_jobs')")
        if cur.fetchone()[0]:
            cur.execute("""
                INSERT INTO user_applied_jobs(user_id,job_id,applied_at,outcome,outcome_captured_at,outcome_email_sent_at)
                SELECT %s,job_id,applied_at,outcome,outcome_captured_at,outcome_email_sent_at
                FROM user_applied_jobs WHERE user_id=%s
                ON CONFLICT(user_id,job_id) DO UPDATE SET
                  applied_at=LEAST(user_applied_jobs.applied_at,EXCLUDED.applied_at),
                  outcome=COALESCE(user_applied_jobs.outcome,EXCLUDED.outcome),
                  outcome_captured_at=COALESCE(user_applied_jobs.outcome_captured_at,EXCLUDED.outcome_captured_at),
                  outcome_email_sent_at=COALESCE(user_applied_jobs.outcome_email_sent_at,EXCLUDED.outcome_email_sent_at)
            """, (key_id, legacy))
            cur.execute("DELETE FROM user_applied_jobs WHERE user_id=%s", (legacy,))
        cur.execute("SELECT to_regclass('public.user_saved_jobs')")
        if cur.fetchone()[0]:
            cur.execute("""
                INSERT INTO user_saved_jobs(user_id,job_id,saved_at)
                SELECT %s,job_id,saved_at FROM user_saved_jobs WHERE user_id=%s
                ON CONFLICT(user_id,job_id) DO NOTHING
            """, (key_id, legacy))
            cur.execute("DELETE FROM user_saved_jobs WHERE user_id=%s", (legacy,))
        for table in ("career_campaigns", "resumes"):
            cur.execute("SELECT to_regclass(%s)", (f"public.{table}",))
            if cur.fetchone()[0]:
                cur.execute(f"UPDATE {table} SET user_id=%s WHERE user_id=%s", (key_id, legacy))
        cur.execute("SELECT to_regclass('public.career_profiles')")
        if cur.fetchone()[0]:
            cur.execute("""
                INSERT INTO career_profiles(user_id,candidate_summary,requirements,links,created_at,updated_at)
                SELECT %s,candidate_summary,requirements,links,created_at,updated_at
                FROM career_profiles WHERE user_id=%s
                ON CONFLICT(user_id) DO UPDATE SET
                  candidate_summary=COALESCE(NULLIF(career_profiles.candidate_summary,''),EXCLUDED.candidate_summary),
                  links=EXCLUDED.links || career_profiles.links,
                  updated_at=GREATEST(career_profiles.updated_at,EXCLUDED.updated_at)
            """, (key_id, legacy))
            cur.execute("DELETE FROM career_profiles WHERE user_id=%s", (legacy,))
        log.info("Migrated rotating-credential ownership to stable key_id=%s", key_id)

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
            # Always prefer an active Pro key if one exists — free_signups.api_key_id
            # can lag behind if the Stripe webhook fired before the user's free_signups
            # row existed, or if a prior free-signup ran before this check was in place.
            cur.execute("""
                SELECT key_id, api_key_hash FROM api_keys
                WHERE client_email = %s AND tier = 'pro' AND active = true
                ORDER BY created_at DESC LIMIT 1
            """, (email,))
            pro_row = cur.fetchone()

            cur.execute(
                "SELECT api_key_id FROM free_signups WHERE email = %s",
                (email,),
            )
            existing = cur.fetchone()

            raw_key    = secrets.token_urlsafe(32)
            key_hash   = hashlib.sha256(raw_key.encode()).hexdigest()
            key_prefix = raw_key[:8]

            if pro_row:
                # Pro subscriber — refresh hash on the Pro key so the magic link
                # returns tier='pro'. Also pin free_signups to that key so future
                # calls don't drift back to a stale free key.
                target_key_id = pro_row[0]
                _migrate_rotating_credential_rows(cur, target_key_id, pro_row[1])
                cur.execute("""
                    UPDATE api_keys
                    SET api_key_hash = %s, api_key_prefix = %s,
                        active = true, expires_at = NOW() + INTERVAL '30 days'
                    WHERE key_id = %s
                """, (key_hash, key_prefix, target_key_id))
                if existing:
                    cur.execute("""
                        UPDATE free_signups
                        SET api_key_id = %s, last_seen_at = NOW(), user_agent = %s
                        WHERE email = %s
                    """, (target_key_id, user_agent, email))
                else:
                    cur.execute("""
                        INSERT INTO free_signups
                            (email, api_key_id, referral_source, user_agent)
                        VALUES (%s, %s, %s, %s)
                    """, (email, target_key_id, referral, user_agent))
            elif existing and existing[0]:
                # Returning free user: refresh hash on their existing free key
                cur.execute("SELECT api_key_hash FROM api_keys WHERE key_id=%s", (existing[0],))
                previous = cur.fetchone()
                _migrate_rotating_credential_rows(cur, existing[0], previous[0] if previous else None)
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
                # Stable account identity. The raw key above is an access credential
                # and rotates whenever a new magic link is requested; it must never
                # be used as an ownership key for user data.
                "key_id": row["key_id"],
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
async def create_checkout(
    request: Request,
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    if not STRIPE_PRICE_ID:
        log.error("Stripe checkout requested without STRIPE_PRICE_ID configured")
        raise HTTPException(status_code=503, detail="Billing is temporarily unavailable.")

    # Price, account, and redirect destinations are server-owned. Accepting any of
    # these from the browser would let a caller create checkout sessions for an
    # arbitrary price/customer or redirect Stripe through an untrusted URL.
    email = _client_email_for_key(conn, key)
    lander_base = os.environ.get("LANDER_BASE_URL", "https://landerjob.com")
    success_url = f"{lander_base}/upgrade-success?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{lander_base}/pricing"
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="subscription",
            line_items=[{"price": STRIPE_PRICE_ID, "quantity": 1}],
            customer_email=email,
            success_url=success_url,
            cancel_url=cancel_url,
            client_reference_id=key["key_id"],
        )
        return {"checkout_url": session.url}
    except Exception as e:
        log.warning(f"Stripe checkout failed for key={key['key_id']}: {e}")
        raise HTTPException(status_code=400, detail="Unable to start checkout. Please try again.")


# ── Stripe billing helpers (resolve customer via api_keys.client_email only) ──
def _client_email_for_key(conn, key: dict) -> str:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            "SELECT client_email FROM api_keys WHERE key_id = %s",
            (key["key_id"],),
        )
        row = cur.fetchone()
    email = (row["client_email"] if row else None) or ""
    email = email.strip().lower()
    if not email:
        raise HTTPException(
            status_code=404,
            detail="No billing email on file for this account. Contact support.",
        )
    return email


def _stripe_customer_id_for_email(email: str) -> str:
    """Exact email match only. Never pick customers.data[0] without a match."""
    try:
        customers = stripe.Customer.list(email=email, limit=10)
    except Exception as e:
        log.warning(f"Stripe Customer.list failed for {email!r}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    matches = [
        c
        for c in (customers.data or [])
        if (c.email or "").strip().lower() == email
    ]
    if len(matches) == 0:
        raise HTTPException(
            status_code=404,
            detail="No Stripe billing profile found for this account.",
        )
    if len(matches) > 1:
        ids = [c.id for c in matches]
        log.warning(f"Stripe: multiple customers for {email!r}: {ids}")
        raise HTTPException(
            status_code=409,
            detail="Multiple billing profiles found for this email. Please contact support.",
        )
    return matches[0].id


def _first_cancellable_subscription(customer_id: str):
    try:
        for status in ("active", "trialing", "past_due"):
            subs = stripe.Subscription.list(customer=customer_id, status=status, limit=1)
            if subs.data:
                return subs.data[0]
    except Exception as e:
        log.warning(f"Stripe Subscription.list failed for {customer_id}: {e}")
        raise HTTPException(status_code=400, detail=str(e))
    return None


async def verify_billing_client(request: Request, conn=Depends(get_conn)) -> dict:
    """Accept either a user's legacy API key or Lander's authenticated server."""
    supplied = request.headers.get("X-Lander-Internal-Key", "")
    if LANDER_INTERNAL_API_KEY and supplied and hmac.compare_digest(supplied, LANDER_INTERNAL_API_KEY):
        email = request.headers.get("X-Lander-User-Email", "").strip().lower()
        if not email:
            raise HTTPException(status_code=400, detail="Authenticated billing email is required.")
        return {"key_id": "lander-web", "billing_email": email}
    return await verify_api_key(request=request, conn=conn)


@app.post("/stripe/create-portal")
async def create_portal(
    request: Request,
    key: dict = Depends(verify_billing_client),
    conn=Depends(get_conn),
):
    body = await request.json()
    lander_base = os.environ.get("LANDER_BASE_URL", "https://landerjob.com")
    return_url = body.get("return_url", f"{lander_base}/settings")
    email = key.get("billing_email") or _client_email_for_key(conn, key)
    customer_id = _stripe_customer_id_for_email(email)
    try:
        session = stripe.billing_portal.Session.create(
            customer=customer_id,
            return_url=return_url,
        )
        return {"portal_url": session.url}
    except Exception as e:
        log.warning(f"Stripe billing portal failed for {email!r} ({customer_id}): {e}")
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/stripe/cancel-subscription")
async def cancel_subscription(
    request: Request,
    key: dict = Depends(verify_api_key),
    conn=Depends(get_conn),
):
    body = await request.json()
    immediate = bool(body.get("immediate", True))
    email = _client_email_for_key(conn, key)
    try:
        customer_id = _stripe_customer_id_for_email(email)
    except HTTPException as exc:
        # No Stripe customer: nothing to cancel (idempotent for delete-account).
        if exc.status_code == 404:
            return {"ok": True, "cancelled": False}
        # Ambiguous multiple customers: must not cancel the wrong subscription.
        raise
    sub = _first_cancellable_subscription(customer_id)
    if not sub:
        return {"ok": True, "cancelled": False}
    try:
        if immediate:
            stripe.Subscription.cancel(sub.id)
        else:
            stripe.Subscription.modify(sub.id, cancel_at_period_end=True)
    except Exception as e:
        log.warning(f"Stripe subscription cancel failed for {email!r} ({sub.id}): {e}")
        raise HTTPException(status_code=400, detail=str(e))
    with conn.cursor() as cur:
        cur.execute(
            "UPDATE api_keys SET active = false WHERE key_id = %s",
            (key["key_id"],),
        )
        conn.commit()
    log.info(f"Cancelled subscription {sub.id} for {email!r} (immediate={immediate})")
    return {"ok": True, "cancelled": True}


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
    key: dict = Depends(verify_resume_client),
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
        # Interactive matching must fail before the edge proxy's hard timeout,
        # particularly while nightly maintenance is touching job_postings.
        cur.execute("SET LOCAL lock_timeout = '8s'")
        cur.execute("SET LOCAL statement_timeout = '45s'")
        # Never emit resume contents into application logs.
        log.info("resume_upload: parsed filename=%r text_len=%d", filename, len(text))
        try:
            skills = extract_skills(text, cur) or {}
        except Exception:
            log.exception("resume_upload: extract_skills raised")
            raise
        log.info("resume_upload: extracted_skill_count=%d", len(skills))
        exp_level = infer_experience_level(text) or "mid"
        # The matcher has a semantic path, but the former upload endpoint never
        # supplied an embedding and therefore ranked generic skill overlap across
        # unrelated domains. Supply the semantic vector so the first response is useful.
        try:
            model = getattr(app.state, "resume_embedding_model", None)
            if model is None:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer("all-MiniLM-L6-v2")
                model.max_seq_length = 256
                app.state.resume_embedding_model = model
            resume_embedding = model.encode(text[:2500], convert_to_numpy=True).tolist()
        except Exception:
            log.exception("resume_upload: semantic embedding failed")
            resume_embedding = None
        matched_jobs = match_jobs(
            resume_skills=skills,
            exp_level=exp_level,
            salary_floor=0,
            db_cursor=cur,
            top_n=50,
            resume_embedding=resume_embedding,
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
        log.exception("resume_upload: matching failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Resume matching is temporarily unavailable. Please try again shortly.",
        )
    finally:
        if cur is not None:
            cur.close()
