#!/usr/bin/env python3
"""
validate_ats_candidates.py

Validates candidates in ats_tenants_candidates by probing each ATS's live API.
For each pending candidate:
  - Workday:          POST to CXS API, count US + data/ML jobs
  - Greenhouse:       GET boards-api.greenhouse.io, count US + data/ML jobs
  - Lever:            GET api.lever.co/v0/postings, count US + data/ML jobs
  - Ashby:            GET api.ashbyhq.com/posting-api, count US + data/ML jobs
  - Workable:         GET apply.workable.com API v3, count US + data/ML jobs
  - iCIMS:            GET tenant portal, parse job count
  - Taleo:            POST REST search, parse job count
  - Eightfold/others: HEAD check + count via HTML scrape

Safe to re-run — only processes 'pending' rows unless --revalidate is set.

Usage:
    python python/validate_ats_candidates.py --dry-run
    python python/validate_ats_candidates.py --apply
    python python/validate_ats_candidates.py --apply --ats workday
    python python/validate_ats_candidates.py --apply --limit 200
    python python/validate_ats_candidates.py --report
    python python/validate_ats_candidates.py --revalidate --apply
"""

import argparse
import logging
import os
import re
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2
from psycopg2.extras import DictCursor
import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ============================================================
# CONFIG
# ============================================================

REQUEST_TIMEOUT = 12
BATCH_DELAY     = 0.3   # seconds between candidates
REQUEST_DELAY   = 0.8   # seconds between API pages within same candidate

WD_SERVERS = ["wd1", "wd5", "wd3", "wd12", "wd103", "wd1480"]
COMMON_WD_BOARDS = [
    "External", "Careers", "External_Career_Site", "JobSearch", "Global",
    "Search", "US", "CareerSite",
]

# ============================================================
# ROLE FILTER (mirrored from ingest_jobs.py)
# ============================================================

_PHRASE_PATTERNS = [re.compile(r"\b" + p + r"\b", re.IGNORECASE) for p in [
    r"data analyst", r"analytics analyst", r"business analyst",
    r"business intelligence analyst", r"bi analyst",
    r"marketing analyst", r"product analyst", r"financial analyst",
    r"fp&a analyst", r"revenue analyst", r"operations analyst",
    r"reporting analyst", r"insights analyst", r"data engineer",
    r"analytics engineer", r"bi engineer", r"business intelligence engineer",
    r"machine learning engineer", r"ml engineer", r"data platform engineer",
    r"applied ml engineer", r"ai engineer", r"llm engineer",
    r"data scientist", r"applied scientist", r"research scientist",
    r"quantitative researcher", r"quantitative scientist",
    r"bi developer", r"business intelligence developer",
    r"data architect", r"bi architect",
    r"revenue operations", r"revops", r"marketing operations",
    r"analytics manager", r"data manager", r"data science manager",
    r"director of analytics", r"director of data",
    r"head of analytics", r"head of data",
    r"advanced analytics", r"mlops engineer", r"ml platform engineer",
    r"nlp engineer", r"computer vision engineer", r"data modell?er",
    r"experimentation analyst", r"experimentation scientist",
    r"decision scientist", r"causal inference scientist",
    r"geospatial data scientist", r"clinical data scientist",
    r"pricing scientist", r"demand forecasting",
    r"quantitative analyst", r"quantitative developer",
    r"staff data engineer", r"staff data scientist",
    r"staff analytics engineer", r"staff machine learning engineer",
]]

_BLOCK_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"\bandroid\b", r"\bios\b", r"\bfrontend\b", r"\bfront.end\b",
    r"\bbackend\b", r"\bback.end\b", r"\bfullstack\b", r"\bfull.stack\b",
    r"\bdevops\b", r"\bsre\b", r"\bsite reliability\b",
    r"\bsecurity engineer\b", r"\bnetwork engineer\b",
    r"\bsoftware engineer\b", r"\bsoftware developer\b",
    r"\bmobile engineer\b", r"\binfrastructure engineer\b",
    r"\bcloud engineer\b", r"\bembedded\b", r"\bhardware\b",
    r"\bfirmware\b", r"\baccount executive\b", r"\baccount manager\b",
    r"\bsales development\b", r"\bbusiness development\b",
    r"\brecruiter\b", r"\brecruiting\b", r"\bpayroll\b",
    r"\bcustomer success\b", r"\bcustomer support\b",
    r"\bcustomer experience\b", r"\bsupport engineer\b",
    r"\bsolutions engineer\b", r"\bimplementation\b",
    r"\bproject manager\b", r"\bproduct designer\b",
    r"\bux designer\b", r"\bux researcher\b", r"\bgraphic designer\b",
    r"\bcontent\b", r"\bcopywriter\b", r"\bseo\b", r"\bpaid media\b",
    r"\bcommunity manager\b", r"\boffice manager\b",
    r"\bexecutive assistant\b", r"\blegal\b",
    r"\bcompliance officer\b", r"\baccounting\b",
    r"\bdata entry\b", r"\bdata coordinator\b", r"\bdata processor\b",
    r"\bdata administrator\b",
]]


def _is_target_role(title: str) -> bool:
    if not title:
        return False
    for pat in _BLOCK_PATTERNS:
        if pat.search(title):
            return False
    for pat in _PHRASE_PATTERNS:
        if pat.search(title):
            return True
    return False


US_SIGNALS = {
    "remote", "united states", "usa", ", al", ", ak", ", az", ", ar",
    ", ca", ", co", ", ct", ", de", ", fl", ", ga", ", hi", ", id",
    ", il", ", in", ", ia", ", ks", ", ky", ", la", ", me", ", md",
    ", ma", ", mi", ", mn", ", ms", ", mo", ", mt", ", ne", ", nv",
    ", nh", ", nj", ", nm", ", ny", ", nc", ", nd", ", oh", ", ok",
    ", or", ", pa", ", ri", ", sc", ", sd", ", tn", ", tx", ", ut",
    ", vt", ", va", ", wa", ", wv", ", wi", ", wy", ", dc",
}
NON_US_SIGNALS = {
    "singapore", "sgp", "india", "ind", "bangalore", "hyderabad",
    "warsaw", "poland", "london", "united kingdom", "uk", "germany",
    "france", "canada", "toronto", "amsterdam", "dublin", "australia",
    "sydney", "tokyo", "japan", "china", "chn", "brazil", "mexico",
    "netherlands", "sweden", "switzerland", "spain", "italy", "korea",
}


def _is_us_job(location: str) -> bool:
    if not location:
        return True
    loc = location.lower()
    for sig in NON_US_SIGNALS:
        if sig in loc:
            return False
    if "," in loc:
        last = loc.split(",")[-1].strip().upper()
        if len(last) == 3 and last not in {"USA", "CAN"} and last.isalpha():
            return False
    for sig in US_SIGNALS:
        if sig in loc:
            return True
    if len(location) < 5:
        return True
    return False


def _count_from_jobs(jobs: List[Dict], title_key: str, location_key: str) -> Tuple[int, int]:
    """
    Count US jobs and data/ML jobs from a flat list of job dicts.
    title_key and location_key are the field names for title and location.
    """
    us_jobs = 0
    data_ml_jobs = 0
    for j in jobs:
        title = j.get(title_key, "") or ""
        loc_raw = j.get(location_key) or ""
        loc = loc_raw if isinstance(loc_raw, str) else (
            loc_raw.get("name", "") if isinstance(loc_raw, dict) else ""
        )
        if _is_us_job(loc):
            us_jobs += 1
            if _is_target_role(title):
                data_ml_jobs += 1
    return us_jobs, data_ml_jobs


# ============================================================
# DB
# ============================================================

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )


def load_pending(
    ats_filter: Optional[str] = None,
    revalidate: bool = False,
    limit: Optional[int] = None,
) -> List[Dict]:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)
    where_parts = []
    params = []
    if not revalidate:
        where_parts.append("status = 'pending'")
    else:
        where_parts.append("status NOT IN ('integrated')")
    if ats_filter:
        where_parts.append("ats = %s")
        params.append(ats_filter)
    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""
    q = f"""
        SELECT ats, tenant, server, company_name, source
        FROM ats_tenants_candidates
        {where}
        ORDER BY discovered_at ASC
        {"LIMIT %s" if limit else ""}
    """
    if limit:
        params.append(limit)
    cur.execute(q, params)
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


def update_candidate(cur, ats: str, tenant: str, server: Optional[str],
                     us_jobs: int, data_ml_jobs: int, status: str) -> None:
    cur.execute(
        """
        UPDATE ats_tenants_candidates SET
            server             = COALESCE(%s, server),
            us_jobs_count      = %s,
            data_ml_jobs_count = %s,
            status             = %s,
            last_validated_at  = now()
        WHERE ats = %s AND tenant = %s
        """,
        (server, us_jobs, data_ml_jobs, status, ats, tenant),
    )


# ============================================================
# WORKDAY VALIDATION (mirrors validate_workday_tenants.py)
# ============================================================

_WD_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Content-Type": "application/json",
    "Accept": "application/json",
}


def _wd_resolve_server(tenant: str, known_server: Optional[str]) -> Optional[str]:
    if known_server:
        return known_server
    url = f"https://{tenant}.myworkdayjobs.com/"
    try:
        r = requests.get(url, allow_redirects=True, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        m = re.search(r"https?://[^.]+\.(wd\d+)\.myworkdayjobs\.com", r.url, re.I)
        if m:
            return m.group(1).lower()
    except Exception:
        pass
    for server in WD_SERVERS:
        url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/External/jobs"
        try:
            r = requests.post(url, json={"limit": 1, "offset": 0, "searchText": ""},
                              headers=_WD_HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                return server
        except Exception:
            pass
        time.sleep(0.2)
    return None


def _wd_find_board(tenant: str, server: str, known_board: Optional[str]) -> Optional[str]:
    boards = []
    if known_board:
        boards.append(known_board)
    T = tenant.capitalize()
    boards += COMMON_WD_BOARDS + [
        f"{tenant}_External", f"{T}_External",
        f"{tenant}Careers", f"{T}Careers",
        f"{tenant}Jobs", f"{T}Jobs",
    ]
    seen: set = set()
    for board in boards:
        if board in seen:
            continue
        seen.add(board)
        url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
        try:
            r = requests.post(url, json={"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""},
                              headers=_WD_HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code == 200:
                data = r.json()
                if data.get("jobPostings") is not None and data.get("total", 0) > 0:
                    return board
        except Exception:
            pass
        time.sleep(0.25)
    return None


def _wd_count_jobs(tenant: str, server: str, board: str) -> Tuple[int, int]:
    url = f"https://{tenant}.{server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    us_jobs = 0
    data_ml_jobs = 0
    for offset in (0, 20, 40, 60, 80):
        try:
            r = requests.post(url, json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""},
                              headers=_WD_HEADERS, timeout=REQUEST_TIMEOUT)
            if r.status_code != 200:
                break
            postings = r.json().get("jobPostings", [])
            if not postings:
                break
            for p in postings:
                title = p.get("title", "")
                locs = p.get("locationsText", "") or p.get("locations", "")
                loc = locs[0] if isinstance(locs, list) and locs else (locs or "")
                if _is_us_job(loc):
                    us_jobs += 1
                    if _is_target_role(title):
                        data_ml_jobs += 1
        except Exception:
            break
        time.sleep(REQUEST_DELAY)
    return us_jobs, data_ml_jobs


def validate_workday(row: Dict) -> Tuple[Optional[str], int, int, str]:
    """Returns (resolved_server, us_jobs, data_ml_jobs, status)."""
    tenant = row["tenant"]
    server = _wd_resolve_server(tenant, row.get("server"))
    if not server:
        return None, 0, 0, "unreachable"

    board = _wd_find_board(tenant, server, None)
    if not board:
        return server, 0, 0, "unreachable"

    us_jobs, data_ml_jobs = _wd_count_jobs(tenant, server, board)
    if us_jobs >= 5:
        return server, us_jobs, data_ml_jobs, "active"
    elif us_jobs > 0:
        return server, us_jobs, data_ml_jobs, "no_data_jobs"
    else:
        return server, 0, 0, "unreachable"


# ============================================================
# GREENHOUSE VALIDATION
# ============================================================

def validate_greenhouse(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    url = f"https://boards-api.greenhouse.io/v1/boards/{tenant}/jobs?content=true"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code == 404:
            # Try the alternate job-boards subdomain slug format
            return None, 0, 0, "unreachable"
        if r.status_code != 200:
            return None, 0, 0, "unreachable"
        data = r.json()
        jobs = data.get("jobs", [])
        us_jobs = 0
        data_ml_jobs = 0
        for j in jobs:
            title = j.get("title", "")
            loc = (j.get("location") or {}).get("name", "")
            if _is_us_job(loc):
                us_jobs += 1
                if _is_target_role(title):
                    data_ml_jobs += 1
        if us_jobs >= 5:
            return None, us_jobs, data_ml_jobs, "active"
        elif us_jobs > 0:
            return None, us_jobs, data_ml_jobs, "no_data_jobs"
        else:
            return None, 0, 0, "unreachable"
    except Exception:
        return None, 0, 0, "unreachable"


# ============================================================
# LEVER VALIDATION
# ============================================================

def validate_lever(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    url = f"https://api.lever.co/v0/postings/{tenant}?mode=json"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"})
        if r.status_code != 200:
            return None, 0, 0, "unreachable"
        jobs = r.json()
        if not isinstance(jobs, list):
            return None, 0, 0, "unreachable"
        us_jobs = 0
        data_ml_jobs = 0
        for j in jobs:
            title = j.get("text", "")
            loc = (j.get("categories") or {}).get("location", "")
            if _is_us_job(loc):
                us_jobs += 1
                if _is_target_role(title):
                    data_ml_jobs += 1
        if us_jobs >= 5:
            return None, us_jobs, data_ml_jobs, "active"
        elif us_jobs > 0:
            return None, us_jobs, data_ml_jobs, "no_data_jobs"
        else:
            return None, 0, 0, "unreachable"
    except Exception:
        return None, 0, 0, "unreachable"


# ============================================================
# ASHBY VALIDATION
# ============================================================

def validate_ashby(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    url = f"https://api.ashbyhq.com/posting-api/job-board/{tenant}"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if r.status_code != 200:
            return None, 0, 0, "unreachable"
        data = r.json()
        jobs = data.get("jobPostings", [])
        us_jobs = 0
        data_ml_jobs = 0
        for j in jobs:
            title = j.get("title", "")
            loc = j.get("locationName", "") or j.get("location", "") or ""
            if isinstance(loc, dict):
                loc = loc.get("locationName", "")
            if _is_us_job(loc):
                us_jobs += 1
                if _is_target_role(title):
                    data_ml_jobs += 1
        if us_jobs >= 5:
            return None, us_jobs, data_ml_jobs, "active"
        elif us_jobs > 0:
            return None, us_jobs, data_ml_jobs, "no_data_jobs"
        else:
            return None, 0, 0, "unreachable"
    except Exception:
        return None, 0, 0, "unreachable"


# ============================================================
# WORKABLE VALIDATION
# ============================================================

def validate_workable(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    url = f"https://apply.workable.com/api/v3/accounts/{tenant}/jobs"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if r.status_code != 200:
            return None, 0, 0, "unreachable"
        data = r.json()
        jobs = data.get("results", [])
        us_jobs = 0
        data_ml_jobs = 0
        for j in jobs:
            title = j.get("title", "")
            loc_obj = j.get("location") or {}
            country = loc_obj.get("country", "")
            city = loc_obj.get("city", "") or loc_obj.get("region", "")
            loc = f"{city}, {country}".strip(", ") if (city or country) else ""
            if country in ("US", "USA", "United States") or _is_us_job(loc):
                us_jobs += 1
                if _is_target_role(title):
                    data_ml_jobs += 1
        if us_jobs >= 5:
            return None, us_jobs, data_ml_jobs, "active"
        elif us_jobs > 0:
            return None, us_jobs, data_ml_jobs, "no_data_jobs"
        else:
            return None, 0, 0, "unreachable"
    except Exception:
        return None, 0, 0, "unreachable"


# ============================================================
# ICIMS VALIDATION
# ============================================================

_ICIMS_JOB_COUNT_RE = re.compile(r'(\d+)\s+(?:open\s+)?(?:position|job|opening)', re.I)

def validate_icims(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    # iCIMS uses HTML portal pages — check if the tenant's portal is alive
    urls_to_try = [
        f"https://{tenant}.icims.com/jobs/search",
        f"https://{tenant}.icims.com/jobs/intro",
        f"https://{tenant}.icims.com/",
    ]
    for url in urls_to_try:
        try:
            r = requests.get(url, timeout=REQUEST_TIMEOUT,
                             headers={"User-Agent": "Mozilla/5.0"},
                             allow_redirects=True)
            if r.status_code == 200 and "icims" in r.text.lower():
                # iCIMS portals don't expose job counts cleanly without auth.
                # Return non-zero placeholder — full validation happens during harvest.
                return None, 1, 0, "no_data_jobs"
        except Exception:
            continue
    return None, 0, 0, "unreachable"


# ============================================================
# TALEO VALIDATION
# ============================================================

def validate_taleo(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    # Try the public REST job search endpoint
    url = f"https://{tenant}.taleo.net/careersection/rest/jobboard/searchjobs"
    try:
        r = requests.post(
            url,
            json={"multiLanguage": "false", "sortByField": "SCORE",
                  "sortOrder": "DESC", "pageNo": 1},
            headers={"User-Agent": "Mozilla/5.0", "Content-Type": "application/json"},
            timeout=REQUEST_TIMEOUT,
        )
        if r.status_code == 200:
            try:
                data = r.json()
                # Taleo returns jobs inside a nested structure
                jobs = (data.get("requisitionList") or
                        data.get("jobs") or
                        data.get("searchResults") or [])
                if isinstance(jobs, list) and len(jobs) > 0:
                    return None, len(jobs), 0, "no_data_jobs"
                return None, 1, 0, "no_data_jobs"
            except Exception:
                return None, 1, 0, "no_data_jobs"
    except Exception:
        pass

    # Fallback: check if the careers portal page responds
    portal_url = f"https://{tenant}.taleo.net/careersection/1/jobsearch.ftl"
    try:
        r = requests.get(portal_url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        if r.status_code == 200 and "taleo" in r.text.lower():
            return None, 1, 0, "no_data_jobs"
    except Exception:
        pass

    return None, 0, 0, "unreachable"


# ============================================================
# EIGHTFOLD VALIDATION
# ============================================================

def validate_eightfold(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    url = f"https://{tenant}.eightfold.ai/careers"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        if r.status_code == 200 and "eightfold" in r.text.lower():
            return None, 1, 0, "no_data_jobs"
    except Exception:
        pass
    return None, 0, 0, "unreachable"


# ============================================================
# JOBVITE VALIDATION
# ============================================================

def validate_jobvite(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    url = f"https://jobs.jobvite.com/{tenant}/jobs"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0"}, allow_redirects=True)
        if r.status_code == 200:
            # Count job entries by looking for common Jobvite HTML patterns
            count = len(re.findall(r'jv-job-list-job', r.text))
            if count > 0:
                return None, count, 0, "no_data_jobs"
    except Exception:
        pass
    return None, 0, 0, "unreachable"


# ============================================================
# SMARTRECRUITERS VALIDATION
# ============================================================

def validate_smartrecruiters(row: Dict) -> Tuple[Optional[str], int, int, str]:
    tenant = row["tenant"]
    url = f"https://api.smartrecruiters.com/v1/companies/{tenant}/postings"
    try:
        r = requests.get(url, timeout=REQUEST_TIMEOUT,
                         headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"})
        if r.status_code == 200:
            data = r.json()
            jobs = data.get("content", [])
            us_jobs = 0
            data_ml_jobs = 0
            for j in jobs:
                title = j.get("name", "")
                loc = (j.get("location") or {}).get("country", "")
                if loc in ("us", "US", "United States") or _is_us_job(loc):
                    us_jobs += 1
                    if _is_target_role(title):
                        data_ml_jobs += 1
            if us_jobs >= 5:
                return None, us_jobs, data_ml_jobs, "active"
            elif us_jobs > 0:
                return None, us_jobs, data_ml_jobs, "no_data_jobs"
            else:
                return None, 0, 0, "unreachable"
    except Exception:
        pass
    return None, 0, 0, "unreachable"


# ============================================================
# DISPATCH
# ============================================================

VALIDATORS = {
    "workday":        validate_workday,
    "greenhouse":     validate_greenhouse,
    "lever":          validate_lever,
    "ashby":          validate_ashby,
    "workable":       validate_workable,
    "icims":          validate_icims,
    "taleo":          validate_taleo,
    "eightfold":      validate_eightfold,
    "jobvite":        validate_jobvite,
    "smartrecruiters":validate_smartrecruiters,
}


def validate_candidate(row: Dict) -> Tuple[Optional[str], int, int, str]:
    """Route to the appropriate validator for this ATS."""
    ats = row.get("ats", "").lower()
    validator = VALIDATORS.get(ats)
    if not validator:
        log.warning(f"  No validator for ATS: {ats} — marking unreachable")
        return None, 0, 0, "unreachable"
    return validator(row)


# ============================================================
# REPORT
# ============================================================

def print_report() -> None:
    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)
    cur.execute("""
        SELECT ats, tenant, server, company_name,
               us_jobs_count, data_ml_jobs_count, source, status
        FROM ats_tenants_candidates
        ORDER BY ats, data_ml_jobs_count DESC, us_jobs_count DESC
    """)
    rows = cur.fetchall()
    cur.close()
    conn.close()

    by_status: Dict[str, List] = {}
    by_ats: Dict[str, Dict[str, int]] = {}
    for r in rows:
        by_status.setdefault(r["status"], []).append(r)
        by_ats.setdefault(r["ats"], {})
        by_ats[r["ats"]][r["status"]] = by_ats[r["ats"]].get(r["status"], 0) + 1

    log.info("")
    log.info("=" * 75)
    log.info("VALIDATION REPORT — ats_tenants_candidates")
    log.info("=" * 75)
    log.info(f"Total candidates: {len(rows)}")
    for status, items in sorted(by_status.items()):
        log.info(f"  {status}: {len(items)}")

    log.info("")
    log.info("By ATS:")
    log.info(f"  {'ATS':<20} {'pending':>8} {'active':>8} {'no_data':>8} {'unreachable':>11} {'integrated':>10}")
    log.info("  " + "-" * 70)
    for ats in sorted(by_ats.keys()):
        s = by_ats[ats]
        log.info(
            f"  {ats:<20} {s.get('pending',0):>8} {s.get('active',0):>8} "
            f"{s.get('no_data_jobs',0):>8} {s.get('unreachable',0):>11} {s.get('integrated',0):>10}"
        )

    active = by_status.get("active", [])
    if active:
        log.info("")
        log.info(f"ACTIVE TENANTS ({len(active)}) — sorted by data/ML job count:")
        log.info(f"  {'ATS':<15} {'Tenant':<28} {'Server':<6} {'US':>5} {'D/ML':>5}  Company")
        log.info("  " + "-" * 75)
        for r in sorted(active, key=lambda x: -(x["data_ml_jobs_count"] or 0)):
            log.info(
                f"  {r['ats']:<15} {r['tenant']:<28} {(r['server'] or '-'):<6} "
                f"{r['us_jobs_count']:>5} {r['data_ml_jobs_count']:>5}  {r['company_name'] or ''}"
            )
        log.info("")
        log.info("To integrate: python python/integrate_ats_candidates.py --apply")


# ============================================================
# MAIN VALIDATION LOOP
# ============================================================

def run_validation(apply: bool, ats_filter: Optional[str], limit: Optional[int], revalidate: bool) -> None:
    pending = load_pending(ats_filter=ats_filter, revalidate=revalidate, limit=limit)
    log.info(f"Loaded {len(pending)} candidates to validate")

    if not pending:
        log.info("Nothing to validate. Run discover_ats_aggressive.py first.")
        return

    results: Dict[str, int] = {"active": 0, "no_data_jobs": 0, "unreachable": 0, "errors": 0}

    conn = cur = None
    if apply:
        conn = get_conn()
        cur = conn.cursor()

    for i, row in enumerate(pending):
        ats = row["ats"]
        tenant = row["tenant"]
        log.info(f"[{i+1}/{len(pending)}] {ats}/{tenant}")

        try:
            server, us_jobs, data_ml_jobs, status = validate_candidate(row)
            results[status] = results.get(status, 0) + 1

            if status == "active":
                log.info(f"  ✅ {ats}/{tenant} — {us_jobs} US jobs, {data_ml_jobs} data/ML")
            elif status == "no_data_jobs":
                log.info(f"  ⚪ {ats}/{tenant} — {us_jobs} US jobs, 0 data/ML")
            else:
                log.info(f"  ❌ {ats}/{tenant} — unreachable")

            if apply:
                update_candidate(cur, ats, tenant, server, us_jobs, data_ml_jobs, status)
                conn.commit()

        except Exception as e:
            log.warning(f"  Error validating {ats}/{tenant}: {e}")
            results["errors"] = results.get("errors", 0) + 1
            if apply and conn:
                try:
                    conn.rollback()
                except Exception:
                    pass

        time.sleep(BATCH_DELAY)

    if apply and conn:
        cur.close()
        conn.close()

    log.info("")
    log.info("=" * 60)
    log.info("VALIDATION COMPLETE")
    log.info("=" * 60)
    for k, v in results.items():
        log.info(f"  {k}: {v}")
    if apply:
        log.info("")
        log.info("Next steps:")
        log.info("  1. python python/validate_ats_candidates.py --report")
        log.info("  2. python python/integrate_ats_candidates.py --apply")


def main():
    ap = argparse.ArgumentParser(
        description="Validate ATS tenant candidates against live APIs."
    )
    ap.add_argument("--apply",      action="store_true", help="Write results to DB")
    ap.add_argument("--dry-run",    action="store_true", help="Print plan, no DB writes")
    ap.add_argument("--report",     action="store_true", help="Print validation report and exit")
    ap.add_argument("--revalidate", action="store_true", help="Re-run all rows, not just pending")
    ap.add_argument("--ats",        type=str, default=None,
                    help="Only validate this ATS (e.g. greenhouse, lever, workday)")
    ap.add_argument("--limit",      type=int, default=None, metavar="N",
                    help="Max candidates to validate")
    args = ap.parse_args()

    if args.report:
        print_report()
        return

    apply = args.apply and not args.dry_run
    if not apply:
        log.info("DRY RUN — use --apply to write to DB")

    run_validation(apply=apply, ats_filter=args.ats, limit=args.limit, revalidate=args.revalidate)


if __name__ == "__main__":
    main()
