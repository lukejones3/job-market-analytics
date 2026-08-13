"""Curated, citation-ready public market answers backed by Lander's live index."""
from __future__ import annotations

from typing import Any


PUBLIC_MARKET_ANSWER_SLUGS = {
    "data-analyst-job-market",
    "companies-hiring-data-scientists",
    "remote-product-manager-job-market",
    "chicago-data-scientist-salaries",
    "remote-job-market",
    "job-market-salary-transparency",
    "fastest-growing-company-hiring",
    "companies-with-most-verified-reposts",
}


_SLICE_CONFIG = {
    "data-analyst-job-market": {
        "condition": "LOWER(BTRIM(jp.role_category)) = %(role_category)s",
        "params": {"role_category": "data_analytics"},
        "row_type": "companies",
    },
    "companies-hiring-data-scientists": {
        "condition": "LOWER(BTRIM(jp.role_category)) = %(role_category)s",
        "params": {"role_category": "data_science"},
        "row_type": "companies",
    },
    "remote-product-manager-job-market": {
        "condition": "LOWER(BTRIM(jp.role_category)) = %(role_category)s AND LOWER(BTRIM(COALESCE(jp.workplace_type, ''))) = 'remote'",
        "params": {"role_category": "product_management"},
        "row_type": "companies",
    },
    "chicago-data-scientist-salaries": {
        "condition": "LOWER(BTRIM(jp.role_category)) = %(role_category)s AND UPPER(COALESCE(jp.loc_state, '')) = 'IL' AND LOWER(COALESCE(jp.loc_city, '')) LIKE '%%chicago%%'",
        "params": {"role_category": "data_science"},
        "row_type": "jobs",
    },
    "remote-job-market": {
        "condition": "LOWER(BTRIM(COALESCE(jp.workplace_type, ''))) = 'remote'",
        "params": {},
        "row_type": "roles",
    },
    "job-market-salary-transparency": {
        "condition": "TRUE",
        "params": {},
        "row_type": "sectors",
    },
}


_MIDPOINT = """CASE
    WHEN jp.salary_min_annual IS NOT NULL AND jp.salary_max_annual IS NOT NULL
      THEN (jp.salary_min_annual::double precision + jp.salary_max_annual::double precision) / 2.0
    WHEN jp.salary_min_annual IS NOT NULL THEN jp.salary_min_annual::double precision
    WHEN jp.salary_max_annual IS NOT NULL THEN jp.salary_max_annual::double precision
END"""


def _slice_answer(cur, slug: str, feed_where: str, base_params: dict[str, Any]) -> dict[str, Any]:
    config = _SLICE_CONFIG[slug]
    condition = config["condition"]
    params = {**base_params, **config["params"]}
    scoped = f"""
        SELECT jp.*, c.company_name, c.company_slug, c.sector, r.role_name,
               COALESCE(NULLIF(BTRIM(l.location), ''), NULLIF(BTRIM(jp.loc_city), ''), 'United States') AS location,
               {_MIDPOINT} AS salary_midpoint
        FROM job_postings jp
        JOIN companies c ON c.company_id=jp.company_id
        JOIN roles r ON r.role_id=jp.role_id
        LEFT JOIN locations l ON l.location_id=jp.location_id
        WHERE {feed_where} AND ({condition})
    """
    cur.execute(f"""
        WITH scoped AS ({scoped})
        SELECT COUNT(*)::int AS active_jobs,
               COUNT(DISTINCT company_id)::int AS companies,
               COUNT(*) FILTER (WHERE salary_midpoint IS NOT NULL)::int AS salary_sample_size,
               COUNT(*) FILTER (WHERE LOWER(BTRIM(COALESCE(workplace_type, '')))='remote')::int AS remote_jobs,
               COUNT(*) FILTER (WHERE COALESCE(posted_date::timestamptz,date_found::timestamptz)>=now()-interval '7 days')::int AS new_jobs_7d,
               percentile_cont(.5) WITHIN GROUP (ORDER BY salary_midpoint)
                   FILTER (WHERE salary_midpoint IS NOT NULL)::double precision AS median_salary,
               (SELECT MAX(published_at)::date::text FROM publication_runs) AS observation_date
        FROM scoped
    """, params)
    stats = dict(cur.fetchone())

    row_type = config["row_type"]
    if row_type == "companies":
        cur.execute(f"""
            WITH scoped AS ({scoped})
            SELECT company_name, company_slug,
                   COUNT(*)::int AS active_jobs,
                   COUNT(*) FILTER (WHERE COALESCE(posted_date::timestamptz,date_found::timestamptz)>=now()-interval '7 days')::int AS new_jobs_7d,
                   AVG((salary_midpoint IS NOT NULL)::int)::double precision AS salary_disclosure_rate,
                   percentile_cont(.5) WITHIN GROUP (ORDER BY salary_midpoint)
                       FILTER (WHERE salary_midpoint IS NOT NULL)::double precision AS median_salary
            FROM scoped
            GROUP BY company_id,company_name,company_slug
            ORDER BY active_jobs DESC,new_jobs_7d DESC,company_name
            LIMIT 20
        """, params)
    elif row_type == "jobs":
        cur.execute(f"""
            WITH scoped AS ({scoped})
            SELECT job_id::text,role_name AS title,company_name,company_slug,location,
                   workplace_type,salary_min_annual::double precision,salary_max_annual::double precision,
                   posted_date::text
            FROM scoped
            WHERE salary_midpoint IS NOT NULL
            ORDER BY salary_midpoint DESC NULLS LAST,posted_date DESC NULLS LAST,job_id
            LIMIT 20
        """, params)
    elif row_type == "roles":
        cur.execute(f"""
            WITH scoped AS ({scoped})
            SELECT role_category,
                   COUNT(*)::int AS active_jobs,
                   COUNT(DISTINCT company_id)::int AS companies,
                   AVG((salary_midpoint IS NOT NULL)::int)::double precision AS salary_disclosure_rate,
                   percentile_cont(.5) WITHIN GROUP (ORDER BY salary_midpoint)
                       FILTER (WHERE salary_midpoint IS NOT NULL)::double precision AS median_salary
            FROM scoped
            WHERE NULLIF(BTRIM(role_category),'') IS NOT NULL
            GROUP BY role_category
            HAVING COUNT(*) >= 5
            ORDER BY active_jobs DESC,role_category
            LIMIT 20
        """, params)
    else:
        cur.execute(f"""
            WITH scoped AS ({scoped})
            SELECT COALESCE(NULLIF(BTRIM(sector),''),'Unclassified') AS sector,
                   COUNT(*)::int AS active_jobs,
                   COUNT(*) FILTER (WHERE salary_midpoint IS NOT NULL)::int AS salary_sample_size,
                   AVG((salary_midpoint IS NOT NULL)::int)::double precision AS salary_disclosure_rate,
                   percentile_cont(.5) WITHIN GROUP (ORDER BY salary_midpoint)
                       FILTER (WHERE salary_midpoint IS NOT NULL)::double precision AS median_salary
            FROM scoped
            GROUP BY 1
            HAVING COUNT(*) >= 25
            ORDER BY salary_disclosure_rate DESC,active_jobs DESC
            LIMIT 20
        """, params)

    return {"slug": slug, "row_type": row_type, "stats": stats, "rows": [dict(row) for row in cur.fetchall()]}


def _growth_answer(cur, slug: str) -> dict[str, Any]:
    order = (
        "active_change_7d DESC,new_opportunities_7d DESC"
        if slug == "fastest-growing-company-hiring"
        else "individual_repost_signals_30d DESC,active_opportunities DESC"
    )
    predicate = (
        "current.active_opportunities-prior.active_opportunities > 0"
        if slug == "fastest-growing-company-hiring"
        else "current.individual_repost_signals_30d > 0"
    )
    cur.execute(f"""
        WITH latest AS (SELECT MAX(snapshot_date) AS day FROM company_radar_daily),
        current AS (
          SELECT r.* FROM company_radar_daily r JOIN latest ON r.snapshot_date=latest.day
          WHERE r.domain='all'
        ), prior AS (
          SELECT r.* FROM company_radar_daily r JOIN latest ON r.snapshot_date=latest.day-7
          WHERE r.domain='all'
        ), ranked AS (
          SELECT c.company_name,c.company_slug,current.active_opportunities,current.new_opportunities_7d,
                 current.individual_repost_signals_30d,
                 current.active_opportunities-prior.active_opportunities AS active_change_7d,
                 CASE WHEN prior.active_opportunities>0 THEN
                   ROUND(100.0*(current.active_opportunities-prior.active_opportunities)/prior.active_opportunities,1)
                 END AS active_change_pct_7d,
                 latest.day::text AS observation_date
          FROM current JOIN prior USING(company_id,domain) JOIN latest ON true
          JOIN public.seo_company_index c ON c.company_id=current.company_id
          WHERE current.active_opportunities>=5
            AND current.coverage_started_at<=latest.day-14
            AND {predicate}
        )
        SELECT * FROM ranked ORDER BY {order} LIMIT 20
    """)
    rows = [dict(row) for row in cur.fetchall()]
    observation_date = rows[0]["observation_date"] if rows else None
    stats = {
        "companies": len(rows),
        "active_jobs": sum(int(row["active_opportunities"]) for row in rows),
        "new_jobs_7d": sum(int(row["new_opportunities_7d"]) for row in rows),
        "verified_reposts_30d": sum(int(row["individual_repost_signals_30d"]) for row in rows),
        "observation_date": observation_date,
    }
    return {"slug": slug, "row_type": "company_trends", "stats": stats, "rows": rows}


def query_public_market_answer(cur, slug: str, feed_where: str, params: dict[str, Any]) -> dict[str, Any]:
    if slug not in PUBLIC_MARKET_ANSWER_SLUGS:
        raise KeyError(slug)
    if slug in _SLICE_CONFIG:
        return _slice_answer(cur, slug, feed_where, params)
    return _growth_answer(cur, slug)
