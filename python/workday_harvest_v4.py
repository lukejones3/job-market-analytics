#!/usr/bin/env python3
"""
workday_harvest_v4.py

New Workday company discovery targeting sectors NOT covered in v3:
- Tech scale-ups (Series C-D, not yet Fortune 500)
- Regional banks and credit unions
- Specialty pharma / biotech
- Energy / utilities
- Real estate / proptech
- Retail / CPG
- Telecom
- Auto / EV
- Hospitality / travel
- Professional services
- Legal tech / fintech scale-ups
- Gaming
- Food & beverage

Usage:
    python3 python/workday_harvest_v4.py
    python3 python/workday_harvest_v4.py --apply
"""

import os, re, time, logging, argparse, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

SERPER_API_KEY = os.getenv("SERPER_API_KEY")
if not SERPER_API_KEY:
    raise ValueError("SERPER_API_KEY not set in .env")

SERPER_URL    = "https://google.serper.dev/search"
REQUEST_DELAY = 0.5

WD_URL_RE = re.compile(
    r'https://([a-zA-Z0-9_-]+)\.(wd\d+)\.myworkdayjobs\.com'
    r'(?:/[a-zA-Z_-]+)?/([a-zA-Z0-9_-]+)',
    re.IGNORECASE
)

TARGET_RE = re.compile(
    r'\b(data analyst|data engineer|analytics engineer|data scientist|'
    r'machine learning|ml engineer|ai engineer|business intelligence|'
    r'revenue operations|marketing analyst|product analyst|'
    r'financial analyst|data architect|mlops|quantitative|'
    r'data governance|data quality|people analytics|pricing analyst|'
    r'supply chain analyst|risk analyst|fraud analyst)\b',
    re.IGNORECASE
)

JUNK = {'job','jobs','en-us','en','external','apply','careers',
        'search','www','app','api','login','redirect'}

QUERIES = [
    # ── Tech Scale-ups (Series C+ not yet Fortune 500) ────────────────────────
    'site:myworkdayjobs.com "data engineer" "brex"',
    'site:myworkdayjobs.com "data scientist" "rippling"',
    'site:myworkdayjobs.com "data analyst" "gusto"',
    'site:myworkdayjobs.com "data engineer" "lattice"',
    'site:myworkdayjobs.com "data scientist" "deel"',
    'site:myworkdayjobs.com "data analyst" "greenhouse"',
    'site:myworkdayjobs.com "data engineer" "figma"',
    'site:myworkdayjobs.com "data scientist" "notion"',
    'site:myworkdayjobs.com "data analyst" "airtable"',
    'site:myworkdayjobs.com "data engineer" "amplitude"',
    'site:myworkdayjobs.com "data scientist" "mixpanel"',
    'site:myworkdayjobs.com "data engineer" "segment"',
    'site:myworkdayjobs.com "data analyst" "miro"',
    'site:myworkdayjobs.com "data scientist" "contentful"',
    'site:myworkdayjobs.com "data engineer" "elastic"',
    'site:myworkdayjobs.com "data analyst" "zendesk"',
    'site:myworkdayjobs.com "data engineer" "twilio"',
    'site:myworkdayjobs.com "data scientist" "sendgrid"',
    'site:myworkdayjobs.com "data analyst" "hubspot"',
    'site:myworkdayjobs.com "data engineer" "datadog"',
    'site:myworkdayjobs.com "data scientist" "pagerduty"',
    'site:myworkdayjobs.com "data engineer" "cloudflare"',
    'site:myworkdayjobs.com "data analyst" "fastly"',
    'site:myworkdayjobs.com "data scientist" "hashicorp"',
    'site:myworkdayjobs.com "data engineer" "confluent"',
    'site:myworkdayjobs.com "data analyst" "dbt labs"',
    'site:myworkdayjobs.com "data scientist" "fivetran"',
    'site:myworkdayjobs.com "data engineer" "airbyte"',
    'site:myworkdayjobs.com "data analyst" "astronomer"',
    'site:myworkdayjobs.com "data scientist" "monte carlo"',

    # ── Regional & Mid-size Banks ─────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "regions bank"',
    'site:myworkdayjobs.com "data engineer" "fifth third bank"',
    'site:myworkdayjobs.com "data scientist" "huntington bank"',
    'site:myworkdayjobs.com "data analyst" "citizens bank"',
    'site:myworkdayjobs.com "data engineer" "m&t bank"',
    'site:myworkdayjobs.com "data scientist" "comerica"',
    'site:myworkdayjobs.com "data analyst" "synovus"',
    'site:myworkdayjobs.com "data engineer" "first horizon"',
    'site:myworkdayjobs.com "data scientist" "associated bank"',
    'site:myworkdayjobs.com "data analyst" "wintrust"',
    'site:myworkdayjobs.com "data engineer" "old national bank"',
    'site:myworkdayjobs.com "data scientist" "glacier bancorp"',
    'site:myworkdayjobs.com "data analyst" "banner bank"',
    'site:myworkdayjobs.com "data engineer" "pacific premier bank"',
    'site:myworkdayjobs.com "data scientist" "western alliance bank"',
    'site:myworkdayjobs.com "data analyst" "east west bank"',
    'site:myworkdayjobs.com "data engineer" "cathay bank"',
    'site:myworkdayjobs.com "data scientist" "sterling bancorp"',
    'site:myworkdayjobs.com "data analyst" "glacier hills bank"',
    'site:myworkdayjobs.com "data engineer" "heartland financial"',

    # ── Specialty Pharma / Biotech ─────────────────────────────────────────────
    'site:myworkdayjobs.com "data scientist" "vertex pharmaceuticals"',
    'site:myworkdayjobs.com "data engineer" "regeneron"',
    'site:myworkdayjobs.com "data analyst" "biogen"',
    'site:myworkdayjobs.com "data scientist" "alexion"',
    'site:myworkdayjobs.com "data engineer" "seagen"',
    'site:myworkdayjobs.com "data analyst" "alnylam"',
    'site:myworkdayjobs.com "data scientist" "blueprint medicines"',
    'site:myworkdayjobs.com "data engineer" "beam therapeutics"',
    'site:myworkdayjobs.com "data analyst" "karuna therapeutics"',
    'site:myworkdayjobs.com "data scientist" "sage therapeutics"',
    'site:myworkdayjobs.com "data engineer" "translate bio"',
    'site:myworkdayjobs.com "data analyst" "arrowhead pharmaceuticals"',
    'site:myworkdayjobs.com "data scientist" "ultragenyx"',
    'site:myworkdayjobs.com "data engineer" "horizon therapeutics"',
    'site:myworkdayjobs.com "data analyst" "acceleron pharma"',
    'site:myworkdayjobs.com "data scientist" "neurocrine biosciences"',
    'site:myworkdayjobs.com "data engineer" "arena pharmaceuticals"',
    'site:myworkdayjobs.com "data analyst" "ironwood pharmaceuticals"',
    'site:myworkdayjobs.com "data scientist" "sarepta therapeutics"',
    'site:myworkdayjobs.com "data engineer" "global blood therapeutics"',

    # ── Energy & Utilities ────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "dominion energy"',
    'site:myworkdayjobs.com "data engineer" "duke energy"',
    'site:myworkdayjobs.com "data scientist" "southern company"',
    'site:myworkdayjobs.com "data analyst" "entergy"',
    'site:myworkdayjobs.com "data engineer" "exelon"',
    'site:myworkdayjobs.com "data scientist" "ameren"',
    'site:myworkdayjobs.com "data analyst" "xcel energy"',
    'site:myworkdayjobs.com "data engineer" "consolidated edison"',
    'site:myworkdayjobs.com "data scientist" "eversource"',
    'site:myworkdayjobs.com "data analyst" "pg&e"',
    'site:myworkdayjobs.com "data engineer" "sempra energy"',
    'site:myworkdayjobs.com "data scientist" "dte energy"',
    'site:myworkdayjobs.com "data analyst" "firstenergy"',
    'site:myworkdayjobs.com "data engineer" "evergy"',
    'site:myworkdayjobs.com "data scientist" "avangrid"',
    'site:myworkdayjobs.com "data analyst" "nextera energy"',
    'site:myworkdayjobs.com "data engineer" "apa corporation"',
    'site:myworkdayjobs.com "data scientist" "pioneer natural resources"',
    'site:myworkdayjobs.com "data analyst" "devon energy"',
    'site:myworkdayjobs.com "data engineer" "coterra energy"',
    'site:myworkdayjobs.com "data scientist" "diamondback energy"',
    'site:myworkdayjobs.com "data analyst" "ovintiv"',
    'site:myworkdayjobs.com "data engineer" "marathon oil"',
    'site:myworkdayjobs.com "data scientist" "hess corporation"',
    'site:myworkdayjobs.com "data analyst" "suncor"',

    # ── Auto / EV ─────────────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "ford motor"',
    'site:myworkdayjobs.com "data scientist" "general motors"',
    'site:myworkdayjobs.com "data analyst" "stellantis"',
    'site:myworkdayjobs.com "data engineer" "toyota"',
    'site:myworkdayjobs.com "data scientist" "honda"',
    'site:myworkdayjobs.com "data analyst" "bmw"',
    'site:myworkdayjobs.com "data engineer" "mercedes benz"',
    'site:myworkdayjobs.com "data scientist" "volkswagen"',
    'site:myworkdayjobs.com "data analyst" "volvo"',
    'site:myworkdayjobs.com "data engineer" "rivian"',
    'site:myworkdayjobs.com "data scientist" "lucid motors"',
    'site:myworkdayjobs.com "data analyst" "fisker"',
    'site:myworkdayjobs.com "data engineer" "canoo"',
    'site:myworkdayjobs.com "data scientist" "lordstown motors"',
    'site:myworkdayjobs.com "data analyst" "borgwarner"',
    'site:myworkdayjobs.com "data engineer" "aptiv"',
    'site:myworkdayjobs.com "data scientist" "lear corporation"',
    'site:myworkdayjobs.com "data analyst" "dana incorporated"',
    'site:myworkdayjobs.com "data engineer" "tenneco"',
    'site:myworkdayjobs.com "data scientist" "modine manufacturing"',

    # ── Retail / CPG ─────────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "kroger"',
    'site:myworkdayjobs.com "data scientist" "albertsons"',
    'site:myworkdayjobs.com "data analyst" "publix"',
    'site:myworkdayjobs.com "data engineer" "ahold delhaize"',
    'site:myworkdayjobs.com "data scientist" "dollar general"',
    'site:myworkdayjobs.com "data analyst" "dollar tree"',
    'site:myworkdayjobs.com "data engineer" "ross stores"',
    'site:myworkdayjobs.com "data scientist" "tjx companies"',
    'site:myworkdayjobs.com "data analyst" "nordstrom"',
    'site:myworkdayjobs.com "data engineer" "macys"',
    'site:myworkdayjobs.com "data scientist" "gap"',
    'site:myworkdayjobs.com "data analyst" "pvh corp"',
    'site:myworkdayjobs.com "data engineer" "hanesbrands"',
    'site:myworkdayjobs.com "data scientist" "carter"',
    'site:myworkdayjobs.com "data analyst" "tapestry"',
    'site:myworkdayjobs.com "data engineer" "capri holdings"',
    'site:myworkdayjobs.com "data scientist" "estee lauder"',
    'site:myworkdayjobs.com "data analyst" "coty"',
    'site:myworkdayjobs.com "data engineer" "revlon"',
    'site:myworkdayjobs.com "data scientist" "church dwight"',
    'site:myworkdayjobs.com "data analyst" "spectrum brands"',
    'site:myworkdayjobs.com "data engineer" "energizer"',
    'site:myworkdayjobs.com "data scientist" "prestige consumer"',
    'site:myworkdayjobs.com "data analyst" "helen troy"',
    'site:myworkdayjobs.com "data engineer" "edgewell personal care"',

    # ── Telecom ───────────────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data engineer" "t-mobile"',
    'site:myworkdayjobs.com "data scientist" "att"',
    'site:myworkdayjobs.com "data analyst" "verizon"',
    'site:myworkdayjobs.com "data engineer" "lumen technologies"',
    'site:myworkdayjobs.com "data scientist" "charter communications"',
    'site:myworkdayjobs.com "data analyst" "altice usa"',
    'site:myworkdayjobs.com "data engineer" "cox communications"',
    'site:myworkdayjobs.com "data scientist" "zayo group"',
    'site:myworkdayjobs.com "data analyst" "windstream"',
    'site:myworkdayjobs.com "data engineer" "consolidated communications"',
    'site:myworkdayjobs.com "data scientist" "frontier communications"',
    'site:myworkdayjobs.com "data analyst" "brightspeed"',
    'site:myworkdayjobs.com "data engineer" "dish network"',
    'site:myworkdayjobs.com "data scientist" "directv"',
    'site:myworkdayjobs.com "data analyst" "shenandoah telecom"',

    # ── Real Estate / Proptech ────────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "cbre"',
    'site:myworkdayjobs.com "data engineer" "jll"',
    'site:myworkdayjobs.com "data scientist" "cushman wakefield"',
    'site:myworkdayjobs.com "data analyst" "colliers"',
    'site:myworkdayjobs.com "data engineer" "newmark"',
    'site:myworkdayjobs.com "data scientist" "prologis"',
    'site:myworkdayjobs.com "data analyst" "equinix"',
    'site:myworkdayjobs.com "data engineer" "digital realty"',
    'site:myworkdayjobs.com "data scientist" "iron mountain"',
    'site:myworkdayjobs.com "data analyst" "simon property"',
    'site:myworkdayjobs.com "data engineer" "brookfield"',
    'site:myworkdayjobs.com "data scientist" "hines"',
    'site:myworkdayjobs.com "data analyst" "related companies"',
    'site:myworkdayjobs.com "data engineer" "greystar"',
    'site:myworkdayjobs.com "data scientist" "lincoln property"',
    'site:myworkdayjobs.com "data analyst" "costar group"',
    'site:myworkdayjobs.com "data engineer" "realpage"',
    'site:myworkdayjobs.com "data scientist" "yardi"',
    'site:myworkdayjobs.com "data analyst" "mri software"',
    'site:myworkdayjobs.com "data engineer" "entrata"',

    # ── Hospitality / Travel ──────────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "marriott"',
    'site:myworkdayjobs.com "data engineer" "hilton"',
    'site:myworkdayjobs.com "data scientist" "hyatt"',
    'site:myworkdayjobs.com "data analyst" "ihg"',
    'site:myworkdayjobs.com "data engineer" "wyndham"',
    'site:myworkdayjobs.com "data scientist" "choice hotels"',
    'site:myworkdayjobs.com "data analyst" "best western"',
    'site:myworkdayjobs.com "data engineer" "expedia"',
    'site:myworkdayjobs.com "data scientist" "booking holdings"',
    'site:myworkdayjobs.com "data analyst" "tripadvisor"',
    'site:myworkdayjobs.com "data engineer" "sabre"',
    'site:myworkdayjobs.com "data scientist" "amadeus"',
    'site:myworkdayjobs.com "data analyst" "global hotel alliance"',
    'site:myworkdayjobs.com "data engineer" "carnival corporation"',
    'site:myworkdayjobs.com "data scientist" "royal caribbean"',
    'site:myworkdayjobs.com "data analyst" "norwegian cruise"',
    'site:myworkdayjobs.com "data engineer" "enterprise rent"',
    'site:myworkdayjobs.com "data scientist" "hertz"',
    'site:myworkdayjobs.com "data analyst" "avis budget"',
    'site:myworkdayjobs.com "data engineer" "budget car rental"',

    # ── Gaming ────────────────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "activision blizzard"',
    'site:myworkdayjobs.com "data engineer" "electronic arts"',
    'site:myworkdayjobs.com "data scientist" "take two interactive"',
    'site:myworkdayjobs.com "data analyst" "ubisoft"',
    'site:myworkdayjobs.com "data engineer" "zynga"',
    'site:myworkdayjobs.com "data scientist" "roblox"',
    'site:myworkdayjobs.com "data analyst" "unity technologies"',
    'site:myworkdayjobs.com "data engineer" "epic games"',
    'site:myworkdayjobs.com "data scientist" "riot games"',
    'site:myworkdayjobs.com "data analyst" "2k games"',
    'site:myworkdayjobs.com "data engineer" "square enix"',
    'site:myworkdayjobs.com "data scientist" "bandai namco"',
    'site:myworkdayjobs.com "data analyst" "sega"',
    'site:myworkdayjobs.com "data engineer" "ncsoft"',
    'site:myworkdayjobs.com "data scientist" "nexon"',
    'site:myworkdayjobs.com "data analyst" "gearbox software"',
    'site:myworkdayjobs.com "data engineer" "insomniac games"',
    'site:myworkdayjobs.com "data scientist" "naughty dog"',
    'site:myworkdayjobs.com "data analyst" "bungie"',
    'site:myworkdayjobs.com "data engineer" "id software"',

    # ── Food & Beverage ───────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "pepsi"',
    'site:myworkdayjobs.com "data engineer" "coca cola"',
    'site:myworkdayjobs.com "data scientist" "nestle"',
    'site:myworkdayjobs.com "data analyst" "unilever"',
    'site:myworkdayjobs.com "data engineer" "kraft heinz"',
    'site:myworkdayjobs.com "data scientist" "general mills"',
    'site:myworkdayjobs.com "data analyst" "conagra"',
    'site:myworkdayjobs.com "data engineer" "kellogg"',
    'site:myworkdayjobs.com "data scientist" "campbell soup"',
    'site:myworkdayjobs.com "data analyst" "hershey"',
    'site:myworkdayjobs.com "data engineer" "mondelez"',
    'site:myworkdayjobs.com "data scientist" "mars"',
    'site:myworkdayjobs.com "data analyst" "tyson foods"',
    'site:myworkdayjobs.com "data engineer" "jbs usa"',
    'site:myworkdayjobs.com "data scientist" "smithfield foods"',
    'site:myworkdayjobs.com "data analyst" "hormel foods"',
    'site:myworkdayjobs.com "data engineer" "sysco"',
    'site:myworkdayjobs.com "data scientist" "us foods"',
    'site:myworkdayjobs.com "data analyst" "performance food group"',
    'site:myworkdayjobs.com "data engineer" "gordon food service"',

    # ── Professional Services / Consulting ────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "accenture"',
    'site:myworkdayjobs.com "data engineer" "capgemini"',
    'site:myworkdayjobs.com "data scientist" "cognizant"',
    'site:myworkdayjobs.com "data analyst" "infosys"',
    'site:myworkdayjobs.com "data engineer" "wipro"',
    'site:myworkdayjobs.com "data scientist" "hcl technologies"',
    'site:myworkdayjobs.com "data analyst" "tech mahindra"',
    'site:myworkdayjobs.com "data engineer" "dxc technology"',
    'site:myworkdayjobs.com "data scientist" "unisys"',
    'site:myworkdayjobs.com "data analyst" "leidos"',
    'site:myworkdayjobs.com "data engineer" "saic"',
    'site:myworkdayjobs.com "data scientist" "parsons"',
    'site:myworkdayjobs.com "data analyst" "aecom"',
    'site:myworkdayjobs.com "data engineer" "jacobs engineering"',
    'site:myworkdayjobs.com "data scientist" "icf international"',
    'site:myworkdayjobs.com "data analyst" "navigant consulting"',
    'site:myworkdayjobs.com "data engineer" "huron consulting"',
    'site:myworkdayjobs.com "data scientist" "west monroe"',
    'site:myworkdayjobs.com "data analyst" "guidehouse"',
    'site:myworkdayjobs.com "data engineer" "grant thornton"',

    # ── Insurance ─────────────────────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "allstate"',
    'site:myworkdayjobs.com "data engineer" "progressive insurance"',
    'site:myworkdayjobs.com "data scientist" "nationwide insurance"',
    'site:myworkdayjobs.com "data analyst" "travelers insurance"',
    'site:myworkdayjobs.com "data engineer" "liberty mutual"',
    'site:myworkdayjobs.com "data scientist" "hartford financial"',
    'site:myworkdayjobs.com "data analyst" "cna financial"',
    'site:myworkdayjobs.com "data engineer" "markel"',
    'site:myworkdayjobs.com "data scientist" "erie insurance"',
    'site:myworkdayjobs.com "data analyst" "auto owners insurance"',
    'site:myworkdayjobs.com "data engineer" "employers holdings"',
    'site:myworkdayjobs.com "data scientist" "rli corp"',
    'site:myworkdayjobs.com "data analyst" "american financial group"',
    'site:myworkdayjobs.com "data engineer" "fgl holdings"',
    'site:myworkdayjobs.com "data scientist" "global indemnity"',

    # ── Manufacturing / Industrial ────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "caterpillar"',
    'site:myworkdayjobs.com "data engineer" "deere"',
    'site:myworkdayjobs.com "data scientist" "emerson electric"',
    'site:myworkdayjobs.com "data analyst" "parker hannifin"',
    'site:myworkdayjobs.com "data engineer" "eaton"',
    'site:myworkdayjobs.com "data scientist" "illinois tool works"',
    'site:myworkdayjobs.com "data analyst" "dover corporation"',
    'site:myworkdayjobs.com "data engineer" "roper technologies"',
    'site:myworkdayjobs.com "data scientist" "ametek"',
    'site:myworkdayjobs.com "data analyst" "fortive"',
    'site:myworkdayjobs.com "data engineer" "watts water"',
    'site:myworkdayjobs.com "data scientist" "xylem"',
    'site:myworkdayjobs.com "data analyst" "rexnord"',
    'site:myworkdayjobs.com "data engineer" "franklin electric"',
    'site:myworkdayjobs.com "data scientist" "watts water technologies"',
    'site:myworkdayjobs.com "data analyst" "watts water"',
    'site:myworkdayjobs.com "data engineer" "graco"',
    'site:myworkdayjobs.com "data scientist" "nordson"',
    'site:myworkdayjobs.com "data analyst" "regal rexnord"',
    'site:myworkdayjobs.com "data engineer" "haynes international"',

    # ── Media & Entertainment ─────────────────────────────────────────────────
    'site:myworkdayjobs.com "data analyst" "warner music"',
    'site:myworkdayjobs.com "data engineer" "sony music"',
    'site:myworkdayjobs.com "data scientist" "universal music"',
    'site:myworkdayjobs.com "data analyst" "live nation"',
    'site:myworkdayjobs.com "data engineer" "amc networks"',
    'site:myworkdayjobs.com "data scientist" "discovery"',
    'site:myworkdayjobs.com "data analyst" "lions gate"',
    'site:myworkdayjobs.com "data engineer" "mgm"',
    'site:myworkdayjobs.com "data scientist" "imax"',
    'site:myworkdayjobs.com "data analyst" "cinemark"',
    'site:myworkdayjobs.com "data engineer" "amc entertainment"',
    'site:myworkdayjobs.com "data scientist" "regal cinemas"',

    # ── Specific role + Workday keyword queries ────────────────────────────────
    'site:myworkdayjobs.com "machine learning engineer" "salary" 2024 OR 2025',
    'site:myworkdayjobs.com "analytics engineer" "dbt" OR "snowflake" "salary"',
    'site:myworkdayjobs.com "data engineer" "spark" OR "kafka" "salary"',
    'site:myworkdayjobs.com "data scientist" "pytorch" OR "tensorflow" "salary"',
    'site:myworkdayjobs.com "mlops engineer" "kubernetes" OR "docker"',
    'site:myworkdayjobs.com "ai engineer" "llm" OR "large language model"',
    'site:myworkdayjobs.com "data analyst" "tableau" OR "looker" "salary"',
    'site:myworkdayjobs.com "quantitative analyst" "python" OR "r" "salary"',
    'site:myworkdayjobs.com "staff data scientist" "salary" "equity"',
    'site:myworkdayjobs.com "principal data engineer" "remote" "salary"',
    'site:myworkdayjobs.com "senior analytics engineer" "dbt" "snowflake"',
    'site:myworkdayjobs.com "data platform engineer" "spark" "salary"',
    'site:myworkdayjobs.com "machine learning scientist" "phd" "salary"',
    'site:myworkdayjobs.com "applied scientist" "nlp" OR "computer vision"',
    'site:myworkdayjobs.com "research scientist" "deep learning" "salary"',

    # ── Geographic targeting for underrepresented metros ──────────────────────
    'site:myworkdayjobs.com "data engineer" "dallas" OR "fort worth" "salary"',
    'site:myworkdayjobs.com "data scientist" "houston" "salary"',
    'site:myworkdayjobs.com "data analyst" "phoenix" OR "scottsdale" "salary"',
    'site:myworkdayjobs.com "data engineer" "denver" OR "boulder" "salary"',
    'site:myworkdayjobs.com "data scientist" "minneapolis" OR "st paul" "salary"',
    'site:myworkdayjobs.com "data analyst" "detroit" OR "ann arbor" "salary"',
    'site:myworkdayjobs.com "data engineer" "pittsburgh" "salary"',
    'site:myworkdayjobs.com "data scientist" "raleigh" OR "durham" "salary"',
    'site:myworkdayjobs.com "data analyst" "charlotte" "salary"',
    'site:myworkdayjobs.com "data engineer" "nashville" "salary"',
    'site:myworkdayjobs.com "data scientist" "st louis" "salary"',
    'site:myworkdayjobs.com "data analyst" "kansas city" "salary"',
    'site:myworkdayjobs.com "data engineer" "columbus" "salary"',
    'site:myworkdayjobs.com "data scientist" "cincinnati" "salary"',
    'site:myworkdayjobs.com "data analyst" "indianapolis" "salary"',
    'site:myworkdayjobs.com "data engineer" "milwaukee" "salary"',
    'site:myworkdayjobs.com "data scientist" "memphis" "salary"',
    'site:myworkdayjobs.com "data analyst" "richmond" "salary"',
    'site:myworkdayjobs.com "data engineer" "jacksonville" "salary"',
    'site:myworkdayjobs.com "data scientist" "oklahoma city" "salary"',
]


def search_serper(query: str) -> list[dict]:
    try:
        r = requests.post(
            SERPER_URL,
            headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
            json={"q": query, "num": 10},
            timeout=15,
        )
        r.raise_for_status()
        return r.json().get("organic", [])
    except Exception as e:
        log.warning(f"Serper error for '{query}': {e}")
        return []


def extract_workday_tenants(results: list[dict]) -> list[tuple]:
    found = []
    for r in results:
        for field in [r.get("link",""), r.get("snippet",""), r.get("title","")]:
            for m in WD_URL_RE.finditer(field):
                tenant  = m.group(1).lower()
                wd_srv  = m.group(2).lower()
                board   = m.group(3)
                if tenant not in JUNK and len(tenant) >= 2:
                    # Try to find company name from result title
                    title = r.get("title", "")
                    name = title.split("|")[0].split("-")[0].split("–")[0].strip()
                    if not name or len(name) > 60:
                        name = tenant.replace("-", " ").title()
                    found.append((name, tenant, board, wd_srv))
    return found


def probe_workday(tenant: str, board: str, wd_server: str) -> int:
    """Check how many target roles exist for this tenant."""
    url = f"https://{tenant}.{wd_server}.myworkdayjobs.com/wday/cxs/{tenant}/{board}/jobs"
    try:
        r = requests.post(
            url,
            json={"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": "data"},
            headers={"Content-Type": "application/json"},
            timeout=6,
        )
        if r.status_code != 200:
            return 0
        data = r.json()
        jobs = data.get("jobPostings", [])
        target_count = sum(1 for j in jobs if TARGET_RE.search(j.get("title", "")))
        return target_count
    except Exception:
        return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="Write new tenants to discovered_companies")
    args = ap.parse_args()

    import psycopg2
    from psycopg2.extras import DictCursor
    from datetime import datetime, timezone

    conn = psycopg2.connect(
        host=os.getenv("PGHOST"), port=int(os.getenv("PGPORT", 5432)),
        dbname=os.getenv("PGDATABASE"), user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=DictCursor)

    # Load existing tenants
    cur.execute("SELECT board_token FROM discovered_companies WHERE ats_source='workday'")
    existing = {r[0].lower() for r in cur.fetchall()}
    log.info(f"Existing Workday tenants: {len(existing)}")

    # Also load from WORKDAY_COMPANIES in ingest_jobs
    seen_tenants = set(existing)
    new_found: dict[str, tuple] = {}

    for i, query in enumerate(QUERIES):
        log.info(f"[{i+1}/{len(QUERIES)}] {query[:80]}")
        results = search_serper(query)

        for name, tenant, board, wd_server in extract_workday_tenants(results):
            key = f"{tenant}/{board}".lower()
            if tenant in seen_tenants or key in seen_tenants:
                continue
            if tenant in new_found:
                continue

            log.info(f"  🔍 New tenant: {name} → {tenant}.{wd_server} / {board}")
            count = probe_workday(tenant, board, wd_server)
            if count > 0:
                log.info(f"  ✅ {name}: {count} target roles found")
                new_found[tenant] = (name, tenant, board, wd_server, count)
                seen_tenants.add(tenant)
            else:
                log.info(f"  ⚠️  {name}: 0 target roles — skipping")

            time.sleep(REQUEST_DELAY)

        time.sleep(REQUEST_DELAY)

    log.info(f"\n{'='*60}")
    log.info(f"NEW TENANTS FOUND: {len(new_found)}")
    for tenant, (name, t, board, wd_srv, count) in sorted(new_found.items(), key=lambda x: -x[1][4]):
        log.info(f"  {name:40} {t:20} {board:30} {wd_srv}  ({count} roles)")

    if args.apply and new_found:
        now = datetime.now(timezone.utc)
        inserted = 0
        import uuid
        for tenant, (name, t, board, wd_srv, count) in new_found.items():
            token = f"{t}/{board}"
            company_id = str(uuid.uuid4())
            cur.execute("""
                INSERT INTO discovered_companies
                    (company_id, company_name, ats_source, board_token, first_seen_at, last_seen_at, enabled)
                VALUES (%s, %s, 'workday', %s, %s, %s, true)
                ON CONFLICT (ats_source, board_token) DO NOTHING
            """, (company_id, name, token, now, now))
            if cur.rowcount:
                inserted += 1
                log.info(f"  ✅ Inserted: {name}")
        log.info(f"Inserted {inserted} new Workday companies")
    elif not args.apply:
        log.info("Dry run — pass --apply to write to DB")

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
