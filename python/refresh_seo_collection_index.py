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
            return count
    finally:
        conn.close()

if __name__ == "__main__":
    print({"seo_role_location_rows": refresh()})
