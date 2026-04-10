#!/usr/bin/env python3
"""
ingest_jobs.py

Multi-source job ingestion pipeline.
Sources:
  1. Greenhouse (free, no auth, full descriptions)
  2. Lever     (free, no auth, full descriptions)
  3. Adzuna    (API key required, partial descriptions — discovery layer)

Usage:
    python python/ingest_jobs.py --apply
    python python/ingest_jobs.py --apply --source greenhouse
    python python/ingest_jobs.py --apply --source lever
    python python/ingest_jobs.py --apply --source adzuna
    python python/ingest_jobs.py --dry-run        (default, no DB writes)

Nightly cron (add to crontab with: crontab -e):
    0 2 * * * cd /path/to/your/repo && python python/ingest_jobs.py --apply >> logs/ingest.log 2>&1

Drop this file in: python/ingest_jobs.py
"""

import hashlib
import json
import logging
import os
import re
import sys
import argparse
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Tuple
from dataclasses import dataclass, field

import requests
from urllib.parse import urlparse
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

# ============================================================
# CONFIG
# ============================================================

# Request throttle — be a good citizen, don't hammer APIs
REQUEST_DELAY_SECONDS = 0.4

# How many Adzuna pages to pull per search term (each page = 50 results)
ADZUNA_MAX_PAGES = 4

# Adzuna country code
ADZUNA_COUNTRY = "us"

# ---- Target role search terms ----
# These drive what gets pulled from every source.
# Tune this list to your target market.
TARGET_ROLES = [
    "data analyst",
    "business intelligence analyst",
    "analytics engineer",
    "data engineer",
    "marketing analyst",
    "product analyst",
    "FP&A analyst",
    "revenue operations analyst",
    "reporting analyst",
    "BI developer",
    "junior data analyst",
    "associate data analyst",
    "associate data engineer",
    "data scientist",
    "machine learning engineer",
    "ml engineer",
    "quantitative analyst",
    "research scientist",
    "applied scientist",
    "data architect",
    "analytics manager",
    "data science manager",
    "staff data engineer",
    "staff data scientist",
    "people analyst",
    "pricing analyst",
    "fraud analyst",
    "risk analyst",
    "supply chain analyst",
    "revenue analyst",
    "clinical data analyst",
    "ai engineer",
    "mlops engineer",
    "data governance analyst",
]

# ---- Greenhouse companies to pull from ----
# These are companies known to use Greenhouse AND hire data/analytics roles.
# Expand this list over time as you discover more.
# Format: (display_name, greenhouse_board_token)
GREENHOUSE_COMPANIES: List[Tuple[str, str]] = [
    ("Stripe",          "stripe"),
    ("Airbnb",          "airbnb"),
    ("Notion",          "notion"),
    ("Figma",           "figma"),
    ("Robinhood",       "robinhood"),
    ("Coinbase",        "coinbase"),
    ("Brex",            "brex"),
    ("Gusto",           "gusto"),
    ("Amplitude",       "amplitude"),
    ("Mixpanel",        "mixpanel"),
    ("dbt Labs",        "dbtlabs"),
    ("Snowflake",       "snowflake"),
    ("Databricks",      "databricks"),
    ("Looker",          "looker"),
    ("Fivetran",        "fivetran"),
    ("Monte Carlo",     "montecarlodata"),
    ("Hex",             "hex"),
    ("Census",          "census"),
    ("Hightouch",       "hightouch"),
    ("Starburst",       "starburstdata"),
    ("Weights & Biases","wandb"),
    ("Scale AI",        "scaleai"),
    ("Replit",          "replit"),
    ("Linear",          "linear"),
    ("Retool",          "retool"),
    ("Airtable",        "airtable"),
    ("Asana",           "asana"),
    ("Zendesk",         "zendesk"),
    ("HubSpot",         "hubspot"),
    ("Klaviyo",         "klaviyo"),
    ("Affirm",          "affirm"),
    ("Plaid",           "plaid"),
    ("Checkr",          "checkr"),
    ("Benchling",       "benchling"),
    ("Intercom",        "intercom"),
    ("Greenhouse",      "greenhouse"),
    ("Lever",           "lever"),
]

# ---- Lever companies to pull from ----
# Format: (display_name, lever_company_slug)
LEVER_COMPANIES: List[Tuple[str, str]] = [
    # --- Original (deduped) ---
    ("Canva",            "canva"),
    ("Shopify",          "shopify"),
    ("Duolingo",         "duolingo"),
    ("Coursera",         "coursera"),
    ("Chime",            "chime"),
    ("Handshake",        "handshake"),
    ("OpenTable",        "opentable"),
    ("SeatGeek",         "seatgeek"),
    ("Squarespace",      "squarespace"),
    ("Faire",            "faire"),
    ("Noom",             "noom"),
    ("Spring Health",    "springhealth"),
    ("Lucidchart",       "lucid"),
    ("ClickUp",          "clickup"),
    ("Loom",             "loom"),
    ("Vercel",           "vercel"),
    ("Rippling",         "rippling"),
    ("Lattice",          "lattice"),
    ("Culture Amp",      "cultureamp"),
    ("Workiva",          "workiva"),
    ("Sigma Computing",  "sigmacomputing"),
    # --- Expanded ---
    ("Pendo",            "pendo"),
    ("Amplitude",        "amplitude"),
    ("FullStory",        "fullstory"),
    ("Heap",             "heap"),
    ("PostHog",          "posthog"),
    ("LaunchDarkly",     "launchdarkly"),
    ("Split",            "split"),
    ("Optimizely",       "optimizely"),
    ("Statsig",          "statsig"),
    ("Eppo",             "eppo"),
    ("Streamlit",        "streamlit"),
    ("Prefect",          "prefect"),
    ("Census",           "census"),
    ("Rudderstack",      "rudderstack"),
    ("Segment",          "segment"),
    ("mParticle",        "mparticle"),
    ("Iterable",         "iterable"),
    ("Braze",            "braze"),
    ("Attentive",        "attentive"),
    ("Sendbird",         "sendbird"),
    ("Twilio",           "twilio"),
    ("Vonage",           "vonage"),
    ("MessageBird",      "messagebird"),
    ("Intercom",         "intercom"),
    ("Zendesk",          "zendesk"),
    ("Freshworks",       "freshworks"),
    ("Medallia",         "medallia"),
    ("Qualtrics",        "qualtrics"),
    ("SurveyMonkey",     "surveymonkey"),
    ("UserTesting",      "usertesting"),
    ("UserZoom",         "userzoom"),
    ("Maze",             "maze"),
    ("Dovetail",         "dovetailapp"),
    ("Notion",           "notion"),
    ("Coda",             "coda"),
    ("Airtable",         "airtable"),
    ("Monday",           "mondaydotcom"),
    ("Asana",            "asana"),
    ("ClickUp",          "clickup"),
    ("Linear",           "linear"),
    ("Height",           "height"),
    ("Shortcut",         "shortcut"),
    ("Productboard",     "productboard"),
    ("Aha",              "aha"),
    ("Pendo",            "pendo"),
    ("Mixpanel",         "mixpanel"),
    ("Looker",           "looker"),
    ("Mode",             "modeanalytics"),
    ("Preset",           "preset"),
    ("Redash",           "redash"),
    ("Metabase",         "metabase"),
    ("Clari",            "clari"),
    ("Gong",             "gong"),
    ("Chorus",           "chorus"),
    ("Salesloft",        "salesloft"),
    ("Outreach",         "outreach"),
    ("Apollo",           "apolloio"),
    ("ZoomInfo",         "zoominfo"),
    ("Clearbit",         "clearbit"),
    ("Demandbase",       "demandbase"),
    ("6sense",           "6sense"),
    ("Bombora",          "bombora"),
    ("G2",               "g2crowd"),
    ("TrustRadius",      "trustradius"),
    ("Crossbeam",        "crossbeam"),
    ("Reveal",           "reveal"),
    ("Alliances",        "alliances"),
    ("Impartner",        "impartner"),
    ("Partnerstack",     "partnerstack"),
    ("Impact",           "impact"),
    ("Rakuten",          "rakuten"),
    ("Ascend",           "ascend"),
    ("Fivetran",         "fivetran"),
    ("Stitch",           "stitch"),
    ("Airbyte",          "airbyte"),
    ("Matillion",        "matillion"),
    ("Talend",           "talend"),
    ("Informatica",      "informatica"),
    ("Boomi",            "boomi"),
    ("MuleSoft",         "mulesoft"),
    ("Workato",          "workato"),
    ("Tray",             "tray"),
    ("Zapier",           "zapier"),
    ("Make",             "make"),
    ("n8n",              "n8n"),
    ("Retool",           "retool"),
    ("Appsmith",         "appsmith"),
    ("Budibase",         "budibase"),
    ("Tooljet",          "tooljet"),
    ("Internal",         "internal"),
    ("Airplane",         "airplanehq"),
    ("Adalo",            "adalo"),
    ("Bubble",           "bubble"),
    ("Webflow",          "webflow"),
    ("Framer",           "framer"),
    ("Draftbit",         "draftbit"),
    ("Glide",            "glideapps"),
    ("AppSheet",         "appsheet"),
    ("Rows",             "rows"),
    ("Grist",            "grist"),
    ("Equals",           "equals"),
    ("Causal",           "causal"),
    ("Pigment",          "pigment"),
    ("Anaplan",          "anaplan"),
    ("Adaptive Insights","adaptiveinsights"),
    ("Planful",          "planful"),
    ("Vena",             "venasolutions"),
    ("Prophix",          "prophix"),
    ("Board",            "board"),
    ("OneStream",        "onestreamsoftware"),
    ("Jedox",            "jedox"),
    ("Workday",          "workday"),
    ("Oracle",           "oracle"),
    ("SAP",              "sap"),
    ("Infor",            "infor"),
    ("Unit4",            "unit4"),
    ("Sage",             "sage"),
    ("Epicor",           "epicor"),
    ("IFS",              "ifs"),
    ("Syspro",           "syspro"),
    ("Acumatica",        "acumatica"),
    ("NetSuite",         "netsuite"),
    ("Xero",             "xero"),
    ("Freshbooks",       "freshbooks"),
    ("Wave",             "waveapps"),
    ("Bench",            "bench"),
    ("Pilot",            "pilot"),
    ("Decimal",          "decimal"),
    ("Settle",           "settle"),
    ("Rho",              "rho"),
    ("Mercury",          "mercury"),
    ("Brex",             "brex"),
    ("Ramp",             "ramp"),
    ("Airbase",          "airbase"),
    ("Spendesk",         "spendesk"),
    ("Pleo",             "pleo"),
    ("Soldo",            "soldo"),
    ("Payhawk",          "payhawk"),
    ("Moss",             "getmoss"),
    ("Yokoy",            "yokoy"),
    ("Expensify",        "expensify"),
    ("Concur",           "concur"),
    ("Coupa",            "coupa"),
    ("Procurify",        "procurify"),
    ("Zip",              "ziphq"),
    ("Ironclad",         "ironclad"),
    ("Docusign",         "docusign"),
    ("PandaDoc",         "pandadoc"),
    ("Juro",             "juro"),
    ("LinkSquares",      "linksquares"),
    ("Evisort",          "evisort"),
    ("ContractPodAi",    "contractpodai"),
    ("Icertis",          "icertis"),
    ("Conga",            "conga"),
    ("Apttus",           "apttus"),
    ("Zuora",            "zuora"),
    ("Chargebee",        "chargebee"),
    ("Recurly",          "recurly"),
    ("Paddle",           "paddle"),
    ("FastSpring",       "fastspring"),
    ("Stripe",           "stripe"),
    ("Adyen",            "adyen"),
    ("Checkout",         "checkout"),
    ("Braintree",        "braintree"),
    ("Square",           "square"),
    ("PayPal",           "paypal"),
    ("Klarna",           "klarna"),
    ("Affirm",           "affirm"),
    ("Afterpay",         "afterpay"),
    ("Sezzle",           "sezzle"),
    ("Splitit",          "splitit"),
    ("Bread",            "breadfinance"),
    ("Paidy",            "paidy"),
    ("Creditas",         "creditas"),
    ("Nubank",           "nubank"),
    ("Chime",            "chime"),
    ("Dave",             "dave"),
    ("Albert",           "albert"),
    ("Cleo",             "meetcleo"),
    ("Current",          "current"),
    ("One",              "one"),
    ("Step",             "step"),
    ("Greenlight",       "greenlight"),
    ("Copper",           "copper"),
    ("Acorns",           "acorns"),
    ("Stash",            "stash"),
    ("Betterment",       "betterment"),
    ("Wealthfront",      "wealthfront"),
    ("Robinhood",        "robinhood"),
    ("Public",           "public"),
    ("Webull",           "webull"),
    ("Moomoo",           "moomoo"),
    ("Tastytrade",       "tastytrade"),
    ("TradeStation",     "tradestation"),
    ("Interactive Brokers","interactivebrokers"),
    ("Alpaca",           "alpaca"),
    ("Tradier",          "tradier"),
    ("Ally",             "ally"),
    ("SoFi",             "sofi"),
    ("LendingClub",      "lendingclub"),
    ("Prosper",          "prosper"),
    ("Avant",            "avant"),
    ("Upstart",          "upstart"),
    ("Blend",            "blend"),
    ("Better",           "better"),
    ("Opendoor",         "opendoor"),
    ("Offerpad",         "offerpad"),
    ("Knock",            "knock"),
    ("Orchard",          "orchard"),
    ("Flyhomes",         "flyhomes"),
    ("Homeward",         "homeward"),
    ("Ribbon",           "ribbon"),
    ("Endpoint",         "endpoint"),
    ("Spruce",           "spruce"),
    ("States Title",     "statestitle"),
    ("Doma",             "doma"),
    ("Mynd",             "mynd"),
    ("Belong",           "belong"),
    ("Doorstead",        "doorstead"),
    ("Poplar",           "poplar"),
    ("Vacasa",           "vacasa"),
    ("Sonder",           "sonder"),
    ("Landing",          "landing"),
    ("Furnished Finder", "furnishedfinder"),
    ("Furnished Quarters","furnishedquarters"),
    ("Zeus Living",      "zeusliving"),
    ("Blueground",       "blueground"),
    ("Nestpick",         "nestpick"),
    ("HousingAnywhere",  "housinganywhere"),
    ("SpareRoom",        "spareroom"),
    ("Roomies",          "roomies"),
    ("Roomster",         "roomster"),
]

# ---- Target role phrases (title must match at least one, as whole words) ----
# These are specific enough to avoid false positives.
ROLE_TITLE_PHRASES = [
    r"data analyst", r"analytics analyst", r"business analyst",
    r"business intelligence analyst", r"bi analyst",
    r"marketing analyst", r"product analyst", r"financial analyst",
    r"fp&a analyst", r"revenue analyst", r"operations analyst",
    r"reporting analyst", r"insights analyst", r"supply chain analyst",
    r"pricing analyst", r"growth analyst", r"quantitative analyst",
    r"research analyst", r"strategy analyst", r"people analyst",
    r"workforce analyst", r"sales analyst", r"credit analyst",
    r"risk analyst", r"data science analyst", r"growth data analyst",
    r"decision analyst", r"decision scientist",
    r"customer insights analyst", r"consumer insights analyst",
    r"market research analyst", r"competitive intelligence analyst",
    r"web analyst", r"digital analyst", r"ecommerce analyst",
    r"retail analyst", r"inventory analyst", r"demand planning analyst",
    r"workforce planning analyst", r"compensation analyst",
    r"hr analyst", r"clinical data analyst", r"healthcare analyst",
    r"investment analyst", r"portfolio analyst", r"fraud analyst",
    r"data governance analyst", r"data quality analyst",
    r"data operations analyst", r"data steward",
    r"data engineer", r"analytics engineer", r"bi engineer",
    r"business intelligence engineer", r"machine learning engineer",
    r"ml engineer", r"data platform engineer",
    r"data infrastructure engineer", r"data reliability engineer",
    r"applied ml engineer", r"ai engineer", r"llm engineer",
    r"analytics platform engineer", r"ai/ml engineer",
    r"data scientist", r"applied scientist", r"research scientist",
    r"applied data scientist", r"quantitative researcher",
    r"quantitative scientist", r"principal data scientist",
    r"staff data scientist", r"machine learning scientist",
    r"applied researcher", r"ai researcher",
    r"founding data scientist", r"data science lead",
    r"bi developer", r"business intelligence developer",
    r"tableau developer", r"power bi developer",
    r"looker developer", r"reporting developer",
    r"data architect", r"bi architect", r"analytics architect",
    r"revenue operations", r"revops", r"marketing operations",
    r"sales operations",
    r"analytics manager", r"data manager", r"analytics lead",
    r"data lead", r"data science manager", r"data engineering manager",
    r"director of analytics", r"director of data",
    r"head of analytics", r"head of data",
    r"data specialist", r"analytics specialist",
    r"analytics consultant", r"data product manager",
    r"ai analyst", r"quantitative developer",
    r"staff data engineer", r"staff analytics engineer",
    r"staff machine learning engineer",
    # Emerging / missing roles
    r"data product analyst",
    r"analytics engineer",
    r"product data scientist",
    r"growth engineer",
    r"experimentation analyst",
    r"experimentation scientist",
    r"a/b testing analyst",
    r"causal inference scientist",
    r"decision scientist",
    r"inference engineer",
    r"prompt engineer",
    r"ai product manager",
    r"ml platform engineer",
    r"mlops engineer",
    r"feature engineer",
    r"data visualization engineer",
    r"data visualization analyst",
    r"geospatial analyst",
    r"geospatial data scientist",
    r"nlp engineer",
    r"nlp scientist",
    r"computer vision engineer",
    r"computer vision scientist",
    r"recommendation systems engineer",
    r"search engineer",
    r"search scientist",
    r"trust and safety analyst",
    r"integrity analyst",
    r"policy data scientist",
    r"clinical data scientist",
    r"health data scientist",
    r"actuarial analyst",
    r"econometrician",
    r"labor economist",
    r"people scientist",
    r"workforce scientist",
    r"talent analytics",
    r"supply chain data scientist",
    r"demand forecasting",
    r"pricing scientist",
    r"pricing data scientist",
    r"revenue data scientist",
]

# ---- Blocklist — titles containing these are NEVER target roles ----
# Catches false positives like "Android Engineer", "AV Operations", etc.
ROLE_TITLE_BLOCKLIST = [
    r"\bandroid\b",
    r"\bios\b",
    r"\bfrontend\b",
    r"\bfront.end\b",
    r"\bbackend\b",
    r"\bback.end\b",
    r"\bfullstack\b",
    r"\bfull.stack\b",
    r"\bdevops\b",
    r"\bsre\b",
    r"\bsite reliability\b",
    r"\bsecurity engineer\b",
    r"\bnetwork engineer\b",
    r"\bsoftware engineer\b",
    r"\bsoftware developer\b",
    r"\bmobile engineer\b",
    r"\binfrastructure engineer\b",
    r"\bplatform engineer\b",
    r"\bcloud engineer\b",
    r"\bembedded\b",
    r"\bhardware\b",
    r"\bfirmware\b",
    r"\bav builds\b",
    r"\baudio.visual\b",
    r"\baccount executive\b",
    r"\baccount manager\b",
    r"\bsales development\b",
    r"\bbusiness development\b",
    r"\brecruiter\b",
    r"\brecruiting\b",
    r"\bhr business\b",
    r"\bpeople partner\b",
    r"\bpayroll\b",
    r"\bcustomer success\b",
    r"\bcustomer support\b",
    r"\bcustomer experience\b",
    r"\bsupport engineer\b",
    r"\bsolutions engineer\b",
    r"\bimplementation\b",
    r"\bproject manager\b",
    r"\bprogram manager\b",
    r"\bproduct manager\b",
    r"\bproduct designer\b",
    r"\bux designer\b",
    r"\bux researcher\b",
    r"\bgraphic designer\b",
    r"\bcontent\b",
    r"\bcopywriter\b",
    r"\bseo\b",
    r"\bpaid media\b",
    r"\bcommunity manager\b",
    r"\bevent\b",
    r"\boffice manager\b",
    r"\bexecutive assistant\b",
    r"\blegal\b",
    r"\bcompliance\b",
    r"\baccounting\b",
    r"\bcontroller\b",
    r"\bcfo\b",
    r"\bcto\b",
    r"\bceo\b",
]

# Pre-compile for performance
_PHRASE_PATTERNS = [re.compile(r"\b" + p + r"\b", re.IGNORECASE) for p in ROLE_TITLE_PHRASES]
_BLOCK_PATTERNS  = [re.compile(p, re.IGNORECASE) for p in ROLE_TITLE_BLOCKLIST]

# ============================================================
# DATA STRUCTURES
# ============================================================

@dataclass
class RawJob:
    source:           str                    # greenhouse / lever / adzuna
    source_id:        str                    # ID from source system
    title:            str
    company:          str
    location:         Optional[str]          = None
    description:      Optional[str]          = None
    job_url:          Optional[str]          = None
    salary_min:       Optional[float]        = None
    salary_max:       Optional[float]        = None
    salary_period:    Optional[str]          = None
    workplace_type:   Optional[str]          = None
    employment_type:  Optional[str]          = None
    posted_date:      Optional[str]          = None
    remote:           bool                   = False
    metadata:         Dict                   = field(default_factory=dict)

# ============================================================
# UTILITIES
# ============================================================

def _clean(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def _strip_html(html: str) -> str:
    """Strip HTML tags and decode common entities."""
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)
    text = re.sub(r"<li[^>]*>", "\n• ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    text = text.replace("&nbsp;", " ").replace("&#39;", "'").replace("&quot;", '"')
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def load_companies_from_db(source: str) -> list:
    """
    Load enabled companies from discovered_companies table.
    Falls back to hardcoded list if table doesn't exist or is empty.
    """
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute(
            """
            SELECT company_name, board_token
            FROM discovered_companies
            WHERE ats_source = %s
              AND enabled = true
            ORDER BY active_roles DESC
            """,
            (source,)
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if rows:
            log.info(f"Loaded {len(rows)} {source} companies from discovered_companies")
            return [(r[0], r[1]) for r in rows]
    except Exception as e:
        log.warning(f"Could not load from discovered_companies ({e}) — using hardcoded list")
    return []


def _md5_id(prefix: str, s: str, n: int = 10) -> str:
    h = hashlib.md5(s.encode("utf-8")).hexdigest()[:n]
    return f"{prefix}{h}"

def _content_hash(job: RawJob) -> str:
    """Stable hash for deduplication — based on source + source_id."""
    raw = f"{job.source}|{job.source_id}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

# ---- Non-US location patterns to reject ----
# Greenhouse/Lever use freetext office names, so we match on known
# international signals. Anything not matching these passes through.
_NON_US_LOCATION_RE = re.compile(
    r"\b(IN-[A-Za-z]|canada|uk|united kingdom|ireland|india|mexico|australia|"
    r"singapore|germany|france|netherlands|spain|brazil|japan|"
    r"poland|czech|sweden|denmark|norway|finland|israel|dubai|uae|"
    r"emea|apac|latam|latin america|europe|asia|africa|"
    r"toronto|vancouver|montreal|london|dublin|bangalore|bengaluru|hyderabad|"
    r"chennai|pune|mumbai|kolkata|mexico city|sydney|melbourne|berlin|amsterdam|"
    r"paris|madrid|stockholm|tel aviv|remote - international)\b",
    re.IGNORECASE,
)

def _is_us_location(location: str, is_remote: bool = False) -> bool:
    """
    Returns True if the location is US-based or safely remote (no country specified).
    Rejects known international office names.
    Remote with no country specified is allowed — assumed US remote.
    """
    if not location:
        # No location at all — allow (enrich will handle it)
        return True
    loc = location.strip()
    # Explicit international signal → reject
    if _NON_US_LOCATION_RE.search(loc):
        return False
    return True


def _is_target_role(title: str) -> bool:
    """
    Returns True if title matches a target role phrase AND does not
    match any blocklist pattern.

    Two-step logic:
      1. Must NOT match any ROLE_TITLE_BLOCKLIST pattern (fast reject)
      2. Must match at least one ROLE_TITLE_PHRASES pattern (whole-word)

    Prevents false positives like:
      "Android Engineer"     -> blocked
      "AV Builds Operations" -> blocked
      "Account Executive"    -> blocked
    """
    if not title:
        return False
    for pat in _BLOCK_PATTERNS:
        if pat.search(title):
            return False
    for pat in _PHRASE_PATTERNS:
        if pat.search(title):
            return True
    return False

def _throttle():
    time.sleep(REQUEST_DELAY_SECONDS)

def _get(url: str, params: dict = None, timeout: int = 12) -> Optional[dict]:
    """Safe GET with error handling."""
    try:
        r = requests.get(url, params=params, timeout=timeout,
                         headers={"User-Agent": "JobAnalyticsPipeline/1.0"})
        if r.status_code == 200:
            return r.json()
        elif r.status_code == 404:
            return None  # company not found on this ATS, skip silently
        else:
            log.warning(f"  HTTP {r.status_code} for {url}")
            return None
    except requests.RequestException as e:
        log.warning(f"  Request failed: {url} — {e}")
        return None

# ============================================================
# SOURCE: GREENHOUSE
# ============================================================


def discover_from_redirect_url(redirect_url: str) -> None:
    """
    Follow an Adzuna redirect_url to find the real ATS destination.
    If it points to Greenhouse or Lever, add to discovered_companies.
    Called during Adzuna ingestion — runs in background, never blocks.
    """
    if not redirect_url:
        return
    try:
        r = requests.get(
            redirect_url, timeout=5, allow_redirects=True,
            headers={"User-Agent": "JobAnalyticsPipeline/1.0"}
        )
        final_url = r.url

        # Greenhouse
        import re
        gh = re.search(
            r'(?:boards\.greenhouse\.io|job-boards\.greenhouse\.io|'
            r'api\.greenhouse\.io/v1/boards)/([a-z0-9_-]+)',
            final_url, re.IGNORECASE
        )
        if gh:
            token = gh.group(1).lower()
            if token not in {"embed","js","css","api","v1","jobs"}:
                _stage_discovered_company("greenhouse", token)
                return

        # Lever
        lv = re.search(r'jobs\.lever\.co/([a-z0-9_-]+)', final_url, re.IGNORECASE)
        if lv:
            slug = lv.group(1).lower()
            _stage_discovered_company("lever", slug)

    except Exception:
        pass  # never block ingestion for discovery failures


def _stage_discovered_company(ats_source: str, token: str) -> None:
    """
    Insert a new company into discovered_companies if not already known.
    Uses active_roles=0 — discover_companies.py refresh will probe it.
    """
    import hashlib
    try:
        conn = get_conn()
        cur = conn.cursor()
        cid = "DC" + hashlib.md5(f"{ats_source}|{token}".encode()).hexdigest()[:10]
        name = token.replace("-", " ").replace("_", " ").title()
        cur.execute(
            """
            INSERT INTO discovered_companies
                (company_id, company_name, ats_source, board_token,
                 discovery_source, active_roles, total_seen, enabled)
            VALUES (%s,%s,%s,%s,'adzuna_redirect',0,0,true)
            ON CONFLICT (ats_source, board_token) DO NOTHING
            """,
            (cid, name, ats_source, token)
        )
        if cur.rowcount > 0:
            log.info(f"  🔍 Discovered new company via Adzuna: [{ats_source}] {name}")
        conn.commit()
        cur.close()
        conn.close()
    except Exception as e:
        pass  # never block ingestion



def _infer_exp_from_title(title: str):
    """Infer experience level from title alone. Used for Adzuna records."""
    if not title:
        return None
    t = title.lower()
    if any(x in t for x in ["intern", "internship", "new grad", "new graduate",
                              "entry level", "entry-level", "junior", " jr "]):
        return "entry"
    if "associate" in t:
        return "associate"
    if " iii" in t or t.endswith(" iii"):
        return "senior"
    if " ii" in t or t.endswith(" ii"):
        return "mid"
    if any(x in t for x in ["senior", " sr ", "sr.", "lead", "principal",
                              "staff", "manager", "director", "head of",
                              "vp ", "vice president"]):
        return "senior"
    return None


def fetch_greenhouse(company_name: str, board_token: str) -> List[RawJob]:
    """
    Pull all jobs from a Greenhouse board.
    ?content=true returns full job descriptions — completely free, no auth.
    Docs: https://developers.greenhouse.io/job-board.html
    """
    url = f"https://api.greenhouse.io/v1/boards/{board_token}/jobs"
    data = _get(url, params={"content": "true"})
    _throttle()

    if not data or "jobs" not in data:
        return []

    jobs = []
    for j in data.get("jobs", []):
        title = _clean(j.get("title", ""))
        if not title or not _is_target_role(title):
            continue

        # Location
        offices = j.get("offices", [])
        location = offices[0].get("name", "") if offices else ""

        # Remote flag
        is_remote = any(
            "remote" in (o.get("name", "") or "").lower()
            for o in offices
        )

        # US filter
        if not _is_us_location(location, is_remote):
            continue

        # Full description from content field
        content = j.get("content", "") or ""
        description = _strip_html(content)

        # Absolute URL
        job_url = j.get("absolute_url", "") or f"https://boards.greenhouse.io/{board_token}/jobs/{j.get('id','')}"

        jobs.append(RawJob(
            source="greenhouse",
            source_id=str(j.get("id", "")),
            title=title,
            company=company_name,
            location=_clean(location),
            description=description,
            job_url=job_url,
            remote=is_remote,
            workplace_type="remote" if is_remote else None,
            posted_date=j.get("updated_at", "")[:10] if j.get("updated_at") else None,
            metadata={"board_token": board_token, "departments": [d.get("name") for d in j.get("departments", [])]},
        ))

    log.info(f"  Greenhouse [{company_name}]: {len(jobs)} target roles found")
    return jobs


def fetch_all_greenhouse() -> List[RawJob]:
    companies = load_companies_from_db("greenhouse")
    if not companies:
        log.info("Falling back to hardcoded GREENHOUSE_COMPANIES list")
        companies = GREENHOUSE_COMPANIES
    all_jobs = []
    for company_name, board_token in companies:
        jobs = fetch_greenhouse(company_name, board_token)
        all_jobs.extend(jobs)
    log.info(f"Greenhouse total: {len(all_jobs)} jobs")
    return all_jobs

# ============================================================
# SOURCE: LEVER
# ============================================================

def fetch_lever(company_name: str, company_slug: str) -> List[RawJob]:
    """
    Pull all published jobs from a Lever board.
    No auth required. Full descriptions returned.
    Docs: https://hire.lever.co/developer/postings
    """
    url = f"https://api.lever.co/v0/postings/{company_slug}"
    data = _get(url, params={"mode": "json"})
    _throttle()

    if not data or not isinstance(data, list):
        return []

    jobs = []
    for j in data:
        title = _clean(j.get("text", ""))
        if not title or not _is_target_role(title):
            continue

        # Location
        categories = j.get("categories", {})
        location = _clean(categories.get("location", "") or "")

        # Workplace type
        commitment = (categories.get("commitment", "") or "").lower()
        workplace_type = None
        if "remote" in commitment or "remote" in location.lower():
            workplace_type = "remote"
        elif "hybrid" in commitment:
            workplace_type = "hybrid"

        # US filter
        if not _is_us_location(location, workplace_type == "remote"):
            continue

        # Full description — Lever returns lists/plain + description blocks
        desc_parts = []
        for block in j.get("descriptionPlain", ""), j.get("description", ""):
            if block:
                desc_parts.append(_strip_html(block) if "<" in block else _clean(block))

        # Also pull lists (requirements, responsibilities etc.)
        for lst in j.get("lists", []):
            lst_text = lst.get("text", "")
            lst_content = _strip_html(lst.get("content", ""))
            if lst_text:
                desc_parts.append(f"\n{lst_text}\n{lst_content}")

        description = "\n\n".join(p for p in desc_parts if p).strip()

        # Salary from Lever structured salaryRange field
        salary_range = j.get("salaryRange") or {}
        lever_salary_min = None
        lever_salary_max = None
        lever_salary_period = None
        if salary_range:
            currency = salary_range.get("currency", "USD")
            interval = salary_range.get("interval", "")
            if currency == "USD" and "year" in interval:
                raw_min = salary_range.get("min")
                raw_max = salary_range.get("max")
                if raw_min and float(raw_min) > 1000:
                    lever_salary_min = float(raw_min)
                if raw_max and float(raw_max) > 1000:
                    lever_salary_max = float(raw_max)
                lever_salary_period = "year"

        jobs.append(RawJob(
            source="lever",
            source_id=j.get("id", ""),
            title=title,
            company=company_name,
            location=location,
            description=description,
            job_url=j.get("hostedUrl", "") or j.get("applyUrl", ""),
            workplace_type=workplace_type,
            employment_type="full-time" if "full" in commitment else None,
            posted_date=datetime.fromtimestamp(
                j["createdAt"] / 1000, tz=timezone.utc
            ).strftime("%Y-%m-%d") if j.get("createdAt") else None,
            salary_min=lever_salary_min,
            salary_max=lever_salary_max,
            salary_period=lever_salary_period,
            metadata={"slug": company_slug, "team": categories.get("team", "")},
        ))

    log.info(f"  Lever [{company_name}]: {len(jobs)} target roles found")
    return jobs


def fetch_ashby(company_name: str, company_slug: str) -> List[RawJob]:
    """
    Pull all published jobs from an Ashby job board.
    No auth required. Full descriptions returned.
    Docs: https://developers.ashbyhq.com/docs/job-postings-api
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}"
    data = _get(url, timeout=20)
    _throttle()

    if not data or not isinstance(data.get("jobs"), list):
        return []

    jobs = []
    for j in data["jobs"]:
        title = _clean(j.get("title", ""))
        if not title or not _is_target_role(title):
            continue

        # Location
        address = (j.get("address") or {}).get("postalAddress", {})
        location = _clean(
            j.get("location", "") or
            address.get("addressLocality", "") or ""
        )
        state = address.get("addressRegion", "")
        country = address.get("addressCountry", "")
        if location and state:
            location = f"{location}, {state}"

        # Workplace type
        workplace_type = None
        wt = (j.get("workplaceType") or "").lower()
        if j.get("isRemote") or "remote" in wt:
            workplace_type = "remote"
        elif "hybrid" in wt:
            workplace_type = "hybrid"
        elif "onsite" in wt or "on_site" in wt:
            workplace_type = "onsite"

        # US filter
        if country and country not in ("United States", "US", ""):
            if not workplace_type == "remote":
                continue
        if not _is_us_location(location, workplace_type == "remote"):
            if country not in ("United States", "US", "") and not j.get("isRemote"):
                continue

        # Description
        desc = j.get("descriptionPlain", "") or _strip_html(j.get("descriptionHtml", ""))
        desc = _clean(desc) if desc else ""

        # Posted date
        posted = None
        if j.get("publishedAt"):
            try:
                posted = j["publishedAt"][:10]
            except Exception:
                pass

        jobs.append(RawJob(
            source="ashby",
            source_id=j.get("id", ""),
            title=title,
            company=company_name,
            location=location,
            description=desc,
            job_url=j.get("jobUrl", "") or j.get("applyUrl", ""),
            workplace_type=workplace_type,
            employment_type="full-time" if j.get("employmentType") == "FullTime" else None,
            posted_date=posted,
            metadata={"slug": company_slug, "department": j.get("department", "")},
        ))

    log.info(f"  Ashby [{company_name}]: {len(jobs)} target roles found")
    return jobs


def fetch_all_ashby() -> List[RawJob]:
    companies = load_companies_from_db("ashby")
    if not companies:
        return []
    jobs = []
    for company_name, slug in companies:
        try:
            fetched = fetch_ashby(company_name, slug)
            jobs.extend(fetched)
        except Exception as e:
            log.warning(f"  Ashby [{company_name}] error: {e}")
    log.info(f"Ashby total: {len(jobs)} jobs")
    return jobs


def fetch_all_lever() -> List[RawJob]:
    companies = load_companies_from_db("lever")
    if not companies:
        log.info("Falling back to hardcoded LEVER_COMPANIES list")
        companies = list({slug: name for name, slug in LEVER_COMPANIES}.items())
        companies = [(v, k) for k, v in companies.items()]
    all_jobs = []
    seen_slugs = set()
    for company_name, slug in companies:
        if slug in seen_slugs:
            continue
        seen_slugs.add(slug)
        jobs = fetch_lever(company_name, slug)
        all_jobs.extend(jobs)
    log.info(f"Lever total: {len(all_jobs)} jobs")
    return all_jobs

# ============================================================
# SOURCE: ADZUNA
# ============================================================

def fetch_adzuna(search_term: str, page: int = 1, discover_mode: bool = False) -> List[RawJob]:
    """
    Pull jobs from Adzuna API.
    Returns partial descriptions — used as discovery layer for salary/location signals.
    Requires ADZUNA_APP_ID and ADZUNA_APP_KEY in .env
    """
    app_id  = os.getenv("ADZUNA_APP_ID", "")
    app_key = os.getenv("ADZUNA_APP_KEY", "")

    if not app_id or not app_key:
        log.warning("Adzuna credentials not set (ADZUNA_APP_ID / ADZUNA_APP_KEY). Skipping.")
        return []

    url = f"https://api.adzuna.com/v1/api/jobs/{ADZUNA_COUNTRY}/search/{page}"
    params = {
        "app_id":         app_id,
        "app_key":        app_key,
        "what":           search_term,
        "results_per_page": 50,
        "content-type":   "application/json",
    }

    data = _get(url, params=params)
    _throttle()

    if not data or "results" not in data:
        return []

    jobs = []
    for j in data.get("results", []):
        title = _clean(j.get("title", ""))
        if not title or not _is_target_role(title):
            continue

        company = _clean((j.get("company") or {}).get("display_name", "") or "")
        location_data = j.get("location", {})
        location = _clean(location_data.get("display_name", "") or "")

        description = _clean(j.get("description", "") or "")

        # US filter (Adzuna already scopes to US country but location
        # display_name can still show international results)
        if not _is_us_location(location):
            continue

        # Discovery — follow redirect_url to find ATS source
        # Only runs during nightly cron (--discover flag), not manual runs
        if discover_mode:
            redirect_url = j.get("redirect_url", "")
            if redirect_url:
                discover_from_redirect_url(redirect_url)

        # Salary
        salary_min    = j.get("salary_min")
        salary_max    = j.get("salary_max")
        salary_period = None
        if salary_min or salary_max:
            # Adzuna returns annual by default for US
            salary_period = "year"

        jobs.append(RawJob(
            source="adzuna",
            source_id=str(j.get("id", "")),
            title=title,
            company=company,
            location=location,
            description=description,
            job_url=j.get("redirect_url", ""),
            salary_min=float(salary_min) if salary_min else None,
            salary_max=float(salary_max) if salary_max else None,
            salary_period=salary_period,
            posted_date=j.get("created", "")[:10] if j.get("created") else None,
            metadata={"search_term": search_term, "page": page, "description_quality": "partial"},
        ))

    return jobs


def fetch_all_adzuna(discover_mode: bool = False) -> List[RawJob]:
    all_jobs = []
    for role in TARGET_ROLES:
        log.info(f"  Adzuna searching: '{role}'")
        for page in range(1, ADZUNA_MAX_PAGES + 1):
            jobs = fetch_adzuna(role, page=page, discover_mode=discover_mode)
            if not jobs:
                break
            all_jobs.extend(jobs)
            log.info(f"    page {page}: {len(jobs)} results")

    log.info(f"Adzuna total: {len(all_jobs)} jobs (before dedup)")
    return all_jobs

# ============================================================
# DB: CONNECTION + EXISTENCE CHECKS
# ============================================================

def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def load_existing_hashes(cur) -> set:
    """
    Load all existing (source, source_id) combos to avoid re-inserting.
    We store source_id in a metadata column or derive it from job_id.
    We use a dedicated ingestion_source + source_id pair stored in job_postings.
    """
    cur.execute("""
        SELECT ingestion_source, source_id
        FROM job_postings
        WHERE ingestion_source IS NOT NULL AND source_id IS NOT NULL
    """)
    return {(r["ingestion_source"], r["source_id"]) for r in cur.fetchall()}

def ensure_schema_columns(cur):
    """
    Add ingestion_source and source_id columns to job_postings if they don't exist.
    Safe to run on every startup.
    """
    cur.execute("""
        ALTER TABLE job_postings
          ADD COLUMN IF NOT EXISTS ingestion_source text,
          ADD COLUMN IF NOT EXISTS source_id        text,
          ADD COLUMN IF NOT EXISTS description_quality text DEFAULT 'full',
          ADD COLUMN IF NOT EXISTS job_url          text
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_jp_source
        ON job_postings(ingestion_source, source_id)
        WHERE ingestion_source IS NOT NULL
    """)

# ============================================================
# DB: INGESTION
# ============================================================

def ingest_job(cur, job: RawJob) -> bool:
    """
    Insert a single RawJob into job_postings.
    Returns True if inserted, False if skipped (duplicate).
    Schema-matched to actual job_postings table definition.
    """
    job_id = _md5_id("J", f"{job.source}|{job.source_id}")

    # description quality tag
    desc_quality = "partial" if job.source == "adzuna" else "full"

    # desc_hash for deduplication (matches existing idx_job_postings_desc_hash)
    desc_hash = hashlib.md5((job.description or "").encode("utf-8")).hexdigest()

    # Always strip HTML at insert time — defensive guarantee regardless of source
    clean_description = _strip_html(job.description or "")
    desc_hash = hashlib.md5(clean_description.encode("utf-8")).hexdigest()

    # data_tier: 1=full signal (GH/Lever/manual), 2=market coverage (Adzuna)
    data_tier = 2 if job.source == "adzuna" else 1
    adzuna_exp_level = _infer_exp_from_title(job.title) if job.source == "adzuna" else None

    cur.execute(
        """
        INSERT INTO job_postings (
            job_id,
            source,
            description_text,
            desc_hash,
            date_found,
            ingested_at,
            posted_date,
            salary_min,
            salary_max,
            salary_period,
            workplace_type,
            employment_type,
            job_url,
            status,
            ingestion_source,
            source_id,
            description_quality,
            data_tier,
            experience_level
        ) VALUES (
            %s, %s, %s, %s, now(), now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON CONFLICT (job_id) DO NOTHING
        """,
        (
            job_id,
            job.source,
            clean_description,
            desc_hash,
            job.posted_date,
            job.salary_min,
            job.salary_max,
            job.salary_period,
            job.workplace_type,
            job.employment_type,
            job.job_url,
            "raw",
            job.source,
            job.source_id,
            desc_quality,
            data_tier,
            adzuna_exp_level,
        )
    )

    inserted = cur.rowcount > 0

    # If inserted and we have company/title, pre-populate those fields
    # (enrich_job_postings.py will fill the rest via NLP)
    if inserted and job.company:
        company_id = _md5_id("C", job.company)
        cur.execute(
            """
            INSERT INTO companies (company_id, company_name)
            VALUES (%s, %s)
            ON CONFLICT (company_id) DO NOTHING
            """,
            (company_id, job.company)
        )
        cur.execute(
            "UPDATE job_postings SET company_id = %s WHERE job_id = %s AND company_id IS NULL",
            (company_id, job_id)
        )

    if inserted and job.title:
        role_id = _md5_id("R", job.title)
        cur.execute(
            """
            INSERT INTO roles (role_id, role_name)
            VALUES (%s, %s)
            ON CONFLICT (role_id) DO NOTHING
            """,
            (role_id, job.title)
        )
        cur.execute(
            "UPDATE job_postings SET role_id = %s WHERE job_id = %s AND role_id IS NULL",
            (role_id, job_id)
        )

    if inserted and job.location:
        # Extract state from "City, ST" pattern
        state = None
        m = re.search(r",\s*([A-Z]{2})$", job.location.strip())
        if m:
            state = m.group(1)
        location_id = _md5_id("L", f"{job.location}|{state or ''}")
        cur.execute(
            """
            INSERT INTO locations (location_id, location, state)
            VALUES (%s, %s, %s)
            ON CONFLICT (location_id) DO NOTHING
            """,
            (location_id, job.location, state)
        )
        cur.execute(
            "UPDATE job_postings SET location_id = %s WHERE job_id = %s AND location_id IS NULL",
            (location_id, job_id)
        )

    return inserted

# ============================================================
# PIPELINE RUN LOGGING
# ============================================================

def log_pipeline_run(cur, run_id: str, source: str, inserted: int,
                     skipped: int, errors: int, status: str = "success"):
    """
    Log results to pipeline_runs table.
    Introspects actual column names first so it works regardless of your schema version.
    """
    try:
        # Discover what columns actually exist
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'pipeline_runs'
        """)
        existing_cols = {r["column_name"] for r in cur.fetchall()}

        if not existing_cols:
            log.warning("pipeline_runs table not found — skipping run log.")
            return

        cols, vals = ["run_id"], [run_id]

        def _add(col, val):
            if col in existing_cols:
                cols.append(col)
                vals.append(val)

        _add("pipeline_name",      f"ingest_{source}")
        _add("script_name",        f"ingest_{source}")
        _add("source",             source)
        _add("status",             status)
        _add("records_processed",  inserted + skipped + errors)
        _add("records_inserted",   inserted)
        _add("records_skipped",    skipped)
        _add("records_failed",     errors)
        _add("jobs_inserted",      inserted)
        _add("jobs_skipped",       skipped)
        _add("jobs_errored",       errors)
        _add("started_at",         "now()")
        _add("finished_at",        "now()")
        _add("created_at",         "now()")
        _add("updated_at",         "now()")

        # now() values need to be literals not params
        placeholders = []
        final_vals = []
        for col, val in zip(cols, vals):
            if val == "now()":
                placeholders.append("now()")
            else:
                placeholders.append("%s")
                final_vals.append(val)

        sql = f"""
            INSERT INTO pipeline_runs ({', '.join(cols)})
            VALUES ({', '.join(placeholders)})
            ON CONFLICT (run_id) DO UPDATE SET
                status = EXCLUDED.status,
                updated_at = now()
        """
        cur.execute(sql, final_vals)
        log.info(f"Pipeline run logged: {run_id}")

    except Exception as e:
        log.warning(f"Could not write to pipeline_runs: {e}")
        try:
            cur.connection.rollback()
        except Exception:
            pass

# ============================================================
# MAIN PIPELINE
# ============================================================

def run_ingestion(source: str, apply: bool, discover_mode: bool = False) -> None:
    run_id = f"ingest_{source}_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    log.info(f"Starting ingestion run: {run_id} | apply={apply}")

    # Fetch from sources
    all_jobs: List[RawJob] = []

    if source in ("greenhouse", "all"):
        log.info("Fetching from Greenhouse...")
        all_jobs.extend(fetch_all_greenhouse())

    if source in ("lever", "all"):
        log.info("Fetching from Lever...")
        all_jobs.extend(fetch_all_lever())

    if source in ("ashby", "all"):
        log.info("Fetching from Ashby...")
        all_jobs.extend(fetch_all_ashby())

    if source in ("adzuna", "all"):
        log.info("Fetching from Adzuna...")
        all_jobs.extend(fetch_all_adzuna(discover_mode=discover_mode))

    log.info(f"Total fetched across all sources: {len(all_jobs)}")

    if not all_jobs:
        log.info("Nothing to ingest.")
        return

    # Deduplicate within this batch by source+source_id
    seen = set()
    deduped = []
    for job in all_jobs:
        key = (job.source, job.source_id)
        if key not in seen and job.source_id:
            seen.add(key)
            deduped.append(job)

    # Cross-source dedup — same company + title + location should not be inserted twice
    # regardless of which ATS source it came from.
    # Location logic:
    #   - Remote roles: location normalized to "remote" (source doesn't matter)
    #   - Onsite/hybrid: use first city segment for city-level dedup
    #   - Unknown location: use "unknown"
    def _norm_location(job) -> str:
        if job.workplace_type == "remote":
            return "remote"
        loc = (job.location or "").strip().lower()
        if not loc or loc in ("united states", "us", "usa", ""):
            return "unknown"
        return loc.split(",")[0].strip()

    # Load existing jobs from DB for cross-source dedup
    # Build a set of (company, title, loc) for all Tier 1 jobs already ingested
    try:
        _conn = get_conn()
        _cur = _conn.cursor()
        _cur.execute("""
            SELECT lower(c.company_name), lower(r.role_name),
                   CASE
                       WHEN jp.workplace_type = 'remote' THEN 'remote'
                       WHEN l.location IS NULL OR l.location = '' THEN 'unknown'
                       ELSE lower(split_part(l.location, ',', 1))
                   END
            FROM job_postings jp
            JOIN companies c ON c.company_id = jp.company_id
            JOIN roles r ON r.role_id = jp.role_id
            LEFT JOIN locations l ON l.location_id = jp.location_id
            WHERE jp.data_tier = 1
        """)
        db_cross_keys = set(_cur.fetchall())
        _cur.close()
        _conn.close()
        log.info(f"Loaded {len(db_cross_keys)} existing Tier 1 job keys for cross-source dedup")
    except Exception as e:
        log.warning(f"Could not load existing jobs for cross-source dedup: {e}")
        db_cross_keys = set()

    cross_seen = set(db_cross_keys)
    deduped_final = []
    for job in deduped:
        loc_key = _norm_location(job)
        cross_key = (
            job.company.lower().strip(),
            job.title.lower().strip(),
            loc_key
        )
        if cross_key in cross_seen:
            continue
        cross_seen.add(cross_key)
        deduped_final.append(job)

    removed = len(deduped) - len(deduped_final)
    if removed > 0:
        log.info(f"Cross-source title+location dedup removed {removed} duplicate postings")
    deduped = deduped_final

    log.info(f"After batch dedup: {len(deduped)} jobs")

    if not apply:
        # Dry run — just show what would be inserted
        log.info("DRY RUN — sample of what would be ingested:")
        for job in deduped[:10]:
            desc_len = len(job.description or "")
            log.info(f"  [{job.source}] {job.company} — {job.title} | {job.location} | desc_len={desc_len}")
        log.info(f"Total would-be inserts: {len(deduped)} (run with --apply to write)")
        return

    # DB writes
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)

    try:
        ensure_schema_columns(cur)
        conn.commit()
    except Exception as e:
        log.error(f"Schema migration failed: {e}")
        conn.rollback()
        conn.close()
        return

    inserted = 0
    skipped  = 0
    errors   = 0

    for job in deduped:
        try:
            was_inserted = ingest_job(cur, job)
            if was_inserted:
                inserted += 1
                if inserted <= 3:
                    # Show first few inserts so we can confirm it's working
                    log.info(f"  ✅ Inserted: [{job.source}] {job.company} — {job.title}")
            else:
                skipped += 1
        except Exception as e:
            log.error(f"  ❌ Failed [{job.source}] {job.company} — {job.title}: {e}")
            log.error(f"     job_id={_md5_id('J', f'{job.source}|{job.source_id}')}")
            conn.rollback()
            errors += 1
            continue

    # ---- COMMIT job inserts FIRST before anything else ----
    conn.commit()
    log.info(f"Ingestion complete — inserted: {inserted} | skipped: {skipped} | errors: {errors}")

    # ---- Annualize Lever salary fields (salary_min/max -> salary_min/max_annual) ----
    try:
        cur.execute("""
            UPDATE job_postings
            SET salary_min_annual = salary_min,
                salary_max_annual = salary_max
            WHERE ingestion_source = 'lever'
            AND salary_min IS NOT NULL
            AND salary_max IS NOT NULL
            AND salary_period = 'year'
            AND salary_min_annual IS NULL
        """)
        annualized = cur.rowcount
        if annualized > 0:
            log.info(f"Annualized salary for {annualized} Lever records")
        conn.commit()
    except Exception as e:
        log.warning(f"Lever salary annualization failed: {e}")
        conn.rollback()

    # ---- Pipeline logging in a SEPARATE transaction so it can never roll back job data ----
    try:
        log_pipeline_run(cur, run_id, source, inserted, skipped, errors)
        conn.commit()
    except Exception as e:
        log.warning(f"Pipeline run logging failed (data already committed safely): {e}")
        conn.rollback()

    cur.close()
    conn.close()

    log.info("Done. Run enrich_job_postings.py --apply --only-missing to enrich new records.")

# ============================================================
# ENTRY POINT
# ============================================================

def main():
    ap = argparse.ArgumentParser(description="Multi-source job ingestion pipeline.")
    ap.add_argument(
        "--source",
        choices=["greenhouse", "lever", "adzuna", "ashby", "all"],
        default="all",
        help="Which source to pull from (default: all)"
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write to DB. Without this flag, runs as dry-run."
    )
    ap.add_argument("--discover", action="store_true", help="Follow Adzuna redirects to discover new companies (slow)")
    args = ap.parse_args()

    run_ingestion(source=args.source, apply=args.apply, discover_mode=args.discover)


if __name__ == "__main__":
    main()
