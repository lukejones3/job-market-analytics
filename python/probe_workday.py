#!/usr/bin/env python3
"""
probe_workday.py

Probes known Workday customers that aren't yet in discovered_companies.
Workday tenant URLs follow: company.myworkdayjobs.com/tenant/jobs
Tenant names are NOT predictable from company names — they must be known.

Usage:
    python python/probe_workday.py --apply
    python python/probe_workday.py --dry-run
"""

import os
import re
import time
import hashlib
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

REQUEST_DELAY   = 0.4
REQUEST_TIMEOUT = 8

TARGET_ROLE_RE = re.compile(
    r"data\s+(analyst|scientist|engineer|architect|steward|governance|quality|platform|ops)|"
    r"analytics\s+engineer|machine\s+learning|ml\s+engineer|ai\s+engineer|"
    r"applied\s+scientist|research\s+scientist|business\s+intelligence|"
    r"revenue\s+operations|revops|marketing\s+ops|sales\s+ops|"
    r"quantitative\s+(analyst|researcher|scientist)|"
    r"llm\s+engineer|data\s+infrastructure|bi\s+(analyst|engineer|developer)|"
    r"decision\s+scientist|data\s+science\s+manager|analytics\s+manager",
    re.IGNORECASE,
)

# Format: (company_name, subdomain, tenant_path)
# subdomain.myworkdayjobs.com/tenant_path/jobs
WORKDAY_CANDIDATES = [
    # Fortune 500 / large enterprise
    ("Accenture",           "accenture",        "Accenture/AccentureCareers/wd1"),
    ("Allstate",            "allstate",         "Allstate/Careers/wd1"),
    ("American Express",    "aexp",             "AXP/jobs/wd5"),
    ("Bank of America",     "bofa",             "BankofAmerica/Global_Careers/wd1"),
    ("Bayer",               "bayer",            "Bayer/Bayer/wd1"),
    ("Chevron",             "chevron",          "Chevron/Chevron_Careers/wd1"),
    ("Citigroup",           "citi",             "Citi/CityExt/wd1"),
    ("Colgate-Palmolive",   "colgate",          "ColgatePalmolive/ColgateExternal/wd1"),
    ("Deloitte",            "deloitte",         "Deloitte/External/wd1"),
    ("Dick's Sporting Goods","dickssportinggoods","dickssportinggoods/dsg_us/wd1"),
    ("Eli Lilly",           "lilly",            "Lilly/LillyCareers/wd1"),
    ("EY",                  "ey",               "EY/EYCareersite/wd1"),
    ("General Electric",    "ge",               "GE/External/wd1"),
    ("General Mills",       "generalmills",     "GeneralMills/External/wd1"),
    ("General Motors",      "generalmotors",    "GeneralMotors/Global_Careers/wd1"),
    ("HP",                  "hp",               "HP/ExternalCareerSite/wd1"),
    ("IBM",                 "ibm",              "IBM/External/wd1"),
    ("Johnson & Johnson",   "jnj",              "JNJ/RecruitingUSA/wd1"),
    ("JPMorgan Chase",      "jpmc",             "JPMORGANCHASE/External/wd1"),
    ("KPMG",                "kpmg",             "KPMG/External/wd1"),
    ("Lockheed Martin",     "lmco",             "LMCareers/LM_Careers/wd1"),
    ("McKinsey",            "mckinsey",         "McKinsey/Careers/wd1"),
    ("Medtronic",           "medtronic",        "medtronic/MedtronicCareers/wd1"),
    ("Merck",               "msd",              "MerckCo/Merck_Careers/wd1"),
    ("MetLife",             "metlife",          "MetLife/External/wd1"),
    ("Microsoft",           "microsoft",        "Microsoft/External/wd1"),
    ("Morgan Stanley",      "morganstanley",    "MORGANSTANLEY/External/wd1"),
    ("Nordstrom",           "nordstrom",        "Nordstrom/NordstromCareers/wd1"),
    ("Oracle",              "oracle",           "Oracle/External/wd1"),
    ("Puma",                "puma",             "PUMA/PUMA_Careers/wd1"),
    ("Raytheon",            "raytheon",         "raytheon/External/wd1"),
    ("Rolls-Royce",         "rollsroyce",       "RollsRoyce/External/wd1"),
    ("Siemens",             "siemens",          "Siemens/External/wd1"),
    ("TD Bank",             "td",               "TD_Bank/External/wd1"),
    ("Unilever",            "unilever",         "Unilever/External/wd1"),
    ("United Airlines",     "united",           "United/External/wd1"),
    ("United Health",       "unitedhealthgroup", "UHG/External/wd1"),
    ("UPS",                 "ups",              "UPS/External/wd1"),
    ("Warner Bros",         "warnerbros",       "WarnerBros/External/wd1"),
    ("Workiva",             "workiva",          "Workiva/External/wd1"),
    # Tech companies
    ("Airbnb",              "airbnb",           "Airbnb/External/wd1"),
    ("Atlassian",           "atlassian",        "Atlassian/External/wd1"),
    ("Autodesk",            "autodesk",         "Autodesk/External/wd1"),
    ("DocuSign",            "docusign",         "Docusign/External/wd1"),
    ("Dropbox",             "dropbox",          "Dropbox/Dropbox_External/wd1"),
    ("Expedia",             "expedia",          "Expedia/External/wd1"),
    ("GitHub",              "github",           "GitHub/GitHub/wd1"),
    ("HubSpot",             "hubspot",          "HubSpot/External/wd1"),
    ("Intuit",              "intuit",           "Intuit/External/wd1"),
    ("LinkedIn",            "linkedin",         "LinkedIn/External/wd1"),
    ("Lyft",                "lyft",             "Lyft/External/wd1"),
    ("Palo Alto Networks",  "paloaltonetworks",  "PaloAltoNetworks/External/wd1"),
    ("Reddit",              "reddit",           "Reddit/External/wd1"),
    ("ServiceNow",          "servicenow",       "ServiceNow/External/wd1"),
    ("Shopify",             "shopify",          "Shopify/External/wd1"),
    ("Slack",               "slack",            "Slack/External/wd1"),
    ("Splunk",              "splunk",           "Splunk/External/wd1"),
    ("Square",              "square",           "Square/External/wd1"),
    ("Twitch",              "twitch",           "Twitch/External/wd1"),
    ("Twitter/X",           "twitter",          "Twitter/External/wd1"),
    ("Uber",                "uber",             "Uber/External/wd1"),
    ("Unity",               "unity",            "Unity/External/wd1"),
    ("Zendesk",             "zendesk",          "Zendesk/External/wd1"),
    # Financial
    ("AIG",                 "aig",              "AIG/External/wd1"),
    ("Ameriprise",          "ameriprise",       "AmeripriseFinancial/External/wd1"),
    ("Charles Schwab",      "schwab",           "Schwab/External/wd1"),
    ("Fidelity",            "fidelityinvestments", "FidelityInvestments/External/wd1"),
    ("Goldman Sachs",       "gs",               "GoldmanSachs/External/wd1"),
    ("Hartford Financial",  "thehartford",      "TheHartford/External/wd1"),
    ("Invesco",             "invesco",          "Invesco/External/wd1"),
    ("Lincoln Financial",   "lincolnfinancial",  "LincolnFinancial/External/wd1"),
    ("Markel",              "markel",           "Markel/External/wd1"),
    ("MassMutual",          "massmutual",       "MassMutual/External/wd1"),
    ("Nationwide",          "nationwide",       "Nationwide/External/wd1"),
    ("New York Life",       "newyorklife",      "NewYorkLife/External/wd1"),
    ("Principal Financial", "principal",        "Principal/External/wd1"),
    ("Raymond James",       "raymondjames",     "RaymondJames/External/wd1"),
    ("State Farm",          "statefarm",        "StateFarm/External/wd1"),
    ("State Street",        "statestreet",      "StateStreet/External/wd1"),
    ("Sun Life",            "sunlife",          "SunLife/External/wd1"),
    ("Travelers",           "travelers",        "Travelers/External/wd1"),
    ("Unum",                "unum",             "Unum/External/wd1"),
    # Healthcare
    ("AbbVie",              "abbvie",           "AbbVie/External/wd1"),
    ("Aetna",               "aetna",            "Aetna/External/wd1"),
    ("Anthem",              "anthem",           "Anthem/External/wd1"),
    ("Becton Dickinson",    "bd",               "BD/External/wd1"),
    ("Bristol Myers",       "bms",              "BristolMyersSquibb/External/wd1"),
    ("Cardinal Health",     "cardinalhealth",   "CardinalHealth/External/wd1"),
    ("Edwards Lifesciences","edwards",          "Edwards/External/wd1"),
    ("HCA Healthcare",      "hcahealthcare",    "HCAhealthcare/External/wd1"),
    ("Intuitive Surgical",  "intuitive",        "IntuitiveSurgical/External/wd1"),
    ("Medline",             "medline",          "Medline/External/wd1"),
    ("Moderna",             "modernatx",        "ModernaTX/External/wd1"),
    ("Quest Diagnostics",   "questdiagnostics", "QuestDiagnostics/External/wd1"),
    ("Stryker",             "stryker",          "Stryker/External/wd1"),
    ("Zimmer Biomet",       "zimmerbiomet",     "ZimmerBiomet/External/wd1"),
    # Retail/Consumer
    ("Best Buy",            "bestbuy",          "BestBuy/External/wd1"),
    ("Costco",              "costco",           "Costco/External/wd1"),
    ("Dollar General",      "dollargeneral",    "DollarGeneral/External/wd1"),
    ("Gap",                 "gap",              "Gap/External/wd1"),
    ("Home Depot",          "homedepot",        "homedepot/External/wd1"),
    ("Kroger",              "kroger",           "Kroger/External/wd1"),
    ("Lowe's",              "lowes",            "lowes/External/wd1"),
    ("Macy's",              "macys",            "Macys/External/wd1"),
    ("McDonald's",          "mcd",              "MCD/External/wd1"),
    ("Starbucks",           "starbucks",        "Starbucks/External/wd1"),
    ("TJX Companies",       "tjx",              "TJX/External/wd1"),
    # Telecom/Media
    ("Charter",             "charter",          "Charter/External/wd1"),
    ("Comcast NBCUniversal","comcastnbcuniversal","comcastnbcuniversal/External/wd1"),
    ("Fox Corporation",     "foxcorporation",   "FoxCorporation/External/wd1"),
    ("ViacomCBS",           "paramount",        "Paramount/External/wd1"),
    ("Warner Media",        "warnermedia",      "WarnerMedia/External/wd1"),
]

def resolve_workday_instance(subdomain: str, tenant_name: str) -> tuple[str, str]:
    """
    Use Workday's vanity URL redirector to find the correct wd{N} instance.
    Returns (resolved_subdomain, tenant_name) or ("", "") if not found.
    """
    # Try common tenant name variants
    base = tenant_name.split("/")[0] if "/" in tenant_name else tenant_name
    candidates = [tenant_name, base, f"{subdomain}careers", f"{subdomain}Careers",
                  "External", "Careers", f"{subdomain}External"]

    for tenant in candidates:
        vanity_url = f"https://{subdomain}.myworkdayjobs.com/{tenant}"
        try:
            r = requests.get(vanity_url, allow_redirects=True, timeout=8,
                           headers={"User-Agent": "Mozilla/5.0"})
            resolved = r.url
            # Extract wd{N} subdomain from resolved URL
            import re as _re
            m = _re.search(r"https://([^.]+\.wd\d+)\.myworkdayjobs\.com/(?:[^/]+/)?([^/?\s]+)", resolved)
            if m:
                resolved_sub = m.group(1)  # e.g. accenture.wd103
                resolved_tenant = m.group(2)
                return resolved_sub, resolved_tenant
        except Exception:
            continue
    return "", ""


def probe_workday_tenant(subdomain: str, tenant_name: str) -> tuple[bool, int, str]:
    """
    Step 1: Resolve the correct wd{N} instance via redirect sniffing.
    Step 2: Hit the CXS API with the correct subdomain and tenant.
    Returns (has_roles, count, working_url_info)
    """
    resolved_sub, resolved_tenant = resolve_workday_instance(subdomain, tenant_name)

    if not resolved_sub:
        return False, 0, ""

    # Extract just the wd{N} part for the CXS URL
    # resolved_sub is like "accenture.wd103"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    # Try CXS API with resolved info
    tenant_candidates = [resolved_tenant, tenant_name,
                        tenant_name.split("/")[0] if "/" in tenant_name else tenant_name]

    for tenant in tenant_candidates:
        url = f"https://{resolved_sub}.myworkdayjobs.com/wday/cxs/{subdomain}/{tenant}/jobs"
        try:
            r = requests.post(
                url,
                json={"limit": 20, "offset": 0, "searchText": "data"},
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                data = r.json()
                jobs = data.get("jobPostings", [])
                if jobs is not None:
                    target = [j for j in jobs if TARGET_ROLE_RE.search(j.get("title", ""))]
                    return len(target) > 0, len(target), f"{resolved_sub}/{tenant}"
        except Exception:
            continue

    return False, 0, resolved_sub

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST"),
        port=os.getenv("PGPORT", "5432"),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--limit", type=int, default=200)
    args = ap.parse_args()

    apply = args.apply and not args.dry_run

    conn = get_conn()
    cur = conn.cursor(cursor_factory=DictCursor)

    # Load existing workday tokens
    cur.execute("SELECT board_token, company_name FROM discovered_companies WHERE ats_source = 'workday'")
    existing = {r["board_token"].lower(): r["company_name"] for r in cur.fetchall()}
    log.info(f"Existing Workday companies: {len(existing)}")

    found = 0
    with_roles = 0
    probed = 0

    for company_name, subdomain, tenant in WORKDAY_CANDIDATES:
        if probed >= args.limit:
            break

        # Check if already tracked
        token_key = f"{subdomain}/{tenant}".lower()
        simple_key = subdomain.lower()

        if any(simple_key in k for k in existing.keys()):
            log.info(f"  ⏭️  {company_name} — already tracked")
            continue

        log.info(f"  Probing {company_name} ({subdomain}.myworkdayjobs.com/{tenant})")
        has_roles, role_count, working_tenant = probe_workday_tenant(subdomain, tenant)
        probed += 1

        if has_roles:
            log.info(f"  ✅ {company_name} — {role_count} target roles (tenant: {working_tenant})")
            if apply:
                company_id = "C" + hashlib.md5(f"workday:{subdomain}".encode()).hexdigest()[:9]
                cur.execute("""
                    INSERT INTO discovered_companies
                        (company_id, company_name, ats_source, board_token,
                         active_roles, total_seen, discovery_source, enabled)
                    VALUES (%s, %s, 'workday', %s, %s, %s, 'workday_probe', true)
                    ON CONFLICT (ats_source, board_token) DO UPDATE SET
                        active_roles = EXCLUDED.active_roles,
                        last_seen_at = now()
                """, (company_id, company_name, f"{subdomain}/{working_tenant}",
                      role_count, role_count))
                conn.commit()
            with_roles += 1
        elif working_tenant:
            log.info(f"  ⚪ {company_name} — found tenant but no target roles")
        else:
            log.info(f"  ❌ {company_name} — tenant not found")

        found += 1
        time.sleep(REQUEST_DELAY)

    log.info(f"\n=== SUMMARY ===")
    log.info(f"Probed: {probed}")
    log.info(f"With target roles: {with_roles}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
