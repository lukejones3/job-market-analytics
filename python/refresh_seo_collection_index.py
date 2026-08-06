#!/usr/bin/env python3
"""Materialize role/location crawl inventory after the public snapshot is published."""
from __future__ import annotations

import os
import psycopg2

STATE = "UPPER(TRIM(COALESCE(NULLIF(TRIM(jp.loc_state), ''), NULLIF(TRIM(split_part(COALESCE(l.location, ''), ',', 2)), ''))))"
CITY = "LOWER(TRIM(COALESCE(NULLIF(TRIM(split_part(COALESCE(l.location, ''), ',', 1)), ''), jp.loc_city, '')))"

METROS = {
    "san-francisco": ("CA", ["san francisco", "sf"]), "new-york": ("NY", ["new york", "manhattan", "brooklyn", "queens"]),
    "seattle": ("WA", ["seattle", "bellevue", "redmond", "kirkland", "bothell", "renton", "tacoma", "everett", "issaquah", "lynnwood", "kent", "federal way"]),
    "austin": ("TX", ["austin"]), "boston": ("MA", ["boston", "cambridge", "somerville", "waltham"]),
    "los-angeles": ("CA", ["los angeles"]), "chicago": ("IL", ["chicago"]), "miami": ("FL", ["miami"]),
    "denver": ("CO", ["denver"]), "atlanta": ("GA", ["atlanta", "alpharetta", "marietta", "sandy springs"]),
    "dallas": ("TX", ["dallas", "fort worth", "plano", "irving", "frisco"]), "phoenix": ("AZ", ["phoenix"]),
    "philadelphia": ("PA", ["philadelphia"]), "houston": ("TX", ["houston"]), "portland": ("OR", ["portland"]),
    "san-diego": ("CA", ["san diego"]), "nashville": ("TN", ["nashville"]), "charlotte": ("NC", ["charlotte"]),
    "detroit": ("MI", ["detroit"]), "minneapolis": ("MN", ["minneapolis", "st. paul", "saint paul"]),
    "raleigh": ("NC", ["raleigh"]), "pittsburgh": ("PA", ["pittsburgh"]), "salt-lake-city": ("UT", ["salt lake"]),
    "tampa": ("FL", ["tampa"]), "st-louis": ("MO", ["st. louis", "st louis"]), "columbus": ("OH", ["columbus"]),
    "indianapolis": ("IN", ["indianapolis"]), "las-vegas": ("NV", ["las vegas"]),
}

BLOCKED = ["cgsfederal", "accenture federal", "booz allen", "mantech", "saic", "caci", "prosidian", "guidehouse", "gdit", "leidos", "northrop grumman", "parsons", "serco federal", "deloitte federal", "invisible agency", "cermaticom", "jobs for humanity", "devoteam", "canonical", "nxp semiconductors", "relx", "bosch group", "about you se", "sixt", "scalablegmbh"]

def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"

def predicates() -> dict[str, str]:
    result = {"remote": "LOWER(TRIM(COALESCE(jp.workplace_type, '')))='remote'"}
    for slug, (state, cities) in METROS.items():
        city_terms = " OR ".join(f"{CITY} LIKE '%{city}%'" for city in cities)
        result[slug] = f"({STATE}={_literal(state)} AND ({city_terms}))"
    result["washington-dc"] = f"({STATE}='DC' OR ({STATE}='VA' AND ({CITY} LIKE '%arlington%' OR {CITY} LIKE '%alexandria%')) OR ({STATE}='MD' AND {CITY} LIKE '%bethesda%'))"
    result["kansas-city"] = f"({STATE} IN ('MO','KS') AND {CITY} LIKE '%kansas city%')"
    for state in ("CA", "NY", "TX", "WA", "IL"):
        result[state.lower()] = f"{STATE}='{state}'"
    return result

def refresh() -> int:
    dsn = os.getenv("DATABASE_URL")
    conn = psycopg2.connect(dsn) if dsn else psycopg2.connect(host=os.environ["PGHOST"], port=os.getenv("PGPORT", "5432"), dbname=os.environ.get("PGDATABASE", "job_analytics"), user=os.environ["PGUSER"], password=os.environ["PGPASSWORD"])
    labels = ",".join(f"CASE WHEN ({sql}) THEN {_literal(slug)}::text END" for slug, sql in predicates().items())
    blocked = ",".join(_literal(value) for value in BLOCKED)
    query = f"""
      SELECT LOWER(BTRIM(jp.role_category)), loc.location, COUNT(*)::int
      FROM job_postings jp JOIN companies c ON c.company_id=jp.company_id LEFT JOIN locations l ON l.location_id=jp.location_id
      CROSS JOIN LATERAL unnest(array_remove(ARRAY[{labels}], NULL)) loc(location)
      WHERE jp.is_public=true AND jp.data_tier=1 AND jp.domain IS NOT NULL
        AND jp.role_category IS NOT NULL AND BTRIM(jp.role_category) <> ''
        AND NOT EXISTS (SELECT 1 FROM unnest(ARRAY[{blocked}]::text[]) b(match) WHERE LOWER(c.company_name) LIKE '%'||b.match||'%')
      GROUP BY 1,2 HAVING COUNT(*)>=5
    """
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout='120s'")
            cur.execute("CREATE TEMP TABLE seo_role_location_next (LIKE public.seo_role_location_index INCLUDING DEFAULTS) ON COMMIT DROP")
            cur.execute(f"INSERT INTO seo_role_location_next(role_category,location_slug,job_count) {query}")
            count = cur.rowcount
            cur.execute("TRUNCATE public.seo_role_location_index")
            cur.execute("INSERT INTO public.seo_role_location_index SELECT * FROM seo_role_location_next")
            blocked_sql = f"NOT EXISTS (SELECT 1 FROM unnest(ARRAY[{blocked}]::text[]) b(match) WHERE LOWER(c.company_name) LIKE '%'||b.match||'%')"
            scope = f"jp.is_public=true AND jp.data_tier=1 AND jp.domain IS NOT NULL AND {blocked_sql}"
            midpoint = "CASE WHEN jp.salary_min_annual IS NOT NULL AND jp.salary_max_annual IS NOT NULL THEN (jp.salary_min_annual::double precision+jp.salary_max_annual::double precision)/2 WHEN jp.salary_min_annual IS NOT NULL THEN jp.salary_min_annual::double precision WHEN jp.salary_max_annual IS NOT NULL THEN jp.salary_max_annual::double precision END"
            ghost = "CASE WHEN mgi.ghost_probability IS NULL THEN 0.5 WHEN mgi.ghost_probability>1 THEN mgi.ghost_probability::double precision/100.0 ELSE mgi.ghost_probability::double precision END"
            cur.execute("CREATE TEMP TABLE seo_company_next (LIKE public.seo_company_index INCLUDING DEFAULTS) ON COMMIT DROP")
            cur.execute(f"""INSERT INTO seo_company_next(company_id,company_slug,company_name,sector,job_count,median_salary,p25_salary,p75_salary,avg_comp_annual,avg_ghost,median_ghost,salary_disclosure_rate)
              SELECT c.company_id::text,c.company_slug,c.company_name,c.sector,COUNT(*)::int,
                percentile_cont(.5) WITHIN GROUP(ORDER BY {midpoint}) FILTER(WHERE {midpoint} IS NOT NULL),
                percentile_cont(.25) WITHIN GROUP(ORDER BY {midpoint}) FILTER(WHERE {midpoint} IS NOT NULL),
                percentile_cont(.75) WITHIN GROUP(ORDER BY {midpoint}) FILTER(WHERE {midpoint} IS NOT NULL),
                AVG({midpoint}) FILTER(WHERE {midpoint} IS NOT NULL), AVG({ghost}),
                percentile_cont(.5) WITHIN GROUP(ORDER BY {ghost}), AVG(CASE WHEN {midpoint} IS NOT NULL THEN 1.0 ELSE 0.0 END)
              FROM job_postings jp JOIN companies c ON c.company_id=jp.company_id LEFT JOIN analytics_analytics.mart_ghost_job_index mgi ON mgi.job_id=jp.job_id
              WHERE {scope} GROUP BY c.company_id,c.company_slug,c.company_name,c.sector HAVING COUNT(*)>=5""")
            cur.execute("CREATE TEMP TABLE seo_skill_next (LIKE public.seo_skill_index INCLUDING DEFAULTS) ON COMMIT DROP")
            cur.execute(f"""INSERT INTO seo_skill_next(skill_id,skill_slug,skill_name,job_count,median_salary,p25_salary,p75_salary,market_median_salary)
              WITH scoped AS (SELECT jp.job_id,{midpoint} mid FROM job_postings jp JOIN companies c ON c.company_id=jp.company_id WHERE {scope}), market AS (SELECT percentile_cont(.5) WITHIN GROUP(ORDER BY mid) med FROM scoped WHERE mid IS NOT NULL)
              SELECT s.skill_id::text,s.skill_slug,s.skill_name,COUNT(DISTINCT sc.job_id)::int,
                percentile_cont(.5) WITHIN GROUP(ORDER BY sc.mid) FILTER(WHERE sc.mid IS NOT NULL),
                percentile_cont(.25) WITHIN GROUP(ORDER BY sc.mid) FILTER(WHERE sc.mid IS NOT NULL),
                percentile_cont(.75) WITHIN GROUP(ORDER BY sc.mid) FILTER(WHERE sc.mid IS NOT NULL),(SELECT med FROM market)
              FROM scoped sc JOIN job_skills js ON js.job_id=sc.job_id JOIN skills s ON s.skill_id=js.skill_id
              GROUP BY s.skill_id,s.skill_slug,s.skill_name HAVING COUNT(DISTINCT sc.job_id)>=5""")
            cur.execute("TRUNCATE public.seo_company_index, public.seo_skill_index")
            cur.execute("INSERT INTO public.seo_company_index SELECT * FROM seo_company_next")
            cur.execute("INSERT INTO public.seo_skill_index SELECT * FROM seo_skill_next")
            return count
    finally:
        conn.close()

if __name__ == "__main__":
    print({"seo_role_location_rows": refresh()})
