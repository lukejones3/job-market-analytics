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
    python python/ingest_jobs.py --dry-run        (default, no DB writes)

Nightly cron (add to crontab with: crontab -e):
    0 2 * * * cd /path/to/your/repo && python python/ingest_jobs.py --apply >> logs/ingest.log 2>&1

Drop this file in: python/ingest_jobs.py
"""

import asyncio
import hashlib
import itertools
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

import aiohttp
import requests
from urllib.parse import urlparse
from dotenv import load_dotenv
import psycopg2
# LOC_PATCH_v1
from location_normalizer import normalize_location

# New ATS harvesters — imported here so `--source all` includes them
try:
    from workable_harvest import fetch_all_workable as _fetch_all_workable
except ImportError:
    _fetch_all_workable = None

try:
    from icims_harvest import fetch_all_icims as _fetch_all_icims
except ImportError:
    _fetch_all_icims = None

try:
    from taleo_harvest import fetch_all_taleo as _fetch_all_taleo
except ImportError:
    _fetch_all_taleo = None

from psycopg2.extras import DictCursor

try:
    from classify_domain import build_alias_map as _build_alias_map, classify_domain as _classify_domain
    _DOMAIN_CLASSIFIER_AVAILABLE = True
except ImportError:
    _DOMAIN_CLASSIFIER_AVAILABLE = False

from company_blocklist import is_company_blocked

_ALIAS_MAP: Optional[Dict] = None

def _get_alias_map(connection):
    global _ALIAS_MAP
    if _ALIAS_MAP is None and _DOMAIN_CLASSIFIER_AVAILABLE:
        _ALIAS_MAP = _build_alias_map(connection)
    return _ALIAS_MAP

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
    # Enterprise/financial sector titles
    r"data management analyst",
    r"data management consultant",
    r"data management manager",
    r"data management director",
    r"data product management",
    r"advanced analytics",
    r"analytics consultant",
    r"analytics director",
    r"data product manager",
    r"data product analyst",
    r"decision intelligence",
    r"quantitative analytics",
    r"quantitative research analyst",
    # Enterprise naming variants — Cigna/Vanguard/Capital One/Adobe
    r"data science advisors?",
    r"data modeling advisor",
    r"data engineering.*advisor",
    r"data measurement.*advisor",
    r"data stewardship",
    r"data steward",
    r"dataops engineer",
    r"data ops engineer",
    r"data science engineer",
    r"analytics.*modeling",
    r"manager.*data science",
    r"manager.*data analysis",
    r"manager.*analytics",
    r"director.*data science",
    r"director.*analytics",
    r"director.*data engineering",
    r"vp.*data",
    r"head of.*data",
    r"head of.*analytics",
    r"lead.*data analytics",
    r"ai data scientist",
    r"ai data engineer",
    r"data science advisors?",
    r"data engineering.*analyst",
    r"manager.*data management",
    # Greenhouse gaps — non-standard titles
    r"business intelligence.*lead",
    r"business intelligence.*manager",
    r"program manager.*ml data",
    r"program manager.*machine learning",
    r"engineering manager.*data engineering",
    r"engineering manager.*machine learning",
    r"engineering manager.*ml",
    r"backend engineer.*data",
    r"software engineer.*data$",
    r"data governance lead",
    r"data science.*internship",
    r"associate.*commercial analytics",
    r"manager.*compliance.*data",
    r"machine learning platform engineer",
    r"program manager.*ml",
    r"program manager.*machine learning data",
    r"data science.*decisions",
    r"manager.*compliance.*data",
    r"senior product manager.*data",
    # Eightfold gaps — Morgan Stanley / Twilio / Ford
    r"analytics solutions.*vice president",
    r"lead analytics",
    r"data modell?er",
    r"senior manager.*machine learning",
    r"manager.*machine learning",
    r"web analytics.*manager",
    r"marketing analytics.*manager",
    r"analytics.*manager",
    r"data.*information architect",
    r"data platform supervisor",
    r"director.*data.*applied sciences",
    r"data infrastructure.*governance analyst",
    r"researcher.*data systems",
    r"member of technical staff.*ai data",
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
    # r"\bplatform engineer\b",  # too broad — ML platform engineers are valid
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
    # r"\bprogram manager\b",  # too broad — ML/data program managers are valid
    # r"\bproduct manager\b",  # too broad — data/AI PMs are valid
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
    r"\bcompliance officer\b",
    r"\bcompliance analyst\b",
    r"\baml compliance\b",
    r"\baccounting\b",
    r"\bcontroller\b",
    r"\bcfo\b",
    r"\bcto\b",
    r"\bceo\b",
    # Non-analytics "data" roles (content ops, MDM, data entry, etc.)
    r"\blocation data\b",
    r"\bmaster data\b",
    r"\bdata specialist\b",
    r"\bdata entry\b",
    r"\bdata steward\b",
    r"\bdata management analyst\b",
    r"\bdata governance analyst\b",
    r"\bclinical data\b",
    r"\bresearch data specialist\b",
    r"\bdata quality specialist\b",
    r"\bdata operations specialist\b",
    r"\bdata coordinator\b",
    r"\bdata processor\b",
    r"\bdata administrator\b",
    r"\bcontent data\b",
    r"\bgis \b",
    r"\bgeospatial analyst\b",
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

# ── Knowledge-worker title filter (whitelist approach) ─────────────────────────
# A title must match at least one INCLUDE pattern AND no EXCLUDE pattern.
# INCLUDE: any of the 8 target verticals
# EXCLUDE: hard-block patterns that override even if inclusion matched

_KW_INCLUDE_RE = re.compile("|".join([
    # DATA / ML
    r"\bdata\s+scienc",          # data science, data scientist
    r"\bdata\s+engineer",
    r"\bdata\s+analyst",
    r"\bdata\s+architect",
    r"\bdata\s+steward",
    r"\bdataops\b|\bdata\s+ops\b",
    r"\bdata\s+platform\b",
    r"\bml\s+engineer",
    r"\bml\s+researcher",
    r"\bmachine\s+learning\b",
    r"\bapplied\s+scientist",
    r"\bresearch\s+scientist",
    r"\bai\s+engineer",
    r"\bai\s+researcher",
    r"\bai/ml\b",
    r"\banalytics\s+engineer",
    r"\bbi\s+(?:analyst|engineer|developer)\b",
    r"\bbusiness\s+intelligence\b",
    r"\bmlops\b|\bml\s+ops\b",
    r"\bdecision\s+scientist",
    r"\bquantitative\s+analyst\b|\bquant\s+analyst\b|\bquant\b",
    r"\bactuar",                  # actuary, actuarial
    r"\bmarketing\s+analyst\b",
    r"\bproduct\s+analyst\b",
    r"\bfinancial\s+analyst\b",
    # ENGINEERING
    r"\bsoftware\s+engin",        # software engineer / engineering
    r"\bsoftware\s+develop",      # software developer / development
    r"\bsde\b|\bswe\b",
    r"\bbackend\b|\bback[\s-]end\b",
    r"\bfrontend\b|\bfront[\s-]end\b",
    r"\bfull[\s-]?stack\b",
    r"\bdeveloper\b",
    r"\bdevops\b",
    r"\bsite\s+reliability\b",
    r"\bsre\b",
    r"\bplatform\s+engineer",
    r"\binfrastructure\s+engineer",
    r"\bcloud\s+engineer",
    r"\bsystems\s+engineer",
    r"\bios\b|\bandroid\b",
    r"\bmobile\s+(?:engineer|developer)",
    r"\bsecurity\s+engineer",
    r"\bappsec\b|\bapplication\s+security\b",
    r"\bqa\s+engineer\b|\bsdet\b|\btest\s+engineer\b|\bautomation\s+engineer\b",
    r"\bembedded\s+(?:engineer|software)\b|\bfirmware\s+(?:engineer|developer)\b",
    r"\bprincipal\s+engineer\b|\bstaff\s+engineer\b|\bdistinguished\s+engineer\b",
    r"\bengineering\s+manager\b|\bdirector\s+of\s+engineering\b",
    r"\brobotic",
    r"\bcontrols\s+engineer\b",
    # SALES
    r"\baccount\s+executive\b",
    r"\bsdr\b|\bbdr\b",
    r"\bsales\s+development\s+rep",
    r"\bbusiness\s+development\s+rep",
    r"\bcustomer\s+success\b",
    r"\bcsm\b",
    r"\bsales\s+engineer\b",
    r"\bsolutions\s+engineer\b",
    r"\bsolutions\s+architect\b",
    r"\baccount\s+manager\b",
    r"\brenewals\s+manager\b",
    r"\brevenue\s+ops\b|\brevops\b|\bsales\s+ops\b",
    r"\bvp\s+(?:of\s+)?sales\b|\bhead\s+of\s+sales\b|\bchief\s+revenue\b|\bcro\b",
    r"\bsales\s+director\b|\bregional\s+sales\s+manager\b",
    # FINANCE
    r"\bfp&a\b|\bfinancial\s+planning\b",
    r"\bfinancial\s+(?:analyst|reporting|controller|manager)\b",
    r"\baccountant\b|\baccounting\b|\bbookkeeper\b|\bcontroller\b",
    r"\btreasury\b|\btreasurer\b",
    r"\bauditor\b|\baudit\b",
    r"\btax\s+(?:analyst|manager|associate|director|counsel)\b",
    r"\binvestment\b",
    r"\bequity\s+research\b|\bcredit\s+analyst\b",
    r"\bunderwriter\b",
    r"\bcfo\b|\bvp\s+(?:of\s+)?finance\b|\bfinance\s+director\b",
    # MARKETING
    r"\bmarketing\s+(?:manager|engineer|ops|director|coordinator|specialist|lead)\b",
    r"\bgrowth\s+(?:manager|lead|hacker|marketing|analyst|engineer|ops)\b|\bhead\s+of\s+growth\b",
    r"\blifecycle\b",
    r"\bperformance\s+marketing\b",
    r"\bdemand\s+gen(?:eration)?\b",
    r"\bcontent\s+(?:marketing|strategist|manager|creator|writer|producer|director)\b",
    r"\bcopywriter\b",
    r"\bcreative\s+director\b|\bart\s+director\b",
    r"\bpublic\s+relations\b",
    r"\bcommunications?\s+(?:manager|director|specialist|strategist)\b",
    r"\bsocial\s+media\s+(?:manager|strategist|analyst)\b",
    r"\bseo\b|\bsem\b",
    r"\bpaid\s+(?:acquisition|media|search|social)\b",
    r"\bproduct\s+marketing\b|\bpmm\b",
    r"\bmarketing\s+ops\b|\bmarops\b",
    r"\bvp\s+(?:of\s+)?marketing\b|\bcmo\b|\bhead\s+of\s+marketing\b",
    r"\bbrand\b",
    # PRODUCT
    r"\bproduct\s+manager\b|\bproduct\s+owner\b",
    r"\btpm\b|\btechnical\s+product\s+manager\b",
    r"\bproduct\s+ops\b",
    r"\bprincipal\s+pm\b|\bgroup\s+pm\b|\bhead\s+of\s+product\b",
    r"\bvp\s+(?:of\s+)?product\b|\bcpo\b",
    # DESIGN
    r"\bdesigner\b",
    r"\bux\b",
    r"\bui/ux\b|\bux/ui\b",
    r"\buser\s+(?:experience|interface)\b",
    r"\bdesign\b",
    # OPS
    r"\bbusiness\s+operations\b|\bbizops\b|\bbiz\s+ops\b",
    r"\bstrategic\s+(?:operations|ops)\b",
    r"\bpeople\s+ops\b|\bhr\s+ops\b|\bpeople\s+operations\b",
    r"\btalent\s+acquisition\b",
    r"\brecruiter\b|\brecruiting\b",
    r"\bchief\s+of\s+staff\b",
    r"\bprogram\s+manager\b",
    r"\boperations\s+analyst\b",
    r"\bexecutive\s+assistant\b",
    # BROAD CROSS-FUNCTIONAL (title-level knowledge-worker signals)
    r"\banalyst\b",
    r"\barchitect\b",
    r"\bscientist\b",
    r"\bresearcher\b",
    r"\bdirector\b",
    r"\bvp\b",
    r"\bhead\s+of\b",
    r"\bconsultant\b",
    r"\bstrateg",                 # strategy, strategist, strategic
    r"\bcompliance\b",
    r"\blegal\b",
    r"\bcounsel\b",
    # ADDITIONAL (gap-fill from dry-run review)
    r"\bai\b",                    # AI Engineer, AI Ops, AI Deployment (word boundary prevents inside-word matches)
    r"\bllm\b",                   # LLM Engineer, LLM Researcher
    r"\bdba\b|\bdatabase\s+admin",
    r"\bfounding\b",              # Founding Engineer, Founding Designer, Founding PM
    r"\bsales\s+manager\b",
    r"\bsales\s+(?:representative|rep)\b",
    r"\bproject\s+manager\b",
    r"\bgtm\b",                   # go-to-market
    r"\btools\s+engineer\b|\btooling\s+engineer\b",
    r"\bcompiler\s+engineer\b",
    r"\bagile\b",                 # Agile Coach, Agile practitioner
    r"\bscrum\b",                 # Scrum Master
    r"\bsales\s+operat",          # Sales Operations (full word)
    r"\bsecurity\s+manager\b",
    r"\bproduct\s+lead\b",
    r"\bresearch\b",              # Research Intern, Research Associate, Research Analyst
]), re.IGNORECASE)

# Hard-block overrides: always drop even if an inclusion matched
_KW_EXCLUDE_RE = re.compile("|".join([
    r"\bdriver\b",
    r"\bcourier\b",
    r"\bpizza\b",
    r"\bbarista\b",
    r"\bcashier\b",
    r"\bstocker\b",
    r"\bhostess\b",
    r"\bline\s+cook\b|\bprep\s+cook\b|\bdishwasher\b",
    r"\bfood\s+service\b",
    r"\bwarehouse\s+(?:associate|worker|team\s+member|operator)\b",
    r"\bretail\s+associate\b|\bstore\s+associate\b|\bsales\s+associate\b",
    r"\bshift\s+(?:leader|manager)\b",
    r"\bphysical\s+therapist\b|\boccupational\s+therapist\b|\bspeech\s+therapist\b",
    r"\blicensed\s+therapist\b|\bmental\s+health\s+therapist\b",
    r"\bregistered\s+nurse\b|\bnurse\s+practitioner\b|\bnurse\s+anesthetist\b",
    r"\brn\b|\blpn\b|\bcna\b|\blvn\b",
    r"\bmedical\s+assistant\b|\bpatient\s+services\b",
    r"\bpharmacy\s+technician\b",
    r"\bautomotive\s+painter\b|\bauto\s+body\b|\bwheel\s+repair\b",
    r"\bdiesel\s+technician\b|\bmechanic\b",
    r"\bfacilities\s+technician\b|\bmaintenance\s+technician\b|\bhvac\b",
    r"\belectrician\b|\bplumber\b|\bwelder\b|\bcarpenter\b",
    r"\btest\s+driver\b|\bvehicle\s+operator\b",
    r"\bdata\s+collection\s+driver\b|\bmapping\s+data\s+collection\b",
    r"\bfield\s+(?:technician|service\s+technician)\b",
    r"\bconstruction\b|\blaborer\b",
    r"\bclinical\s+research\s+coordinator\b",
]), re.IGNORECASE)


def _is_knowledge_worker_title(title: str) -> bool:
    """True if title belongs to one of the 8 target knowledge-worker verticals."""
    if not title:
        return False
    if not _KW_INCLUDE_RE.search(title):
        return False
    if _KW_EXCLUDE_RE.search(title):
        return False
    return True


def _is_target_role(title: str) -> bool:
    return _is_knowledge_worker_title(title)

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

        # LOC_PATCH_v1: drop foreign via normalize_location
        if normalize_location(location, "remote" if is_remote else None).should_drop:
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

        # LOC_PATCH_v1: drop foreign via normalize_location
        if normalize_location(location, workplace_type).should_drop:
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
        # LOC_PATCH_v1: drop foreign via normalize_location, keep US-country override
        if normalize_location(location, workplace_type).should_drop:
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
    Returns True if inserted, False if skipped (duplicate or non-KW title).
    Schema-matched to actual job_postings table definition.
    """
    if is_company_blocked(job.company):
        return False
    if not _is_knowledge_worker_title(job.title):
        return False

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

    # LOC_PATCH_v1: normalize location once at insert (single source of truth)
    _loc = normalize_location(job.location, job.workplace_type)

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
            experience_level,
            last_seen_at,
            loc_city,
            loc_state,
            loc_country
        ) VALUES (
            %s, %s, %s, %s, now(), now(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now(), %s, %s, %s
        )
        ON CONFLICT (job_id) DO UPDATE SET last_seen_at = now()
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
            _loc.city,
            _loc.state,
            _loc.country,
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

    if inserted and _DOMAIN_CLASSIFIER_AVAILABLE:
        alias_map = _get_alias_map(cur.connection)
        if alias_map:
            domain, secondary, _ = _classify_domain(
                job.title or "", job.description or "", alias_map
            )
            cur.execute(
                """
                UPDATE job_postings
                SET domain               = %s,
                    domain_secondary     = %s,
                    domain_classified_at = now()
                WHERE job_id = %s AND domain IS NULL
                """,
                (domain, secondary if secondary else None, job_id),
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


# ============================================================
# SOURCE: WORKDAY
# ============================================================

WORKDAY_COMPANIES = [
    # (display_name, tenant, board, wd_server)
    ("Applied Materials", "amat",        "External",              "wd1"),
    ("PayPal",            "paypal",       "jobs",                  "wd1"),
    ("Adobe",             "adobe",        "external_experienced",  "wd5"),
    ("eBay",              "ebay",         "apply",                 "wd5"),
    ("Salesforce",        "salesforce",   "External_Career_Site",  "wd12"),
    ("Workday",           "workday",      "Workday",               "wd5"),
    ("CrowdStrike",       "crowdstrike",  "crowdstrikecareers",    "wd5"),
    ("Dell",              "dell",         "External",              "wd1"),
    ("CVS Health",        "cvshealth",    "cvs_health_careers",    "wd1"),
    ("Netflix",           "netflix",      "Netflix",               "wd1"),
    ("Nike",              "nike",         "nke",                   "wd1"),
    ("Cisco",             "cisco",        "Cisco_Careers",         "wd5"),
    ("Etsy",              "etsy",         "Etsy_Careers",          "wd5"),
    ("Zoom",              "zoom",         "Zoom",                  "wd5"),
    # Batch 2 — added April 11 2026
    ("NVIDIA",             "nvidia",        "nvidiaexternalcareersite", "wd5"),
    ("Intel",              "intel",         "External",                "wd1"),
    ("Booz Allen",         "bah",           "BAH_Jobs",                "wd1"),
    ("Leidos",             "leidos",        "External",                "wd5"),
    ("Nasdaq",             "nasdaq",        "Global_External_Site",    "wd1"),
    ("Pfizer",             "pfizer",        "PfizerCareers",           "wd1"),
    ("GSK",                "gsk",           "GSKCareers",              "wd5"),
    ("Prudential",         "pru",           "Careers",                 "wd5"),
    ("Visa",               "visa",          "Visa",                    "wd5"),
    ("Motorola Solutions", "motorolasolutions", "Careers",             "wd5"),
    ("Target",             "target",        "TargetCareers",           "wd5"),
    ("Boeing",             "boeing",        "EXTERNAL_CAREERS",        "wd1"),
    # Batch 3 — added April 12 2026
    ("Walmart",            "walmart",       "WalmartExternal",         "wd5"),
    ("Capital One",        "capitalone",    "Capital_One",             "wd12"),
    ("Northrop Grumman",   "ngc",           "Northrop_Grumman_External_Site", "wd1"),
    ("Mastercard",         "mastercard",    "CorporateCareers",        "wd1"),
    ("Fidelity",           "fmr",           "FidelityCareers",         "wd1"),
    ("BlackRock",          "blackrock",     "BlackRock_Professional",  "wd1"),
    ("Comcast",            "comcast",       "Comcast_Careers",         "wd5"),
    ("Disney",             "disney",        "disneycareer",            "wd5"),
    ("Snap",               "snapchat",      "sourced",                 "wd1"),
    # Batch 4 — added April 12 2026
    ("S&P Global",         "spgi",          "SPGI_Careers",            "wd5"),
    ("PNC Bank",           "pnc",           "External",                "wd5"),
    ("Guidehouse",         "guidehouse",    "External",                "wd1"),
    ("US Bank",            "usbank",        "US_Bank_Careers",         "wd1"),
    ("TransUnion",         "transunion",    "TransUnion",              "wd5"),
    ("USAA",               "usaa",          "USAAJOBSWD",              "wd1"),
    ("Tempus",             "tempus",        "Tempus_Careers",          "wd5"),
    ("Bloomberg Industry", "bloomberg",     "Bloombergindustrygroup_External_Career_Site", "wd1"),
    ("Blackstone",         "blackstone",    "Blackstone_Campus_Careers","wd1"),
    ("Bain Capital",       "baincapital",   "External_Public",         "wd1"),
    ("Roche/Genentech",    "roche",         "ROG-A2O-GENE",            "wd3"),
    ("Rocket Companies",   "quickenloans",  "rocket_careers",          "wd5"),
    # Batch 5 — added April 12 2026
    ("Abbott",             "abbott",        "AbbottCareers",           "wd5"),
    ("PwC",                "pwc",           "Global_Experienced_Careers","wd3"),
    ("Cigna",              "cigna",         "cignacareers",            "wd5"),
    ("Wells Fargo",        "wf",            "WellsFargoJobs",          "wd1"),
    ("3M",                 "3m",            "Search",                  "wd1"),
    ("Thermo Fisher",      "thermofisher",  "ThermoFisherCareers",     "wd5"),
    ("AT&T",               "att",           "ATTGeneral",              "wd1"),
    ("Humana",             "humana",        "Humana_External_Career_Site","wd5"),
    ("T. Rowe Price",      "troweprice",    "TRowePrice",              "wd5"),
    ("Synchrony",          "synchronyfinancial","careers",             "wd5"),
    ("Verizon",            "verizon",       "verizon-careers",         "wd12"),
    # Batch 6 — added April 12 2026
    ("Hitachi",            "hitachi",       "hitachi",                 "wd1"),
    ("Vanguard",           "vanguard",      "Vanguard_External",       "wd5"),
    ("NXP Semiconductors", "nxp",           "careers",                 "wd3"),
    ("T-Mobile",           "tmobile",       "External",                "wd1"),
    # Batch 7 — added April 13 2026
    ("Amgen",              "amgen",         "Careers",                 "wd1"),
    ("Medtronic",          "medtronic",     "MedtronicCareers",        "wd1"),
    ("Gartner",            "gartner",       "EXT",                     "wd5"),
    ("The Hartford",       "thehartford",   "Careers_External",        "wd5"),
    ("BMO Bank",           "bmo",           "External",                "wd3"),
    ("Guidewire",          "guidewire",     "External",                "wd5"),
    ("GEICO",              "geico",         "external",                "wd1"),
    ("Fractal Analytics",  "fractal",       "Careers",                 "wd1"),
    ("Wiley",              "wiley",         "wiley_careers",           "wd1"),
    ("Acxiom",             "acxiomllc",     "acxiomusa",               "wd5"),
    ("Brookfield",         "brookfield",    "bpandc",                  "wd5"),

    # Workday harvest 2026-04-19
    ("Bah", "bah", "BAH_Jobs", "wd1"),
    ("Paypal", "paypal", "jobs", "wd1"),
    ("Guidehouse", "guidehouse", "External", "wd1"),
    ("Amgen", "amgen", "Careers", "wd1"),
    ("Leidos", "leidos", "External", "wd5"),
    ("Gdit", "gdit", "External_Career_Site", "wd5"),
    ("Thehartford", "thehartford", "Careers_External", "wd5"),
    ("Autodesk", "autodesk", "Ext", "wd1"),
    ("Walmart", "walmart", "Non-WorkdayInternal", "wd5"),
    ("Vanguard", "vanguard", "vanguard_external", "wd5"),
    ("Bristolmyerssquibb", "bristolmyerssquibb", "BMS", "wd5"),
    ("Relx", "relx", "ElsevierJobs", "wd3"),
    ("Centene", "centene", "Centene_External", "wd5"),
    ("Amat", "amat", "External", "wd1"),
    ("Db", "db", "DBWebsite", "wd3"),
    ("Gartner", "gartner", "EXT", "wd5"),
    ("Zendesk", "zendesk", "zendesk", "wd1"),
    ("Abbott", "abbott", "abbottcareers", "wd5"),
    ("Ibotta", "ibotta", "Ibotta", "wd1"),
    ("Dxctechnology", "dxctechnology", "DXCJobs", "wd1"),
    ("Nvidia", "nvidia", "NVIDIAExternalCareerSite", "wd5"),
    ("Rochester", "rochester", "UR_Staff", "wd5"),
    ("Statestreet", "statestreet", "Global", "wd1"),
    ("Transunion", "transunion", "TransUnion", "wd5"),
    ("Huron", "huron", "huroncareers", "wd1"),
    ("Nasdaq", "nasdaq", "Global_External_Site", "wd1"),
    ("Amplify", "amplify", "Amplify_Careers", "wd1"),
    ("Remitly", "remitly", "Remitly_Careers", "wd5"),
    ("Motorolasolutions", "motorolasolutions", "Careers", "wd5"),
    ("Warnerbros", "warnerbros", "global", "wd5"),
    ("Ouryahoo", "ouryahoo", "careers", "wd5"),
    ("Generalmotors", "generalmotors", "Careers_GM", "wd5"),
    ("Pnc", "pnc", "External", "wd5"),
    ("Logitech", "logitech", "Logitech", "wd5"),
    ("Sec", "sec", "Samsung_Careers", "wd3"),
    ("Turo", "turo", "Turo_careers", "wd12"),
    ("Zillow", "zillow", "Zillow_Group_External", "wd5"),
    ("Worldpay", "worldpay", "Worldpay_External_Careers_Site", "wd5"),
    ("Axos", "axos", "Axos", "wd5"),
    ("Workday", "workday", "Workday", "wd5"),
    ("Avav", "avav", "AVAV", "wd1"),
    ("Leonardocompany", "leonardocompany", "LeonardoCareerSite", "wd3"),
    ("Healthcare", "healthcare", "Search", "wd1"),
    ("Transamerica", "transamerica", "US", "wd5"),
    ("Maxar", "maxar", "Vantor", "wd1"),
    ("Cc", "cc", "ChanelCareers", "wd3"),
    ("Trimble", "trimble", "TrimbleCareers", "wd1"),
    ("Quantiphi", "quantiphi", "Careers_at_Quantiphi", "wd1"),
    ("Pacificlife", "pacificlife", "PacificLifeCareers", "wd1"),
    ("Levistraussandco", "levistraussandco", "External", "wd5"),
    ("Aviva", "aviva", "External", "wd1"),
    ("Capitalone", "capitalone", "Capital_One", "wd12"),
    ("Alignmenthealthcare", "alignmenthealthcare", "ahc_external", "wd12"),
    ("Allstate", "allstate", "allstate_careers", "wd5"),
    ("Alliance", "alliance", "nissanjobs", "wd3"),
    ("Ensemblehp", "ensemblehp", "EnsembleHealthPartnersCareers", "wd5"),
    ("Rakuten", "rakuten", "RakutenInc", "wd1"),
    ("Integralads", "integralads", "IAScareers", "wd1"),
    ("Intel", "intel", "External", "wd1"),
    ("Cigna", "cigna", "cignacareers", "wd5"),
    ("Tihinsurance", "tihinsurance", "CRC_Careers", "wd1"),
    ("Gaig", "gaig", "GAIG_External", "wd1"),
    ("Stryker", "stryker", "StrykerCareers", "wd1"),
    ("Workhuman", "workhuman", "WorkhumanCareers", "wd1"),
    ("Conagrabrands", "conagrabrands", "Careers_US", "wd1"),
    ("Amerilife", "amerilife", "External", "wd5"),
    ("Boeing", "boeing", "EXTERNAL_CAREERS", "wd1"),
    ("Hntb", "hntb", "HNTB_Careers", "wd5"),
    ("Northeastern", "northeastern", "careers", "wd1"),
    ("Troweprice", "troweprice", "TRowePrice", "wd5"),
    ("Ntrs", "ntrs", "northerntrust", "wd1"),
    ("Pfizer", "pfizer", "PfizerCareers", "wd1"),
    ("Elanco", "elanco", "External_Career", "wd5"),
    ("Diconium", "diconium", "diconium", "wd3"),
    ("Massgeneralbrigham", "massgeneralbrigham", "MGBExternal", "wd1"),
    ("Datasite", "datasite", "datasite", "wd1"),
    ("Ffive", "ffive", "f5jobs", "wd5"),
    ("Kla", "kla", "Search", "wd1"),
    ("Carislifesciences", "carislifesciences", "CLS", "wd12"),
    ("Genesys", "genesys", "Genesys", "wd1"),
    ("Mymvw", "mymvw", "MVW", "wd5"),
    ("Appliedis", "appliedis", "FeaturedJobs", "wd5"),
    ("Global", "global", "globalpartnerscareers", "wd1"),
    ("Totalwine", "totalwine", "twm", "wd1"),
    ("Terex", "terex", "terexcareers", "wd1"),
    ("Meredith", "meredith", "EXT", "wd5"),
    ("Wk", "wk", "External", "wd3"),
    ("Firstrand", "firstrand", "FRB", "wd3"),
    ("Tcenergy", "tcenergy", "CAREER_SITE_TC", "wd3"),
    ("Pgatoursuperstore", "pgatoursuperstore", "PGAT_SS", "wd12"),
    ("Cbrands", "cbrands", "CBI_External_Careers", "wd5"),
    ("Sunlife", "sunlife", "Experienced-Jobs", "wd3"),
    ("Canadiantirecorporation", "canadiantirecorporation", "Enterprise_External_Careers_Site", "wd3"),
    ("Vizient", "vizient", "Vizient_Careers", "wd1"),
    ("Capgroup", "capgroup", "capitalgroupcareers", "wd1"),
    ("Uchicago", "uchicago", "External", "wd5"),
    ("Mufgub", "mufgub", "MUFG-Careers", "wd3"),
    ("Raymondjames", "raymondjames", "RaymondJamesCareers", "wd1"),
    ("Becu", "becu", "External", "wd1"),
    ("Cliftonlarsonallen", "cliftonlarsonallen", "CLA", "wd1"),
    ("Fca", "fca", "FCA_earlycareers", "wd3"),
    ("Dickssportinggoods", "dickssportinggoods", "DSG", "wd1"),
    ("Ovative", "ovative", "ovative", "wd12"),
    ("Cardinalhealth", "cardinalhealth", "ext", "wd1"),
    ("Vantagedc", "vantagedc", "Vantage", "wd1"),
    ("Rb", "rb", "FRS", "wd5"),
    ("Sterlingmets", "sterlingmets", "Mets", "wd5"),
    ("Epicorsoftware", "epicorsoftware", "epicorjobs", "wd5"),
    ("Mfs", "mfs", "MFS-Careers", "wd1"),
    ("Sbd", "sbdinc", "Stanley_Black_Decker_Career_Site", "wd1"),
    ("Exactsciences", "exactsciences", "Exact_Sciences", "wd1"),
    ("Redfin", "redfin", "redfin_careers", "wd1"),
    ("Theirc", "theirc", "External_Careers", "wd1"),
    ("Gatesfoundation", "gatesfoundation", "Gates", "wd1"),
    ("Cna", "cna", "CNA_Careers", "wd1"),
    ("Shelterinsurance", "shelterinsurance", "careers", "wd5"),
    ("Hp", "hp", "ExternalCareerSite", "wd5"),
    ("Gn", "gn", "GN-Careers", "wd3"),
    ("Georgetown", "georgetown", "Georgetown_Admin_Careers", "wd1"),
    ("Checkout", "checkout", "CheckoutCareers", "wd3"),
    ("Oclc", "oclc", "OCLC_Careers", "wd1"),
    ("Tempus", "tempus", "Tempus_Careers", "wd5"),
    ("Nationstar", "nationstar", "MrCooper", "wd5"),
    ("Fullsteam", "fullsteam", "External", "wd1"),
    ("Wiley", "wiley", "wiley_careers", "wd1"),
    ("Salvationarmyca", "salvationarmyca", "tsacb", "wd3"),
    ("Groundswell", "groundswell", "groundswell", "wd12"),
    ("Meharrymedicalcollege", "meharrymedicalcollege", "external", "wd12"),
    ("Brownhealth", "brownhealth", "External_Careers", "wd12"),
    ("Archgroup", "archgroup", "Careers", "wd1"),
    ("Coke", "coke", "coca-cola-careers", "wd1"),
    ("Tmx", "tmx", "TMX_Careers", "wd3"),
    ("Disney", "disney", "disneycareer", "wd5"),
    ("Lendingclub", "lendingclub", "External", "wd1"),
    ("Thomsonreuters", "thomsonreuters", "External_Career_Site", "wd5"),
    ("Wex", "wexinc", "WEXInc", "wd5"),
    ("Blackstone", "blackstone", "BX_External_Site", "wd1"),
    ("Entegris", "entegris", "EntegrisCareers", "wd1"),
    ("Redhat", "redhat", "Jobs", "wd5"),
    ("Zayo", "zayo", "Zayo_Careers", "wd1"),
    ("Crowdstrike", "crowdstrike", "crowdstrikecareers", "wd5"),
    ("Ciena", "ciena", "Careers", "wd5"),
    ("Arbella", "arbella", "Arbella", "wd5"),
    ("Caa", "caa", "Careers", "wd1"),
    ("Hhmi", "hhmi", "External", "wd1"),
    ("Pressganey", "pressganey", "Careers", "wd1"),
    ("Shi", "shi", "shicareers", "wd12"),
    ("Careoregon", "careoregon", "CO", "wd12"),
    ("Thgrp", "thgrp", "HeritageConstructionMaterials", "wd12"),
    ("Micron", "micron", "External", "wd1"),
    ("Davidyurman", "davidyurman", "DavidYurmanCareers", "wd1"),
    ("Sanofi", "sanofi", "SanofiCareers", "wd3"),
    ("Insulet", "insulet", "insuletcareers", "wd5"),
    ("Shakeshack", "shakeshack", "External", "wd5"),
    ("Iheartmedia", "iheartmedia", "External_iHM", "wd5"),
    ("Utaustin", "utaustin", "UTstaff", "wd1"),
    ("Baincapital", "baincapital", "External_Public", "wd1"),
    ("Pru", "pru", "Careers", "wd5"),
    ("Cmu", "cmu", "CMU", "wd5"),
    ("Barclays", "barclays", "External_Career_Site_Barclays", "wd3"),
    ("Gcu", "gcu", "GCE", "wd1"),
    ("Hklaw", "hklaw", "Holland_Knight", "wd1"),
    ("Pimacounty", "pimacounty", "pimacareers", "wd5"),
    ("Emerson", "emerson", "Emerson_College_Staff", "wd5"),
    ("Methode", "methode", "methode", "wd5"),
    ("Washpost", "washpost", "washingtonpostcareers", "wd5"),
    ("Arabellesolutions", "arabellesolutions", "Arabelle_Solutions", "wd3"),
    ("Montefiore", "montefiore", "MMC", "wd12"),
    ("Cars", "cars", "cars", "wd12"),
    ("Biamp", "biamp", "Biamp", "wd12"),
    ("Humana", "humana", "CenterWell_External_Career_Site", "wd5"),
    ("Assurant", "assurant", "Assurant_Careers", "wd1"),
    ("Duboischemicals", "duboischemicals", "External", "wd1"),
    ("Ag", "ag", "Airbus", "wd3"),
    ("Manulife", "manulife", "MFCJH_AdminJobs", "wd3"),
    ("Snapfinance", "snapfinance", "Snap_External_Careers", "wd1"),
    ("Servco", "servco", "Servco_Careers", "wd5"),
    ("Hq", "hq", "Securian_External", "wd12"),
    ("Swib", "swib", "EXT", "wd12"),
    ("Cambiahealth", "cambiahealth", "External", "wd1"),
    ("Bbinsurance", "bbinsurance", "Careers", "wd1"),
    ("Newrez", "newrez", "NRZ", "wd1"),
    ("Acg", "acg", "Careers", "wd1"),
    ("Greendot", "greendotcorp", "GDC", "wd1"),
    ("Racetrac", "racetrac", "SSC", "wd5"),
    ("Ryan", "ryan", "RyanCareers", "wd1"),
    ("Drinkmilos", "drinkmilos", "drinkmilos", "wd1"),
    ("Maricopa", "maricopa", "MC_External", "wd1"),
    ("Isu", "isu", "iowastatejobs", "wd1"),
    ("Ohiohealth", "ohiohealth", "OhioHealthJobs", "wd5"),
    ("Asuep", "asuep", "ASUEP", "wd5"),
    ("Dukeenergy", "dukeenergy", "search", "wd1"),
    ("Agilent", "agilent", "Agilent_Careers", "wd5"),
    ("Galderma", "galderma", "External", "wd3"),

    # Workday harvest 2026-04-19
    ("Bah", "bah", "BAH_Jobs", "wd1"),
    ("Paypal", "paypal", "jobs", "wd1"),
    ("Guidehouse", "guidehouse", "External", "wd1"),
    ("Amgen", "amgen", "Careers", "wd1"),
    ("Leidos", "leidos", "External", "wd5"),
    ("Gdit", "gdit", "External_Career_Site", "wd5"),
    ("Thehartford", "thehartford", "Careers_External", "wd5"),
    ("Spgi", "spgi", "SPGI_Internal", "wd5"),
    ("Autodesk", "autodesk", "Ext", "wd1"),
    ("Walmart", "walmart", "Non-WorkdayInternal", "wd5"),
    ("Vanguard", "vanguard", "vanguard_external", "wd5"),
    ("Bristolmyerssquibb", "bristolmyerssquibb", "BMS", "wd5"),
    ("Relx", "relx", "ElsevierJobs", "wd3"),
    ("Astreya", "astreya", "life-at-astreya-opportunities", "wd5"),
    ("Amat", "amat", "External", "wd1"),
    ("Db", "db", "DBWebsite", "wd3"),
    ("Gartner", "gartner", "EXT", "wd5"),
    ("Abbott", "abbott", "abbottcareers", "wd5"),
    ("Centene", "centene", "Centene_External", "wd5"),
    ("Ibotta", "ibotta", "Ibotta", "wd1"),
    ("Dxctechnology", "dxctechnology", "DXCJobs", "wd1"),
    ("Rochester", "rochester", "UR_Staff", "wd5"),
    ("Nvidia", "nvidia", "NVIDIAExternalCareerSite", "wd5"),
    ("Statestreet", "statestreet", "Global", "wd1"),
    ("Transunion", "transunion", "TransUnion", "wd5"),
    ("Huron", "huron", "huroncareers", "wd1"),
    ("Nasdaq", "nasdaq", "Global_External_Site", "wd1"),
    ("Amplify", "amplify", "Amplify_Careers", "wd1"),
    ("Motorolasolutions", "motorolasolutions", "Careers", "wd5"),
    ("Logitech", "logitech", "Logitech", "wd5"),
    ("Warnerbros", "warnerbros", "global", "wd5"),
    ("Ouryahoo", "ouryahoo", "careers", "wd5"),
    ("Parsons", "parsons", "Search", "wd5"),
    ("Sec", "sec", "Samsung_Careers", "wd3"),
    ("Turo", "turo", "Turo_careers", "wd12"),
    ("3M", "3m", "Search", "wd1"),
    ("Zillow", "zillow", "Zillow_Group_External", "wd5"),
    ("Worldpay", "worldpay", "Worldpay_External_Careers_Site", "wd5"),
    ("Axos", "axos", "Axos", "wd5"),
    ("Workday", "workday", "Workday", "wd5"),
    ("Avav", "avav", "AVAV", "wd1"),
    ("Leonardocompany", "leonardocompany", "LeonardoCareerSite", "wd3"),
    ("Analogdevices", "analogdevices", "External", "wd1"),
    ("Transamerica", "transamerica", "US", "wd5"),
    ("Cc", "cc", "ChanelCareers", "wd3"),
    ("Pacificlife", "pacificlife", "PacificLifeCareers", "wd1"),
    ("Levistraussandco", "levistraussandco", "External", "wd5"),
    ("Aviva", "aviva", "External", "wd1"),
    ("Cigna", "cigna", "cignacareers", "wd5"),
    ("Capitalone", "capitalone", "Capital_One", "wd12"),
    ("Alignmenthealthcare", "alignmenthealthcare", "ahc_external", "wd12"),
    ("Allstate", "allstate", "allstate_careers", "wd5"),
    ("Alliance", "alliance", "nissanjobs", "wd3"),
    ("Ensemblehp", "ensemblehp", "EnsembleHealthPartnersCareers", "wd5"),
    ("Rakuten", "rakuten", "RakutenInc", "wd1"),
    ("Integralads", "integralads", "IAScareers", "wd1"),
    ("Intel", "intel", "External", "wd1"),
    ("Tihinsurance", "tihinsurance", "CRC_Careers", "wd1"),
    ("Gaig", "gaig", "GAIG_External", "wd1"),
    ("Stryker", "stryker", "StrykerCareers", "wd1"),
    ("Workhuman", "workhuman", "WorkhumanCareers", "wd1"),
    ("Conagrabrands", "conagrabrands", "Careers_US", "wd1"),
    ("Amerilife", "amerilife", "External", "wd5"),
    ("Hntb", "hntb", "HNTB_Careers", "wd5"),
    ("Guardianlife", "guardianlife", "Guardian-Life-Careers", "wd5"),
    ("Northeastern", "northeastern", "careers", "wd1"),
    ("Troweprice", "troweprice", "TRowePrice", "wd5"),
    ("Totalwine", "totalwine", "twm", "wd1"),
    ("Ntrs", "ntrs", "northerntrust", "wd1"),
    ("Pfizer", "pfizer", "PfizerCareers", "wd1"),
    ("Elanco", "elanco", "External_Career", "wd5"),
    ("Massgeneralbrigham", "massgeneralbrigham", "MGBExternal", "wd1"),
    ("Synnex", "synnex", "tdsynnexcareers", "wd5"),
    ("Ffive", "ffive", "f5jobs", "wd5"),
    ("Kla", "kla", "Search", "wd1"),
    ("Carislifesciences", "carislifesciences", "CLS", "wd12"),
    ("Mymvw", "mymvw", "MVW", "wd5"),
    ("Appliedis", "appliedis", "FeaturedJobs", "wd5"),
    ("Meredith", "meredith", "EXT", "wd5"),
    ("Harbourvest", "harbourvest", "HVP", "wd5"),
    ("Wk", "wk", "External", "wd3"),
    ("Tcenergy", "tcenergy", "CAREER_SITE_TC", "wd3"),
    ("Frostbank", "frostbank", "External", "wd5"),
    ("Cbrands", "cbrands", "CBI_External_Careers", "wd5"),
    ("Sunlife", "sunlife", "Experienced-Jobs", "wd3"),
    ("Canadiantirecorporation", "canadiantirecorporation", "Enterprise_External_Careers_Site", "wd3"),
    ("Vizient", "vizient", "Vizient_Careers", "wd1"),
    ("Capgroup", "capgroup", "capitalgroupcareers", "wd1"),
    ("Mufgub", "mufgub", "MUFG-Careers", "wd3"),
    ("Raymondjames", "raymondjames", "RaymondJamesCareers", "wd1"),
    ("Cliftonlarsonallen", "cliftonlarsonallen", "CLA", "wd1"),
    ("Tera", "tera", "TERANET", "wd3"),
    ("Dickssportinggoods", "dickssportinggoods", "DSG", "wd1"),
    ("Cardinalhealth", "cardinalhealth", "ext", "wd1"),
    ("Vantagedc", "vantagedc", "Vantage", "wd1"),
    ("Rb", "rb", "FRS", "wd5"),
    ("Sterlingmets", "sterlingmets", "Mets", "wd5"),
    ("Epicorsoftware", "epicorsoftware", "epicorjobs", "wd5"),
    ("Mfs", "mfs", "MFS-Careers", "wd1"),
    ("Sbd", "sbdinc", "Stanley_Black_Decker_Career_Site", "wd1"),
    ("Communitybrands", "communitybrands", "Momentive_External_Careers", "wd1"),
    ("Uchicago", "uchicago", "External", "wd5"),
    ("Redfin", "redfin", "redfin_careers", "wd1"),
    ("Theirc", "theirc", "External_Careers", "wd1"),
    ("Gatesfoundation", "gatesfoundation", "Gates", "wd1"),
    ("Shelterinsurance", "shelterinsurance", "careers", "wd5"),
    ("Firstrand", "firstrand", "FRB", "wd3"),
    ("Gn", "gn", "GN-Careers", "wd3"),
    ("Georgetown", "georgetown", "Georgetown_Admin_Careers", "wd1"),
    ("Checkout", "checkout", "CheckoutCareers", "wd3"),
    ("Oclc", "oclc", "OCLC_Careers", "wd1"),
    ("Tempus", "tempus", "Tempus_Careers", "wd5"),
    ("Nationstar", "nationstar", "MrCooper", "wd5"),
    ("Fullsteam", "fullsteam", "External", "wd1"),
    ("Wiley", "wiley", "wiley_careers", "wd1"),
    ("Salvationarmyca", "salvationarmyca", "tsacb", "wd3"),
    ("Meharrymedicalcollege", "meharrymedicalcollege", "external", "wd12"),
    ("Brownhealth", "brownhealth", "External_Careers", "wd12"),
    ("Archgroup", "archgroup", "Careers", "wd1"),
    ("Fca", "fca", "FCA_earlycareers", "wd3"),
    ("Tmx", "tmx", "TMX_Careers", "wd3"),
    ("Disney", "disney", "disneycareer", "wd5"),
    ("Comcast", "comcast", "Comcast_Careers", "wd5"),
    ("Thomsonreuters", "thomsonreuters", "External_Career_Site", "wd5"),
    ("Vsp", "vsp", "VSPVisionCareers", "wd1"),
    ("Lendingclub", "lendingclub", "External", "wd1"),
    ("Ryansg", "ryansg", "Ryan_Specialty_Career_Site", "wd5"),
    ("Wex", "wexinc", "WEXInc", "wd5"),
    ("Nationwide", "nationwide", "Nationwide_Career", "wd1"),
    ("Entegris", "entegris", "EntegrisCareers", "wd1"),
    ("Redhat", "redhat", "Jobs", "wd5"),
    ("Hp", "hp", "ExternalCareerSite", "wd5"),
    ("Zayo", "zayo", "Zayo_Careers", "wd1"),
    ("Firstquality", "firstquality", "FIRSTQUALITY", "wd5"),
    ("Arbella", "arbella", "Arbella", "wd5"),
    ("Caa", "caa", "Careers", "wd1"),
    ("Blackstone", "blackstone", "BX_External_Site", "wd1"),
    ("Hhmi", "hhmi", "External", "wd1"),
    ("Pressganey", "pressganey", "Careers", "wd1"),
    ("Cambiahealth", "cambiahealth", "External", "wd1"),
    ("Utaustin", "utaustin", "UTstaff", "wd1"),
    ("Shi", "shi", "shicareers", "wd12"),
    ("Thgrp", "thgrp", "HeritageConstructionMaterials", "wd12"),
    ("Micron", "micron", "External", "wd1"),
    ("Davidyurman", "davidyurman", "DavidYurmanCareers", "wd1"),
    ("Pru", "pru", "Careers", "wd5"),
    ("Sanofi", "sanofi", "SanofiCareers", "wd3"),
    ("Talktalk", "talktalk", "TalkTalkCareers", "wd3"),
    ("Shakeshack", "shakeshack", "External", "wd5"),
    ("Insulet", "insulet", "insuletcareers", "wd5"),
    ("Iheartmedia", "iheartmedia", "External_iHM", "wd5"),
    ("Baincapital", "baincapital", "External_Public", "wd1"),
    ("Cmu", "cmu", "CMU", "wd5"),
    ("Barclays", "barclays", "External_Career_Site_Barclays", "wd3"),
    ("Gcu", "gcu", "GCE", "wd1"),
    ("Hklaw", "hklaw", "Holland_Knight", "wd1"),
    ("Washpost", "washpost", "washingtonpostcareers", "wd5"),
    ("Dodgeandcox", "dodgeandcox", "Dodgecox", "wd5"),
    ("Arabellesolutions", "arabellesolutions", "Arabelle_Solutions", "wd3"),
    ("Montefiore", "montefiore", "MMC", "wd12"),
    ("Biamp", "biamp", "Biamp", "wd12"),
    ("Humana", "humana", "CenterWell_External_Career_Site", "wd5"),
    ("Shm", "shm", "Summit_CityMD", "wd5"),
    ("Gnw", "gnw", "Genworth_Confidential", "wd1"),
    ("Assurant", "assurant", "Assurant_Careers", "wd1"),
    ("Duboischemicals", "duboischemicals", "External", "wd1"),
    ("Ag", "ag", "Airbus", "wd3"),
    ("Snapfinance", "snapfinance", "Snap_External_Careers", "wd1"),
    ("Servco", "servco", "Servco_Careers", "wd5"),
    ("Hq", "hq", "Securian_External", "wd12"),
    ("Swib", "swib", "EXT", "wd12"),
    ("Bbinsurance", "bbinsurance", "Careers", "wd1"),
    ("Newrez", "newrez", "NRZ", "wd1"),
    ("Acg", "acg", "Careers", "wd1"),
    ("Greendot", "greendotcorp", "GDC", "wd1"),
    ("Racetrac", "racetrac", "SSC", "wd5"),
    ("Ryan", "ryan", "RyanCareers", "wd1"),
    ("Isu", "isu", "iowastatejobs", "wd1"),
    ("Maricopa", "maricopa", "MC_External", "wd1"),
    ("Fiserv", "fiserv", "EXT", "wd5"),
    ("Asuep", "asuep", "ASUEP", "wd5"),
    ("Condenast", "condenast", "CondeCareers", "wd5"),
    ("Dukeenergy", "dukeenergy", "search", "wd1"),
    ("Agilent", "agilent", "Agilent_Careers", "wd5"),
    ("Galderma", "galderma", "External", "wd3"),

    # Workday harvest v3 2026-04-19
    ("Paypal", "paypal", "jobs", "wd1"),
    ("Relx", "relx", "relx", "wd3"),
    ("Mckesson", "mckesson", "External_Careers", "wd3"),
    ("Manulife", "manulife", "MFCJH_Jobs", "wd3"),
    ("Leidos", "leidos", "External", "wd5"),
    ("Gdit", "gdit", "External_Career_Site", "wd5"),
    ("Citi", "citi", "2", "wd5"),
    ("Db", "db", "DBWebsite", "wd3"),
    ("Accenture", "accenture", "AccentureCareers", "wd103"),
    ("Pg", "pg", "1000", "wd5"),
    ("Bristolmyerssquibb", "bristolmyerssquibb", "BMS", "wd5"),
    ("Hpe", "hpe", "Jobsathpe", "wd5"),
    ("Airliquide", "airliquidehr", "AirLiquideExternalCareer", "wd3"),
    ("Hcmportal", "hcmportal", "Search", "wd5"),
    ("Iqvia", "iqvia", "IQVIA", "wd1"),
    ("Kbr", "kbr", "KBR_Careers", "wd5"),
    ("Centene", "centene", "Centene_External", "wd5"),
    ("Dentsuaegis", "dentsuaegis", "DAN_GLOBAL", "wd3"),
    ("Statestreet", "statestreet", "Global", "wd1"),
    ("Carrier", "carrier", "jobs", "wd5"),
    ("3M", "3m", "Search", "wd1"),
    ("Myhcm", "myhcm", "Betway", "wd3"),
    ("Warnerbros", "warnerbros", "global", "wd5"),
    ("Globalfoundries", "globalfoundries", "External", "wd1"),
    ("Healthcare", "healthcare", "Search", "wd1"),
    ("Lilly", "lilly", "LLY", "wd5"),
    ("Cc", "cc", "ChanelCareers", "wd3"),
    ("Capitalone", "capitalone", "Capital_One", "wd12"),
    ("Ntrs", "ntrs", "northerntrust", "wd1"),
    ("Cigna", "cigna", "cignacareers", "wd5"),
    ("Novartis", "novartis", "novartis_careers", "wd3"),
    ("Chevron", "chevron", "Jobs", "wd5"),
    ("Nordstrom", "nordstrom", "nordstrom_careers", "wd501"),
    ("Rollsroyce", "rollsroyce", "Professional", "wd3"),
    ("Sunlife", "sunlife", "Experienced", "wd3"),
    ("Unum", "unum", "External", "wd1"),
    ("Xcelenergy", "xcelenergy", "External", "wd1"),
    ("Sterlingmets", "sterlingmets", "Mets", "wd5"),
    ("Solenis", "solenis", "Solenis", "wd1"),
    ("Ing", "ing", "ICSGBLCOR", "wd3"),
    ("Weir", "weir", "Weir_External_Careers", "wd3"),
    ("Fedex", "fedex", "FXE_APAC_External", "wd1"),
    ("Chrobinson", "chrobinson", "CHRobinson", "wd5"),
    ("Ryder", "ryder", "RyderCareers", "wd5"),
    ("Crowdstrike", "crowdstrike", "crowdstrikecareers", "wd5"),
    ("Cvshealth", "cvshealth", "CVS_Health_Careers", "wd1"),
    ("Firstrand", "firstrand", "FRB", "wd3"),
    ("Gap", "gapinc", "GAPINC", "wd1"),
    ("Theirc", "theirc", "External_Careers", "wd1"),
    ("Venerable", "venerable", "venerablecareers", "wd5"),
    ("Embl", "embl", "EMBL", "wd103"),
    ("Lseg", "lseg", "Careers", "wd3"),
    ("Danaher", "danaher", "danaherjobs", "wd1"),
    ("Huntsman", "huntsman", "Huntsman", "wd1"),
    ("Ciena", "ciena", "Careers", "wd5"),
    ("Hp", "hp", "ExternalCareerSite", "wd5"),
    ("Astrazeneca", "astrazeneca", "Careers", "wd3"),
    ("Sonoco", "sonoco", "CorporateCareers", "wd1"),
    ("Comcast", "comcast", "Comcast_Careers", "wd5"),
    ("Sartori", "sartorius", "sartoriuscareers", "wd3"),
    ("Helenoftroy", "helenoftroy", "Main_HoT", "wd503"),
    ("Fiserv", "fiserv", "EXT", "wd5"),
    ("Dexcom", "dexcom", "Dexcom", "wd1"),
    ("Dukeenergy", "dukeenergy", "search", "wd1"),
    ("Eversource", "eversource", "externalsite", "wd1"),
    ("Workiva", "workiva", "careers", "wd503"),
    ("Invesco", "invesco", "IVZ", "wd1"),
    ("Aig", "aig", "aig", "wd1"),
    ("Ag", "ag", "Airbus", "wd3"),
    ("Unisys", "unisys", "External", "wd5"),
    ("Msd", "msd", "SearchJobs", "wd5"),
    ("Roche", "roche", "roche-ext", "wd3"),
    ("Att", "att", "ATTGeneral", "wd1"),
    ("Intrum", "intrum", "External", "wd3"),
    ("Kiongroup", "kiongroup", "KIONGroup", "wd3"),
    ("Saabgroup", "saabgroup", "Combitech_careers", "wd3"),
    ("Dowjones", "dowjones", "News_Corp_Careers", "wd1"),
    ("Washpost", "washpost", "washingtonpostcareers", "wd5"),
    ("Datev", "datev", "Datev_Careers", "wd3"),
    ("Cdw", "cdw", "careers", "wd5"),
]

def _parse_remote_type(remote_type: str):
    """Normalize Workday's remoteType field to our workplace_type enum."""
    if not remote_type:
        return None
    rt = remote_type.lower().strip()
    if "remote" in rt or "virtual" in rt:   # "100% Remote", "Remote", "Fully Remote"
        return "remote"
    if "hybrid" in rt:
        return "hybrid"
    if "on" in rt and "site" in rt:         # "On-Site" / "Onsite"
        return "onsite"
    return None


def _parse_workday_start_date(s: str):
    """Parse Workday's startDate ISO string to a date. Returns None if invalid."""
    if not s:
        return None
    try:
        return datetime.strptime(s.strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ── Workday async config ──────────────────────────────────────────────────────

_WD_USER_AGENTS = [
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_4) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36 Edg/122.0.0.0",
    "Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:123.0) Gecko/20100101 Firefox/123.0",
]
_WD_UA_CYCLE = itertools.cycle(_WD_USER_AGENTS)

_WD_GLOBAL_CONCURRENCY  = 50    # max in-flight requests across all tenants
_WD_PER_HOST            = 3     # max concurrent requests per {tenant}.wdN.myworkdayjobs.com
_WD_TIMEOUT             = aiohttp.ClientTimeout(total=30)
_WD_GLOBAL_429_THRESH   = 5     # pause entire harvester after this many global 429s
_WD_GLOBAL_PAUSE_SECS   = 300   # 5 minutes

_wd_state: Dict = {"global_429": 0, "pause_until": 0.0}

_WD_NON_US = frozenset([
    "singapore", "sgp", "india", "ind", "bangalore", "warsaw", "poland",
    "uk", "london", "germany", "france", "canada", "toronto", "amsterdam",
    "dublin", "australia", "sydney", "tokyo", "japan", "china", "chn",
    "brazil", "mexico", "netherlands", "sweden",
])


async def _wd_check_pause() -> None:
    wait = _wd_state["pause_until"] - time.monotonic()
    if wait > 0:
        log.info(f"Workday: global 429 pause — sleeping {wait:.0f}s")
        await asyncio.sleep(wait)


async def _wd_fetch_page(
    session: aiohttp.ClientSession,
    list_url: str,
    headers: dict,
    offset: int,
    limit: int,
) -> Tuple[List[dict], int, int]:
    """Fetch one list page. Returns (postings, total, http_status)."""
    try:
        async with session.post(
            list_url,
            json={"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""},
            headers=headers,
        ) as r:
            if r.status == 200:
                data = await r.json(content_type=None)
                return data.get("jobPostings", []), data.get("total", 0), 200
            return [], 0, r.status
    except asyncio.TimeoutError:
        return [], 0, 408
    except Exception:
        return [], 0, 0


async def _wd_detail(
    session: aiohttp.ClientSession,
    detail_url: str,
    detail_headers: dict,
) -> Tuple[str, str, Optional[str], Optional[str]]:
    """Fetch job detail page. Returns (desc, location, posted_date, remote_type)."""
    if not detail_url:
        return "", "", None, None
    await _wd_check_pause()
    try:
        async with session.get(detail_url, headers=detail_headers) as r:
            if r.status != 200:
                return "", "", None, None
            detail = await r.json(content_type=None)
            info = detail.get("jobPostingInfo", {})
            desc = (info.get("jobDescription", "")
                    or detail.get("jobDescription", "")
                    or detail.get("description", "") or "")
            desc = re.sub(r"<[^>]+>", " ", desc)
            desc = re.sub(r"\s+", " ", desc).strip()
            location = info.get("location", "")
            remote_type = _parse_remote_type(info.get("remoteType", ""))
            sd = _parse_workday_start_date(info.get("startDate", ""))
            posted_date = str(sd) if sd else None
            return desc, location, posted_date, remote_type
    except Exception:
        return "", "", None, None


async def _fetch_workday_tenant_async(
    session: aiohttp.ClientSession,
    name: str,
    tenant: str,
    board: str,
    wd_server: str,
) -> List[RawJob]:
    base = f"https://{tenant}.{wd_server}.myworkdayjobs.com"
    list_url = f"{base}/wday/cxs/{tenant}/{board}/jobs"
    ua = next(_WD_UA_CYCLE)
    headers = {
        "User-Agent": ua,
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Referer": f"{base}/en-US/{board}",
    }
    limit = 20

    # Phase 1: fetch page 0 to learn total job count
    await _wd_check_pause()
    page0_postings, total, status = await _wd_fetch_page(session, list_url, headers, 0, limit)

    if status == 429:
        _wd_state["global_429"] += 1
        if _wd_state["global_429"] >= _WD_GLOBAL_429_THRESH:
            log.warning(
                f"Workday: {_wd_state['global_429']} global 429s — "
                f"pausing harvester {_WD_GLOBAL_PAUSE_SECS}s"
            )
            _wd_state["pause_until"] = time.monotonic() + _WD_GLOBAL_PAUSE_SECS
            _wd_state["global_429"] = 0
        log.warning(f"  Workday [{name}] 429 on page 0 — skipping tenant")
        return []

    if not page0_postings:
        return []

    all_postings = list(page0_postings)

    # Phase 2: fire all remaining pages concurrently
    if total > limit:
        remaining_offsets = list(range(limit, total, limit))
        log.info(f"  [{name}] {total} total jobs — fetching {len(remaining_offsets) + 1} pages concurrently")
        page_tasks = [
            _wd_fetch_page(session, list_url, headers, off, limit)
            for off in remaining_offsets
        ]
        page_results = await asyncio.gather(*page_tasks, return_exceptions=True)
        for pr in page_results:
            if isinstance(pr, Exception):
                continue
            postings, _, pg_status = pr
            if pg_status == 429:
                _wd_state["global_429"] += 1
            if postings:
                all_postings.extend(postings)

    # Phase 3: parse postings, fire all detail requests concurrently
    posting_meta = []
    detail_coros = []

    for p in all_postings:
        title = p.get("title", "")
        if not _is_target_role(title):
            continue

        ext_path = p.get("externalPath", "")
        job_id = "WD" + hashlib.md5(f"{tenant}|{ext_path}".encode()).hexdigest()[:10]

        location = ""
        locs = p.get("locationsText", "") or p.get("locations", "")
        if isinstance(locs, list) and locs:
            location = locs[0]
        elif isinstance(locs, str):
            location = locs

        workplace_type = _parse_remote_type(p.get("remoteType", ""))
        posting_meta.append((title, ext_path, job_id, location, workplace_type))

        if ext_path:
            clean_path = ext_path.lstrip("/")
            detail_url = f"{base}/wday/cxs/{tenant}/{board}/{clean_path}"
            detail_headers = {
                **headers,
                "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{base}/en-US/{board}{ext_path}",
            }
            detail_coros.append(_wd_detail(session, detail_url, detail_headers))
        else:
            async def _noop():
                return ("", "", None, None)
            detail_coros.append(_noop())

    detail_results = await asyncio.gather(*detail_coros, return_exceptions=True)

    jobs: List[RawJob] = []
    for (title, ext_path, job_id, location, workplace_type), dr in zip(posting_meta, detail_results):
        if isinstance(dr, Exception):
            dr = ("", "", None, None)
        desc, detail_loc, posted_date, detail_remote = dr

        if detail_loc and (not location or "location" in location.lower()):
            location = detail_loc
        if workplace_type is None:
            workplace_type = detail_remote

        loc_lower = location.lower()
        if any(s in loc_lower for s in _WD_NON_US):
            continue
        if location and len(location) > 3 and "," in location:
            last = location.split(",")[-1].strip().upper()
            if len(last) == 3 and last not in ("USA", "CAN") and last.isalpha():
                continue

        if workplace_type is None:
            if "remote" in loc_lower or "virtual" in loc_lower:
                workplace_type = "remote"
            elif "hybrid" in loc_lower:
                workplace_type = "hybrid"

        jobs.append(RawJob(
            source="workday",
            source_id=job_id,
            company=name,
            title=title,
            location=location,
            description=desc,
            job_url=f"{base}/en-US/{board}/{ext_path.lstrip('/')}",
            salary_min=None,
            salary_max=None,
            salary_period=None,
            workplace_type=workplace_type,
            posted_date=posted_date,
        ))

    return jobs


def _load_workday_list() -> List[Tuple[str, str, str, str]]:
    """Return [(name, tenant, board, wd_server), ...] — hardcoded + discovered_companies."""
    workday_list = list(WORKDAY_COMPANIES)
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("""
            SELECT company_name, board_token FROM discovered_companies
            WHERE ats_source = 'workday'
              AND enabled = true
              AND discovery_source IN ('serper_dork', 'workday_probe', 'workday_dork', 'manual')
              AND board_token LIKE '%wd%'
        """)
        rows = cur.fetchall()
        cur.close()
        conn.close()
        hardcoded_tenants = {t for _, t, _, _ in WORKDAY_COMPANIES}
        added = 0
        for company_name, board_token in rows:
            parts = board_token.split("/")
            if len(parts) == 3:
                tenant, wd_server, board = parts
                if board.lower() in ("en-us", "fr-ca", "en-gb", "ja-jp", "de-de"):
                    continue
                if tenant not in hardcoded_tenants:
                    workday_list.append((company_name, tenant, board, wd_server))
                    added += 1
        log.info(
            f"Workday: {len(WORKDAY_COMPANIES)} hardcoded + {added} dynamic "
            f"= {len(workday_list)} total tenants"
        )
    except Exception as e:
        log.warning(f"Could not load dynamic Workday companies: {e}")
    return workday_list


async def _run_all_workday_async(workday_list: List[Tuple]) -> List[RawJob]:
    global _wd_state
    _wd_state = {"global_429": 0, "pause_until": 0.0}
    connector = aiohttp.TCPConnector(
        limit=_WD_GLOBAL_CONCURRENCY, limit_per_host=_WD_PER_HOST
    )
    async with aiohttp.ClientSession(connector=connector, timeout=_WD_TIMEOUT) as session:
        tasks = [
            _fetch_workday_tenant_async(session, name, tenant, board, wd_server)
            for name, tenant, board, wd_server in workday_list
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    all_jobs: List[RawJob] = []
    errors = 0
    for (name, *_), r in zip(workday_list, results):
        if isinstance(r, Exception):
            log.warning(f"  Workday [{name}] unhandled exception: {r}")
            errors += 1
        elif isinstance(r, list):
            if r:
                log.info(f"  Workday [{name}]: {len(r)} jobs")
            all_jobs.extend(r)
    log.info(
        f"Workday async: {len(all_jobs)} jobs from {len(workday_list)} tenants "
        f"({errors} errors)"
    )
    return all_jobs


def fetch_all_workday() -> List[RawJob]:
    workday_list = _load_workday_list()
    t0 = time.monotonic()
    all_jobs = asyncio.run(_run_all_workday_async(workday_list))
    elapsed = time.monotonic() - t0
    log.info(f"Workday total: {len(all_jobs)} jobs in {elapsed:.0f}s ({elapsed / 60:.1f} min)")
    return all_jobs



# ============================================================
# SOURCE: AMAZON
# ============================================================

AMAZON_SEARCH_TERMS = [
    # data_ml
    "data analyst", "data scientist", "data engineer",
    "analytics engineer", "machine learning engineer",
    "business intelligence", "research scientist", "applied scientist",
    # engineering
    "software engineer", "backend engineer", "frontend engineer",
    "devops engineer", "platform engineer", "security engineer",
    # product / design
    "product manager", "product designer", "ux designer",
    # sales / marketing
    "account executive", "solutions engineer", "marketing manager",
    # finance / ops
    "financial analyst", "operations manager", "program manager",
]


def fetch_smartrecruiters(company_name: str, company_slug: str) -> List[RawJob]:
    # SR_PATCH_v1_detail_fetch
    """
    Pull all published jobs from a SmartRecruiters company board.
    No auth required for public boards. Returns paginated JSON.

    NOTE: SR's list endpoint does NOT include jobAd in responses. We must hit
    the per-posting detail endpoint to get descriptions. To avoid wasted API
    calls, we pre-fetch the set of SR job_ids we already have with
    descriptions and skip detail fetches for those.

    Docs: https://developers.smartrecruiters.com/reference/postingapi
    """
    base_url = f"https://api.smartrecruiters.com/v1/companies/{company_slug}/postings"
    jobs: List[RawJob] = []
    offset = 0
    limit = 100

    # Pre-fetch existing SR job_ids that already have descriptions (skip set)
    # Saves us re-hitting the detail endpoint for jobs we've already enriched.
    have_desc: set = set()
    try:
        _conn = get_conn()
        try:
            with _conn.cursor() as _cur:
                _cur.execute("""
                    SELECT job_id FROM job_postings
                    WHERE source = 'smartrecruiters'
                      AND length(COALESCE(description_text, '')) >= 200
                """)
                have_desc = {row[0] for row in _cur.fetchall()}
        finally:
            _conn.close()
    except Exception as _e:
        log.warning(f"SmartRecruiters skip-set prefetch failed: {_e}")
        # Fall through — we'll just fetch all details (slower but correct)

    while True:
        data = _get(base_url, params={"limit": limit, "offset": offset})
        _throttle()
        if not data or not isinstance(data, dict):
            break
        postings = data.get("content", [])
        if not postings:
            break

        for j in postings:
            title = _clean(j.get("name", ""))
            if not title or not _is_target_role(title):
                continue

            # Location
            loc_obj = j.get("location", {}) or {}
            city = _clean(loc_obj.get("city", "") or "")
            region = _clean(loc_obj.get("region", "") or "")
            country = _clean(loc_obj.get("country", "") or "")
            location = ", ".join(p for p in [city, region, country] if p)
            remote_flag = bool(loc_obj.get("remote"))

            # Workplace type
            workplace_type = None
            if remote_flag or "remote" in (location or "").lower():
                workplace_type = "remote"

            # LOC_PATCH_v1: drop foreign via normalize_location
            if normalize_location(location, workplace_type).should_drop:
                continue

            # Compute would-be job_id ahead of detail-fetch decision
            posting_id = j.get("id") or j.get("uuid") or ""
            _candidate_job_id = _md5_id("J", f"smartrecruiters|{posting_id}")

            # SR_PATCH_v1: list endpoint does not include jobAd. Fetch detail
            # endpoint per posting unless we already have this job's description.
            description = ""
            if _candidate_job_id in have_desc:
                # Already have it with a real description — skip detail fetch entirely.
                # We still emit a RawJob so last_seen_at gets touched on the existing row.
                description = ""  # no need; ON CONFLICT will not overwrite description
            else:
                detail_url = j.get("ref") or f"{base_url}/{posting_id}"
                detail = _get(detail_url)
                _throttle()

                desc_parts = []
                if detail and isinstance(detail, dict):
                    ja = detail.get("jobAd", {}) or {}
                    sections = ja.get("sections", {}) or {}
                    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
                        blk = sections.get(key, {}) or {}
                        txt = blk.get("text", "")
                        if txt:
                            desc_parts.append(_strip_html(txt) if "<" in txt else _clean(txt))
                description = "\n\n".join(p for p in desc_parts if p).strip()

            # Industry / type hints
            type_obj = j.get("typeOfEmployment", {}) or {}
            employment_type = (type_obj.get("id") or "").lower().replace("_", "-")
            if employment_type and "full" in employment_type:
                employment_type = "full-time"
            elif employment_type and "part" in employment_type:
                employment_type = "part-time"
            else:
                employment_type = None

            # Posted date
            posted_date = None
            release_dt = j.get("releasedDate") or j.get("createdOn")
            if release_dt and isinstance(release_dt, str):
                posted_date = release_dt[:10]

            # Job URL — public posting URL pattern
            posting_id = j.get("id") or j.get("uuid") or ""
            # SR_PATCH_v1: refNumber is the company-internal code (e.g. "REF2051E") which
            # does NOT resolve on jobs.smartrecruiters.com. The posting_id (numeric SR id)
            # is the canonical part of the public URL.
            ref_num = j.get("refNumber", "")
            job_url = f"https://jobs.smartrecruiters.com/{company_slug}/{posting_id}"

            jobs.append(RawJob(
                source="smartrecruiters",
                source_id=str(posting_id),
                title=title,
                company=company_name,
                location=location,
                description=description,
                job_url=job_url,
                workplace_type=workplace_type,
                employment_type=employment_type,
                posted_date=posted_date,
                salary_min=None,
                salary_max=None,
                salary_period=None,
                metadata={"slug": company_slug, "ref": ref_num},
            ))

        # Pagination — SmartRecruiters returns totalFound; loop until exhausted
        total = data.get("totalFound", 0)
        offset += limit
        if offset >= total:
            break

    log.info(f"  SmartRecruiters [{company_name}]: {len(jobs)} target roles found")
    return jobs


def fetch_all_smartrecruiters() -> List[RawJob]:
    """Fetch all SmartRecruiters companies from discovered_companies table."""
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT company_name, board_token
        FROM discovered_companies
        WHERE ats_source = 'smartrecruiters' AND enabled = true
        ORDER BY active_roles DESC NULLS LAST
    """)
    companies = cur.fetchall()
    cur.close()
    conn.close()

    log.info(f"  SmartRecruiters: pulling {len(companies)} companies from DB")
    all_jobs: List[RawJob] = []
    for name, slug in companies:
        try:
            all_jobs.extend(fetch_smartrecruiters(name, slug))
        except Exception as e:
            log.warning(f"SmartRecruiters [{name}] error: {e}")
    return all_jobs


def fetch_amazon() -> List[RawJob]:
    base = "https://www.amazon.jobs"
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "application/json, text/javascript, */*",
        "Referer": f"{base}/en/search",
    }

    jobs = []
    seen_ids = set()

    for term in AMAZON_SEARCH_TERMS:
        offset = 0
        limit = 20
        while True:
            try:
                r = requests.get(
                    f"{base}/en/search.json",
                    params={"query": term, "offset": offset, "result_limit": limit},
                    headers=headers, timeout=12
                )
                if r.status_code != 200:
                    break
                data = r.json()
                postings = data.get("jobs", [])
                total = data.get("hits", 0)
                if not postings:
                    break

                for j in postings:
                    job_id = str(j.get("id_icims", ""))
                    if not job_id or job_id in seen_ids:
                        continue

                    title = j.get("title", "")
                    if not _is_target_role(title):
                        continue

                    # US only
                    country = j.get("country_code", "")
                    if country and country.upper() not in ("US", "USA", "UNITED STATES"):
                        continue

                    seen_ids.add(job_id)

                    city = j.get("city", "")
                    state = j.get("state", "")
                    location = f"{city}, {state}".strip(", ") if city or state else ""

                    desc = j.get("description", "") or j.get("description_short", "") or ""
                    desc = re.sub(r"<[^>]+>", " ", desc)
                    desc = re.sub(r"\s+", " ", desc).strip()

                    # Workplace type
                    workplace_type = None
                    loc_lower = location.lower() + " " + title.lower()
                    if "remote" in loc_lower:
                        workplace_type = "remote"
                    elif "hybrid" in loc_lower:
                        workplace_type = "hybrid"

                    job_path = j.get("job_path", f"/en/jobs/{job_id}")
                    jobs.append(RawJob(
                        source="amazon",
                        source_id=job_id,
                        company=j.get("company_name", "Amazon"),
                        title=title,
                        location=location,
                        description=desc,
                        job_url=f"{base}{job_path}",
                        salary_min=None,
                        salary_max=None,
                        salary_period=None,
                        workplace_type=workplace_type,
                    ))

                offset += limit
                if offset >= min(total, 200):  # cap at 200 per term
                    break
                time.sleep(0.4)

            except Exception as e:
                log.warning(f"Amazon [{term}] error: {e}")
                break
        time.sleep(0.5)

    log.info(f"Amazon total: {len(jobs)} unique target roles")
    return jobs


# ============================================================
# SOURCE: EIGHTFOLD
# ============================================================

EIGHTFOLD_COMPANIES = [
    # (display_name, subdomain, domain)
    ("Microsoft",       "microsoft",  "microsoft.com"),
    ("American Express","aexp",       "aexp.com"),
    ("Morgan Stanley",  "morganstanley", "morganstanley.com"),
    ("Ford",            "ford",          "ford.com"),
    ("Twilio",          "twilio",        "twilio.com"),
    ("Starbucks",       "starbucks",     "starbucks.com"),
]

def fetch_eightfold_company(name: str, subdomain: str, domain: str) -> List[RawJob]:
    base = f"https://{subdomain}.eightfold.ai"
    headers = {
        "Accept": "application/json",
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Referer": f"{base}/careers",
    }

    search_terms = [""]

    jobs = []
    seen_ids = set()

    for term in search_terms:
        start = 0
        num = 10
        while True:
            try:
                r = requests.get(f"{base}/api/pcsx/search",
                    params={"domain": domain, "query": term, "start": start, "num": num},
                    headers=headers, timeout=12)
                if r.status_code != 200:
                    break
                data = r.json().get("data", {})
                positions = data.get("positions", [])
                count = data.get("count", 0)
                if not positions:
                    break
                if start == 0:
                    log.info(f"    Eightfold [{name}] term='{term}': {count} total")

                for p in positions:
                    job_id = str(p.get("id", ""))
                    if not job_id or job_id in seen_ids:
                        continue

                    title = p.get("name", "")
                    if not _is_target_role(title):
                        continue

                    # US only filter
                    locations = p.get("locations", [])
                    loc_str = " ".join(locations).lower() if locations else ""
                    non_us = ["united kingdom", "london", "india", "bangalore", "canada",
                              "germany", "france", "australia", "singapore", "ireland",
                              "netherlands", "poland", "sweden", "brazil", "mexico",
                              "japan", "china", "spain", "italy", "denmark"]
                    if any(c in loc_str for c in non_us):
                        continue
                    if locations and not any(x in loc_str for x in
                        ["united states", "usa", "remote", "u.s.", "washington", "california",
                         "new york", "texas", "illinois", "georgia", "virginia", "colorado",
                         "massachusetts", "north carolina", "florida", "ohio", "arizona"]):
                        continue

                    seen_ids.add(job_id)

                    # Fetch full description via SmartApply detail
                    desc = ""
                    salary_min, salary_max = None, None
                    job_url = f"{base}/careers/job/{job_id}"
                    try:
                        dr = requests.get(f"{base}/api/apply/v2/jobs/{job_id}",
                            params={"domain": domain, "hl": "en"},
                            headers=headers, timeout=12)
                        if dr.status_code == 200:
                            detail = dr.json()
                            desc = detail.get("job_description", "") or ""
                            desc = re.sub(r"<[^>]+>", " ", desc)
                            desc = re.sub(r"\s+", " ", desc).strip()
                            job_url = detail.get("canonicalPositionUrl", "") or job_url
                    except:
                        pass
                    time.sleep(0.3)

                    location = locations[0] if locations else ""
                    workplace_type = None
                    wlo = p.get("workLocationOption", "")
                    if wlo and "remote" in wlo.lower():
                        workplace_type = "remote"
                    elif wlo and "hybrid" in wlo.lower():
                        workplace_type = "hybrid"
                    elif "remote" in loc_str:
                        workplace_type = "remote"

                    jobs.append(RawJob(
                        source="eightfold",
                        source_id=f"{subdomain}_{job_id}",
                        company=name,
                        title=title,
                        location=location,
                        description=desc,
                        job_url=job_url,
                        salary_min=None,
                        salary_max=None,
                        salary_period=None,
                        workplace_type=workplace_type,
                    ))

                start += num
                if count > 0 and start >= min(count, 500):
                    break
                time.sleep(0.4)

            except Exception as e:
                log.warning(f"Eightfold [{name}] term={term} error: {e}")
                break
        time.sleep(0.5)

    log.info(f"  Eightfold [{name}]: {len(jobs)} target roles found")
    return jobs


def fetch_all_eightfold() -> List[RawJob]:
    all_jobs = []
    for name, subdomain, domain in EIGHTFOLD_COMPANIES:
        try:
            jobs = fetch_eightfold_company(name, subdomain, domain)
            all_jobs.extend(jobs)
            time.sleep(0.5)
        except Exception as e:
            log.warning(f"Eightfold [{name}] failed: {e}")
    log.info(f"Eightfold total: {len(all_jobs)} jobs")
    return all_jobs

def run_ingestion(source: str, apply: bool) -> None:
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

    if source in ("workday", "all"):
        log.info("Fetching from Workday...")
        all_jobs.extend(fetch_all_workday())

    if source in ("eightfold", "all"):
        log.info("Fetching from Eightfold...")
        all_jobs.extend(fetch_all_eightfold())

    if source in ("smartrecruiters", "all"):
        log.info("Fetching from SmartRecruiters...")
        all_jobs.extend(fetch_all_smartrecruiters())

    if source in ("amazon", "all"):
        log.info("Fetching from Amazon...")
        all_jobs.extend(fetch_amazon())

    if source in ("workable", "all"):
        if _fetch_all_workable:
            log.info("Fetching from Workable...")
            all_jobs.extend(_fetch_all_workable())
        else:
            log.warning("Workable harvester not available (workable_harvest.py missing)")

    if source in ("icims", "all"):
        if _fetch_all_icims:
            log.info("Fetching from iCIMS...")
            all_jobs.extend(_fetch_all_icims())
        else:
            log.warning("iCIMS harvester not available (icims_harvest.py missing)")

    if source in ("taleo", "all"):
        if _fetch_all_taleo:
            log.info("Fetching from Taleo...")
            all_jobs.extend(_fetch_all_taleo())
        else:
            log.warning("Taleo harvester not available (taleo_harvest.py missing)")

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
    # Build two sets:
    #   active_cross_keys  — raw/active jobs (block re-insertion)
    #   expired_cross_keys — expired jobs keyed by (company, title, loc) -> job_id
    #                        When a match is found, update last_seen_at instead of dropping
    try:
        _conn = get_conn()
        _cur = _conn.cursor()
        _cur.execute("""
            SELECT lower(c.company_name), lower(r.role_name),
                   CASE
                       WHEN jp.workplace_type = 'remote' THEN 'remote'
                       WHEN l.location IS NULL OR l.location = '' THEN 'unknown'
                       ELSE lower(split_part(l.location, ',', 1))
                   END,
                   jp.status,
                   jp.job_id
            FROM job_postings jp
            JOIN companies c ON c.company_id = jp.company_id
            JOIN roles r ON r.role_id = jp.role_id
            LEFT JOIN locations l ON l.location_id = jp.location_id
            WHERE jp.data_tier = 1
        """)
        rows = _cur.fetchall()
        _cur.close()
        _conn.close()

        active_cross_keys = {}  # (company, title, loc) -> job_id
        expired_cross_map = {}  # (company, title, loc) -> job_id
        for company, title, loc, status, job_id in rows:
            key = (company, title, loc)
            if status == 'raw':
                active_cross_keys[key] = job_id
            else:
                expired_cross_map[key] = job_id

        log.info(f"Loaded {len(active_cross_keys)} active + {len(expired_cross_map)} expired Tier 1 job keys for cross-source dedup")
    except Exception as e:
        log.warning(f"Could not load existing jobs for cross-source dedup: {e}")
        active_cross_keys = set()
        expired_cross_map = {}

    cross_seen = set(active_cross_keys.keys())
    deduped_final = []
    reactivated = 0
    _seen_job_ids = set()  # job_ids to touch last_seen_at in batch

    for job in deduped:
        if is_company_blocked(job.company):
            continue
        loc_key = _norm_location(job)
        cross_key = (
            job.company.lower().strip(),
            job.title.lower().strip(),
            loc_key
        )
        if cross_key in cross_seen:
            # Already active in DB — collect job_id for batch last_seen_at update
            if apply and cross_key in active_cross_keys:
                _seen_job_ids.add(active_cross_keys[cross_key])
            continue

        if cross_key in expired_cross_map:
            # Job was expired but is live again — reactivate by updating last_seen_at
            if apply:
                try:
                    _conn = get_conn()
                    _cur = _conn.cursor()
                    _cur.execute("""
                        UPDATE job_postings
                        SET last_seen_at = now(), status = 'raw'
                        WHERE job_id = %s
                    """, (expired_cross_map[cross_key],))
                    _conn.commit()
                    _cur.close()
                    _conn.close()
                    reactivated += 1
                except Exception as e:
                    log.warning(f"Could not reactivate job {expired_cross_map[cross_key]}: {e}")
            cross_seen.add(cross_key)
            continue

        cross_seen.add(cross_key)
        deduped_final.append(job)

    # Batch update last_seen_at for all deduped-but-active jobs
    if apply and _seen_job_ids:
        try:
            _conn = get_conn()
            _cur = _conn.cursor()
            _cur.execute(
                "UPDATE job_postings SET last_seen_at = now() WHERE job_id = ANY(%s)",
                (list(_seen_job_ids),)
            )
            _conn.commit()
            _cur.close()
            _conn.close()
            log.info(f"Touched last_seen_at for {len(_seen_job_ids)} deduped active jobs")
        except Exception as e:
            log.warning(f"Batch last_seen_at update failed: {e}")

    removed = len(deduped) - len(deduped_final) - reactivated
    if removed > 0:
        log.info(f"Cross-source title+location dedup removed {removed} duplicate postings")
    if reactivated > 0:
        log.info(f"Cross-source dedup reactivated {reactivated} previously expired jobs")
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

    # ---- Remote dedup: keep latest posting per (company, role) for remote jobs ----
    try:
        cur.execute("""
            UPDATE job_postings jp
            SET status = 'ignored'
            WHERE jp.status = 'raw'
              AND jp.workplace_type = 'remote'
              AND jp.job_id NOT IN (
                SELECT DISTINCT ON (company_id, role_id) job_id
                FROM job_postings
                WHERE status = 'raw' AND workplace_type = 'remote'
                ORDER BY company_id, role_id, date_found DESC, ingested_at DESC
              )
        """)
        deduped_remote = cur.rowcount
        if deduped_remote > 0:
            log.info(f"Remote dedup: marked {deduped_remote} duplicate remote postings as ignored")
        conn.commit()
    except Exception as e:
        log.warning(f"Remote dedup pass failed: {e}")
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
        choices=["greenhouse", "lever", "ashby", "workday", "amazon", "eightfold", "smartrecruiters", "workable", "icims", "taleo", "all"],
        default="all",
        help="Which source to pull from (default: all)"
    )
    ap.add_argument(
        "--apply",
        action="store_true",
        help="Write to DB. Without this flag, runs as dry-run."
    )
    args = ap.parse_args()

    run_ingestion(source=args.source, apply=args.apply)


if __name__ == "__main__":
    main()
