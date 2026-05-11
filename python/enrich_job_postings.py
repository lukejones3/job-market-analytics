#!/usr/bin/env python3
import asyncio
import hashlib
import logging
import os
import re
import argparse
import time
from pathlib import Path
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List, Tuple

log = logging.getLogger(__name__)

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor, execute_batch

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from vertical_taxonomy import VERTICALS
from classify_domain import build_alias_map as _build_domain_alias_map, classify_domain as _classify_domain_fn

# Always load .env from repo root (safe in heredocs / python -c)
load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / ".env")

# ============================================================
# HARD GUARANTEES / NO-TOLERANCE RULES
# ============================================================
# 1) Skills: ONLY from the allowlist below (and their aliases), NOTHING else.
# 2) "AI" is ONLY allowed when it's the standalone token "AI" AND ONLY inside a skills section.
# 3) States: ONLY US state abbreviations + DC.
# 4) Salary: ONLY if it has a $ sign AND a period indicator (/yr, per year, annually, etc.).
# 5) Company/title extraction: refuse LinkedIn chrome noise; refuse "apply/reposted/..." garbage.
# 6) Compatible with scripts/run_batch.sh calling:
#       python -u python/enrich_job_postings.py --rescan-skills
# ============================================================

US_STATES_PLUS_DC = {
    "AL","AK","AZ","AR","CA","CO","CT","DE","FL","GA","HI","ID","IL","IN","IA","KS","KY","LA","ME","MD",
    "MA","MI","MN","MS","MO","MT","NE","NV","NH","NJ","NM","NY","NC","ND","OH","OK","OR","PA","RI","SC",
    "SD","TN","TX","UT","VT","VA","WA","WV","WI","WY","DC",
}

WORKPLACE_TYPES = {"onsite", "hybrid", "remote"}
EMPLOYMENT_TYPES = {"full-time", "part-time", "contract", "temporary", "internship"}

PRIORITY_RANK = {"required": 3, "preferred": 2, "nice-to-have": 1}

# ---- Skill section gates (only extract skills in these contexts) ----
SKILL_SECTION_HEADERS = {
    "skills",
    "technical skills",
    "required skills",
    "core skills",
    "qualifications",
    "required qualifications",
    "preferred qualifications",
    "requirements",
    "what you will have",
    "what you'll have",
    "what you bring",
    "what you'll bring",
    "what we are looking for",
    "what we're looking for",
    "tools",
    "tools & technologies",
    "tech stack",
    "technology",
    "stack",
}

NONSKILL_SECTION_HEADERS = {
    "responsibilities",
    "what you will do",
    "what you'll do",
    "job description",
    "about the job",
    "about the role",
    "about you",
    "about us",
    "benefits",
    "compensation",
    "pay range",
    "equal opportunity",
    "eeo",
    "privacy",
    "how to apply",
    "applicants",
    "why you should apply",
    "our values",
}

# ---- LinkedIn / UI junk lines we never want to parse as content ----
JUNK_LINE_PATTERNS = [
    r"\bwe leverage\b",
    r"\bpowered by ai\b",
    r"\bwith the help of ai\b",
    r"\bhelp of ai\b",
    r"\bget personalized tips\b",
    r"\bmatches your job preferences\b",
    r"\bover \d+ people clicked\b",
    r"\bresponses managed off linkedin\b",
    r"\btry premium\b",
    r"\btailor\b.*\bresume\b",
    r"\bshow match details\b",
    r"\beasy apply\b",
    r"\bbe an early applicant\b",
    r"\bbe among the first\b",
    r"\bclicked apply\b",
    r"\bapplicants?\b",
    r"\breposted\b",
    r"\bpromoted\b",
]

# ---- Company stopwords ----
_COMPANY_STOPWORDS = {
    "apply",
    "about the job",
    "reposted",
    "promoted",
    "responses managed",
    "full-time",
    "part-time",
    "contract",
    "temporary",
    "internship",
    "intern",
    "job alert",
    "show match",
    "tailor",
    "use ai",
    "saved",
    "save",
    "hybrid",
    "remote",
    "onsite",
    "on site",
    "in office",
    "easy apply",
    "see who you know",
    "how you match",
    "over 100 applicants",
    "applicants",
    "people clicked apply",
    "clicked apply",
    "be an early applicant",
    "be among the first",
    "share",
    "logo",
}

_BAD_COMPANY_TOKEN_RE = re.compile(
    r"\b(full\s*time|part\s*time|contract|temporary|internship|intern|remote|hybrid|on\s*site|onsite|in\s*office|reposted|applicants|apply|saved|share|promoted|clicked\s*apply|easy\s*apply)\b",
    flags=re.IGNORECASE,
)

_TITLE_KEYWORDS_RE = re.compile(
    r"(analyst|analytics|engineer|scientist|manager|director|specialist|strategist|consultant|administrator|lead|principal|staff)",
    flags=re.IGNORECASE,
)

# ============================================================
# SKILL ALLOWLIST (STRICT)
# - Only these can be output into job_skills.
# - You can expand aliases via skill_aliases table safely.
# - We also include a small internal synonym map as a fallback.
# ============================================================

# Canonical names (must match your skills.skill_name values; we normalize case/space).
ALLOWED_CANON_SKILLS = {
    # From your list (trimmed to non-garbage; no soft fluff)
    "SQL",
    "Excel",
    "Power BI",
    "Powerpoint",
    "Tableau",
    "Looker",
    "Google Sheets",
    "Google Slides",
    "Google Analytics",
    "GA4",
    "dbt",
    "Snowflake",
    "Redshift",
    "BigQuery",
    "Python",
    "R",
    "SAS",
    "Alteryx",
    "Amplitude",
    "Optimizely",
    "Domo",
    "Sigma",
    "Salesforce",
    "Shopify",
    "HubSpot",
    "Klaviyo",
    "Attentive",
    "Quicksight",
    "Matplotlib",
    "A/B Testing",
    "ETL Development",
    "Financial Modeling",
    "Logistic Regression",
    "Time Series Forecasting",
    "Multivariate Modeling",
    "ERP Systems",
    "SAP BW/HANA",
    "TM1",
    "MDX",
    "Perl",
    "Ruby",
    "MATLAB",
    "Stata",
    "Asana",
    "Airtable",
    "Lucidchart",
    "JSON",
    "DAX",
    # AI/ML: special rules apply for AI; ML allowed only as skill, but not via “we leverage” junk lines.
    "Machine Learning",
    "AI",
    # Modern data stack
    "Spark",
    "PySpark",
    "Airflow",
    "Kafka",
    "Terraform",
    "Docker",
    "Kubernetes",
    "Git",
    # Cloud platforms
    "AWS",
    "GCP",
    "Azure",
    "Azure Data Factory",
    "AWS Glue",
    # ML/AI tools
    "PyTorch",
    "TensorFlow",
    "Scikit-learn",
    "MLflow",
    "Hugging Face",
    "LangChain",
    "OpenAI",
    # Analytics tools
    "Hex",
    "Metabase",
    "Grafana",
    "Apache Superset",
    "Mode",
    # Languages
    "Scala",
    "Java",
    "Bash",
    # Data formats
    "Parquet",
    "Delta Lake",
    "Iceberg",
    # Stats/methods
    "Statistics",
    "Causal Inference",
    "Econometrics",
    "NLP",
    "Computer Vision",
    "Deep Learning",
    "Regression",
    "Classification",
    # BI/reporting
    "Power Automate",
    "SharePoint",
    "SSRS",
    "SSAS",
    "MicroStrategy",
    "Large Language Models",
    "Marketo",
    "Data Modeling",
    "Statistical Modeling",
    "Agentic Systems",
    "AWS Bedrock",
    # Finance credentials
    "CPA",
    "CFA",
    "CFP",
    "CMA",
}

# Canonical skills that are too generic/noisy for your goal — banned even if present in data.
BANNED_CANON_SKILLS = {
    "insights",
    "collaborate",
    "detail-oriented",
    "data analysis",
    "business analysis",
    "gap analysis",
    "reporting",
    "presentation",
    "marketing",
    "sales",
    "consulting",
    "querying",
    "forecasts",
    "forecasting",
    "competitor analysis",
    "post-mortem analysis",
    "concisely",
    "diplomacy",
    "ordinances",
    "revenuecloudfx",
    "ara premium",
    "commerceiq",
    "circana",
    "cube design",
    "turbointegrator scripting",
    "omni",
    "freewheel",
    "periscope",
    "exceedra",
    "amos",
    "pi",
    "modeling",
    "basic modeling",
    "case modeling",
    "quantitative analytics",
    "artificial intelligence/machine learning",
    "ai-assisted analysis",
    "and/or with data modeling",
    "utilize statistical modeling",
    "develop insightful dashboards",
    "we leverage machine learning",
    "vlookups",
    "bia’s",
    "node.js",
}

# Internal fallback aliases (still bounded to allowlist)
FALLBACK_ALIASES: Dict[str, List[str]] = {
    "SQL": ["sql", "postgresql", "postgres", "mssql", "sql server"],
    "Excel": ["excel", "vlookup", "vlookups", "xlookup", "powerpivot", "power pivot"],
    "Power BI": ["power bi", "powerbi", "dax"],
    "Google Analytics": ["google analytics"],
    "GA4": ["ga4", "ga 4"],
    "dbt": ["dbt"],
    "Snowflake": ["snowflake"],
    "Redshift": ["redshift"],
    "BigQuery": ["bigquery", "big query"],
    "Python": ["python"],
    "Matplotlib": ["matplotlib"],
    "JSON": ["json"],
    "DAX": ["dax"],
    "A/B Testing": ["a/b testing", "ab testing", "a/b test", "ab test"],
    "ETL Development": ["etl", "etl development", "elt", "elt development"],
    "Financial Modeling": ["financial modeling"],
    "Machine Learning": ["machine learning"],
    "AI": ["ai"],  # SPECIAL: standalone token + only in skills sections
    "Spark": ["spark", "apache spark", "pyspark"],
    "PySpark": ["pyspark", "py spark"],
    "Airflow": ["airflow", "apache airflow"],
    "Kafka": ["kafka", "apache kafka"],
    "Terraform": ["terraform"],
    "Docker": ["docker"],
    "Kubernetes": ["kubernetes", "k8s"],
    "Git": ["git", "github", "gitlab", "version control"],
    "AWS": ["aws", "amazon web services", "amazon aws"],
    "GCP": ["gcp", "google cloud", "google cloud platform"],
    "Azure": ["azure", "microsoft azure"],
    "Azure Data Factory": ["azure data factory", "adf"],
    "AWS Glue": ["aws glue", "glue"],
    "PyTorch": ["pytorch", "torch"],
    "TensorFlow": ["tensorflow", "tf"],
    "Scikit-learn": ["scikit-learn", "sklearn", "scikit learn"],
    "MLflow": ["mlflow", "ml flow"],
    "Hugging Face": ["hugging face", "huggingface"],
    "LangChain": ["langchain", "lang chain"],
    "OpenAI": ["openai", "open ai", "gpt", "chatgpt"],
    "Hex": ["hex"],
    "Metabase": ["metabase"],
    "Grafana": ["grafana"],
    "Mode": ["mode analytics", "mode"],
    "Scala": ["scala"],
    "Java": ["java"],
    "Bash": ["bash", "shell scripting", "shell script"],
    "Parquet": ["parquet", "apache parquet"],
    "Delta Lake": ["delta lake", "delta"],
    "Iceberg": ["iceberg", "apache iceberg"],
    "Statistics": ["statistics", "statistical analysis", "statistical modeling"],
    "Causal Inference": ["causal inference", "causal analysis"],
    "NLP": ["nlp", "natural language processing"],
    "Computer Vision": ["computer vision", "cv"],
    "Deep Learning": ["deep learning", "neural network", "neural networks"],
    "Regression": ["regression", "linear regression", "logistic regression"],
    "Power Automate": ["power automate", "ms power automate"],
    "MicroStrategy": ["microstrategy", "mstr"],
    "Large Language Models": ["large language models", "llm", "llms", "large language model"],
    "Marketo": ["marketo"],
    "Data Modeling": ["data modeling", "data modelling", "dimensional modeling", "dimensional modelling"],
    "Statistical Modeling": ["statistical modeling", "statistical modelling", "statistical models"],
    "Agentic Systems": ["agentic systems", "agentic ai", "ai agents", "autonomous agents"],
    "AWS Bedrock": ["aws bedrock", "bedrock", "amazon bedrock"],
}

def load_existing_skill_ids(cur) -> Dict[str, str]:
    """
    Return {canon_skill_name: skill_id} from the existing skills table.
    Canon key uses _canon_key() so 'Power BI' and 'power bi' normalize identically.
    """
    cur.execute("SELECT skill_id, skill_name FROM skills")
    out: Dict[str, str] = {}
    for r in cur.fetchall():
        canon = _canon_key(r["skill_name"])
        if canon:
            out[canon] = r["skill_id"]
    return out

# ============================================================
# TEXT NORMALIZATION
# ============================================================

def clean_text(s: str) -> str:
    s = (s or "").replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()

def normalize_for_matching(s: str) -> str:
    s = (s or "").lower().replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def _is_junk_line(line: str) -> bool:
    low = normalize_for_matching(line)
    return any(re.search(p, low) for p in JUNK_LINE_PATTERNS)

def _is_headerish(raw: str) -> bool:
    ln = (raw or "").strip()
    if not ln:
        return False
    # Strong header signal: mostly uppercase and not too short
    if re.fullmatch(r"[A-Z0-9 \-\/&(),.]{6,}", ln) and sum(c.isalpha() for c in ln) >= 4:
        return True
    return False

def strip_linkedin_chrome(desc: str) -> str:
    """
    Conservative removal of obvious LinkedIn UI chrome that poisons parsing.
    """
    lines = (desc or "").splitlines()
    out = []
    for raw in lines:
        ln = clean_text(raw)
        low = ln.lower()

        if not ln:
            out.append(raw)
            continue

        # Hard drop UI lines
        if low in {"share", "show more options", "apply", "save", "message", "beta"}:
            continue
        if "responses managed off linkedin" in low:
            continue
        if "try premium" in low:
            continue
        if "tailor my resume" in low or ("tailor" in low and "resume" in low):
            continue
        if "get personalized tips" in low:
            continue
        if "matches your job preferences" in low:
            continue
        if re.search(r"\bover\s+\d+\s+people clicked apply\b", low):
            continue
        if "show match details" in low:
            continue
        if "easy apply" in low:
            continue
        if re.search(r"\b(clicked apply|be an early applicant|be among the first)\b", low):
            continue

        out.append(raw)
    return "\n".join(out)

# ============================================================
# PARSERS: WORKPLACE, EMPLOYMENT, SALARY
# ============================================================

def parse_workplace_type(text: str) -> Optional[str]:
    t = normalize_for_matching(text)
    # Priority: hybrid > remote > onsite (hybrid usually most specific)
    if re.search(r"\bhybrid\b", t):
        return "hybrid"
    if re.search(r"\bremote\b", t):
        return "remote"
    if re.search(r"\bon[- ]?site\b|\bonsite\b|\bin office\b", t):
        return "onsite"
    return None

def parse_employment_type(text: str) -> Optional[str]:
    t = normalize_for_matching(text)
    if re.search(r"\bfull[- ]?time\b", t):
        return "full-time"
    if re.search(r"\bpart[- ]?time\b", t):
        return "part-time"
    if re.search(r"\bintern(ship)?\b", t):
        return "internship"
    if re.search(r"\bcontract(or)?\b", t):
        return "contract"
    if re.search(r"\btemporary\b|\btemp\b", t):
        return "temporary"
    return None

def _money_to_number(x: str) -> Optional[Decimal]:
    x = x.strip().replace(",", "").replace("$", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", x)
    if not m:
        return None
    val = Decimal(m.group(1))
    suffix = m.group(2).lower()
    if suffix == "k":
        val *= 1000
    elif suffix == "m":
        val *= 1000000
    return val

def _infer_period_from_context(text: str, start: int, end: int) -> Optional[str]:
    window = text[max(0, start - 80) : min(len(text), end + 80)].lower()
    if re.search(r"\b(per\s*year|/yr|yearly|annually|annual)\b", window):
        return "year"
    if re.search(r"\b(per\s*hour|/hr|hourly)\b", window):
        return "hour"
    if re.search(r"\b(per\s*month|/mo|monthly)\b", window):
        return "month"
    # USD alone is not a period signal — don't infer year from it here
    return None

def _to_dec(s: str) -> Optional[Decimal]:
    """Convert string like '140K', '140,000', '269,400.00', '122.250,00' to Decimal."""
    if s is None:
        return None
    s = s.strip().replace(" ", "")
    # Strip trailing punctuation
    s = s.rstrip(".,;:")
    # European thousands separator: 122.250,00 or 1.234.567
    # If dot appears before comma or before end with 3 digits after it, it's a thousands sep
    s = re.sub(r"\.(\d{3})(?=[,.]|$)", r"\1", s)
    # European decimal comma: 122250,00 -> 122250.00
    s = re.sub(r",(\d{2})$", r".\1", s)
    # Remove remaining commas (thousands separators)
    s = s.replace(",", "").replace("$", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([kKmM]?)", s)
    if not m:
        return None
    val = Decimal(m.group(1))
    suffix = m.group(2).lower()
    if suffix == "k":
        # if value already looks like thousands (>= 1000), K is a typo — don't scale
        if val >= 1000:
            pass  # 161000K -> 161000, not 161000000
        else:
            val *= 1000
    elif suffix == "m":
        val *= 1000000
    return val


def _scale_pair(s1: str, s2: str) -> tuple:
    """
    Parse two salary strings.
    If s2 has K suffix and s1 doesn't, scale s1 by K too.
    e.g. '$185 - $235K' -> (185000, 235000)
    """
    s1, s2 = s1.strip(), s2.strip()
    has_k2 = bool(re.search(r"[kK]$", s2))
    has_k1 = bool(re.search(r"[kK]$", s1))
    if has_k2 and not has_k1 and not re.search(r"\.", s1):
        # bare number like "185" with companion "235K" -> treat as 185K
        raw1 = re.sub(r"[,$]", "", s1)
        try:
            if Decimal(raw1) < 10000:  # looks like thousands (185, 140 etc)
                v1 = _to_dec(s1 + "K")
            else:
                v1 = _to_dec(s1)
        except Exception:
            v1 = _to_dec(s1)
    else:
        v1 = _to_dec(s1)
    v2 = _to_dec(s2)
    return v1, v2


def _period_from_context(text: str, lo: Optional[Decimal] = None) -> Optional[str]:
    """Infer period from text context and value magnitude.

    Priority order:
      1) Magnitude-based: if lo is clearly annual-sized (>= $15K), it's yearly.
         Long company descriptions often mention "monthly audience" etc. which
         would false-match. Magnitude is more reliable for big numbers.
      2) Explicit period keywords (per year, hourly, etc.)
    """
    # Magnitude-first check — most reliable for parseable values
    if lo is not None:
        if lo >= 15000:
            return "year"
        if Decimal("1000") <= lo < Decimal("15000"):
            return "month"
        if Decimal("7") <= lo < Decimal("1000"):
            return "hour"

    # Fall back to keyword detection for ambiguous values
    t = text.lower()
    if re.search(r"\b(per\s*year|/yr|yearly|annually|annual|per\s*annum|a\s+year)\b", t):
        return "year"
    if re.search(r"\b(per\s*hour|/hr|/hour|hourly|an\s+hour)\b", t):
        return "hour"
    if re.search(r"\b(per\s*month|/mo|monthly|a\s+month)\b", t):
        return "month"
    return None


def _sanity(lo: Optional[Decimal], hi: Optional[Decimal], period: Optional[str]) -> bool:
    """Validate a salary result."""
    if lo is None or hi is None or period is None:
        return False
    if lo > hi:
        lo, hi = hi, lo
    if period == "year":
        return Decimal("15000") <= lo and hi <= Decimal("1000000")
    if period == "hour":
        return Decimal("7") <= lo and hi <= Decimal("500")
    if period == "month":
        return Decimal("1000") <= lo and hi <= Decimal("50000")
    return False


# Shared dash pattern
_D = "(?:-|–|—|~|to)"
_M = r"\$?\s*([\d,\.]+[kKmM]?)"


def _try_slash_period(tline: str):
    """$81.6K/yr - $102K/yr  or  $40/hr - $50/hr"""
    m = re.search(
        r"\$?\s*([\d,\.]+[kKmM]?)\s*/\s*(yr|year|hr|hour|mo|month)\s*" + _D +
        r"\s*\$?\s*([\d,\.]+[kKmM]?)\s*/\s*(yr|year|hr|hour|mo|month)",
        tline, re.IGNORECASE)
    if m:
        p1, p2 = m.group(2)[0].lower(), m.group(4)[0].lower()
        if p1 == p2:
            pm = {"y": "year", "h": "hour", "m": "month"}[p1]
            v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(3))
            if v1 and v2:
                return min(v1, v2), max(v1, v2), pm
    return None


def _try_labeled_range(tline: str, window: str):
    """
    salary range $X-$Y, pay range $X-$Y, base salary $X-$Y,
    starting base salary of $X-$Y, compensation range $X-$Y, etc.
    """
    LABELS = (
        r"salary\s+range|pay\s+range|pay\s+band|pay\s+rate|"
        r"base\s+pay\s+range|compensation\s+range|"
        r"expected\s+salary\s+range|expected\s+salary|"
        r"base\s+salary\s+range\s+is|base\s+salary\s+range|base\s+salary|"
        r"starting\s+base\s+salary\s+of|salary\s+range\s+of|"
        r"ote|on[\-\s]target\s+earnings|target\s+compensation|"
        r"is\s+expected\s+to\s+be|for\s+full[\-\s]time|"
        r"the\s+pay\s+scale|compensation"
    )
    pat = re.compile(
        rf"({LABELS}).{{0,200}}?"
        rf"\$?\s*([\d,\.]+[kKmM]?)\s*(?:OTE|USD|CAD)?\s*{_D}\s*\$?\s*([\d,\.]+[kKmM]?)",
        re.IGNORECASE)

    for target in ([tline, window] if window != tline else [tline]):
        m = pat.search(target)
        if m:
            v1, v2 = _scale_pair(m.group(2), m.group(3))
            if v1 and v2:
                lo, hi = min(v1, v2), max(v1, v2)
                period = _period_from_context(target, lo)
                if period and _sanity(lo, hi, period):
                    return lo, hi, period
    return None


def _try_min_max_labels(tline: str):
    """
    Minimum: $X Maximum: $Y
    $X (minimum) ... $Y (maximum)
    """
    # keyword style
    m = re.search(
        r"minimum[:\s]+\$?\s*([\d,\.]+[kKmM]?).{0,100}?maximum[:\s]+\$?\s*([\d,\.]+[kKmM]?)",
        tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1, v2), max(v1, v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p):
                return lo, hi, p

    # $X (minimum) ... $Y (midpoint) ... $Z (maximum) -> use min and max
    m = re.search(
        r"\$\s*([\d,\.]+)\s*\(minimum\).{0,150}\$\s*([\d,\.]+)\s*\(maximum\)",
        tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1, v2), max(v1, v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p):
                return lo, hi, p
    return None


def _try_bare_range(tline: str):
    """
    Any $X - $Y pattern relying on magnitude for period.
    Handles: USD $X-$Y, CAD $X-$Y, $X &mdash; $Y, $ X-$Y (space after $)
    Uses finditer to try ALL matches — not just first — since description might
    have "$100 million in funding" before the actual salary range.
    """
    # USD prefix - iterate all matches
    for m in re.finditer(
        r"USD\s+\$?\s*([\d,\.]+[kKmM]?)\s*" + _D + r"\s*\$?\s*([\d,\.]+[kKmM]?)",
        tline, re.IGNORECASE):
        v1, v2 = _scale_pair(m.group(1), m.group(2))
        if v1 and v2:
            lo, hi = min(v1, v2), max(v1, v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p):
                return lo, hi, p

    # CAD prefix - iterate
    for m in re.finditer(
        r"CAD\s+\$?\s*([\d,\.]+[kKmM]?)\s*" + _D + r"\s*(?:CAD\s+)?\$?\s*([\d,\.]+[kKmM]?)",
        tline, re.IGNORECASE):
        v1, v2 = _scale_pair(m.group(1), m.group(2))
        if v1 and v2:
            lo, hi = min(v1, v2), max(v1, v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p):
                return lo, hi, p

    # Standard $X - $Y - iterate all matches in the line
    for m in re.finditer(
        r"\$\s*([\d,\.]+[kKmM]?)\s*" + _D + r"\s*\$?\s*([\d,\.]+[kKmM]?)",
        tline, re.IGNORECASE):
        v1, v2 = _scale_pair(m.group(1), m.group(2))
        if v1 and v2:
            lo, hi = min(v1, v2), max(v1, v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p):
                return lo, hi, p

    # $X to $Y (without dash) - iterate
    for m in re.finditer(
        r"\$\s*([\d,\.]+[kKmM]?)\s+to\s+\$?\s*([\d,\.]+[kKmM]?)",
        tline, re.IGNORECASE):
        v1, v2 = _scale_pair(m.group(1), m.group(2))
        if v1 and v2:
            lo, hi = min(v1, v2), max(v1, v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p):
                return lo, hi, p

    return None


def _try_min_max_pay_range(tline: str):
    """Ryder style: Minimum Pay Range : $X Maximum Pay Range : $Y"""
    import re
    m = re.search(
        r"minimum\s+(?:pay\s+)?range\s*[:\s]+\$?\s*([\d,\.]+)\s+"
        r"maximum\s+(?:pay\s+)?range\s*[:\s]+\$?\s*([\d,\.]+)",
        tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p): return lo, hi, p
    return None


def _try_targeted_pay_range(tline: str):
    """Dickssportinggoods: Targeted Pay Range: $X - $Y"""
    import re
    m = re.search(
        r"targeted\s+pay\s+range[:\s]+\$?\s*([\d,\.]+)\s*[-–—]\s*\$?\s*([\d,\.]+)",
        tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p): return lo, hi, p
    return None


try:
    from llm_client import extract_salary_llm, classify_role
    _LLM_AVAILABLE = True
except ImportError:
    _LLM_AVAILABLE = False
    def extract_salary_llm(*args, **kwargs):
        return None
    def classify_role(*args, **kwargs):
        return None

_LLM_CAP = int(os.environ["ENRICH_MAX_LLM_CALLS"]) if os.environ.get("ENRICH_MAX_LLM_CALLS") else None
_llm_state = {"calls": 0, "cap_warned": False}


def _llm_allowed() -> bool:
    if _LLM_CAP is None:
        return True
    if _llm_state["calls"] >= _LLM_CAP:
        if not _llm_state["cap_warned"]:
            log.warning(f"ENRICH_MAX_LLM_CALLS={_LLM_CAP} reached — skipping LLM for remaining jobs")
            _llm_state["cap_warned"] = True
        return False
    return True


_SALARY_KW = re.compile(
    r"(salary|compensation|pay range|base pay|pay rate|hourly rate|"
    r"total comp|annual pay|wage|remuneration)",
    re.IGNORECASE,
)


def _salary_llm_snippet(text: str) -> Optional[str]:
    """Return the salary-relevant text window for LLM extraction, or None if no keyword."""
    m = _SALARY_KW.search(text or "")
    if not m:
        return None
    start = max(0, m.start() - 200)
    end = min(len(text), m.end() + 400)
    return text[start:end]


def _try_llm_fallback(text: str):
    """
    Last-resort: send a snippet to Haiku for salary extraction.
    Only triggered after all regex parsers fail.
    """
    if not _LLM_AVAILABLE or not _llm_allowed():
        return None

    snippet = _salary_llm_snippet(text)
    if snippet is None:
        return None

    _llm_state["calls"] += 1
    result = extract_salary_llm(snippet)
    if result:
        lo, hi, period = result
        if _sanity(lo, hi, period):
            return lo, hi, period
    return None


def _try_annual_salary_range(tline: str):
    """Dentsuaegis: The annual salary range for this position is $X - $Y"""
    import re
    m = re.search(
        r"annual\s+(?:base\s+)?(?:salary|pay)\s+range\s+(?:for\s+this\s+(?:position|role)\s+)?is\s+\$?\s*([\d,\.]+[kK]?)\s*[-–—]\s*\$?\s*([\d,\.]+[kK]?)",
        tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            if _sanity(lo, hi, "year"): return lo, hi, "year"
    return None


def _try_spaced_number_range(tline: str):
    """Fix numbers with internal spaces: $1 65,656 or $125 , 0 00 or $ 111 ,000"""
    import re
    # Normalize spaces within numbers then re-parse
    normalized = re.sub(r'\$\s*(\d[\d\s,]*\d)', lambda m: '$' + re.sub(r'[\s]', '', m.group(1)), tline)
    # Also normalize standalone spaced numbers after dash: - 125 , 0 00
    normalized = re.sub(r'([-–—])\s*(\d[\d\s,]*\d)', lambda m: m.group(1) + re.sub(r'[\s]', '', m.group(2)), normalized)
    if normalized != tline:
        result = _try_labeled_range(normalized, normalized) or _try_bare_range(normalized)
        if result and _sanity(result[0], result[1], result[2]):
            return result
    return None


def _try_salary_slash_annually(tline: str):
    """Relx: Salary: $72,500/annually"""
    import re
    m = re.search(r"\$\s*([\d,\.]+)\s*/\s*annual", tline, re.IGNORECASE)
    if m:
        v = _to_dec(m.group(1))
        if v and _sanity(v, v, "year"): return v, v, "year"
    return None


def _try_salary_plus(tline: str):
    """Utaustin: Salary Range $70,000 + depending"""
    import re
    m = re.search(r"salary\s+range\s+\$\s*([\d,\.]+)\s*\+", tline, re.IGNORECASE)
    if m:
        v = _to_dec(m.group(1))
        if v and _sanity(v, v, "year"): return v, v, "year"
    return None


def _try_point72_typo(tline: str):
    """Point72: $130,000-$1450,000 — second value has extra digit, likely $145,000"""
    import re
    from decimal import Decimal
    m = re.search(r"\$\s*([\d,]+)\s*-\s*\$\s*(\d{4,}),000", tline, re.IGNORECASE)
    if m:
        v1 = _to_dec(m.group(1))
        # $1450,000 -> strip leading extra digit if result > 1M
        raw2 = m.group(2)
        v2 = _to_dec(raw2 + "000")
        if v2 and v2 > Decimal("1000000"):
            # try removing first digit
            v2 = _to_dec(raw2[1:] + "000") if len(raw2) > 3 else v2
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            if _sanity(lo, hi, "year"): return lo, hi, "year"
    return None


def _try_ford_salary_grades(tline: str):
    """Ford: Salary grade 6 and ranges from $72,480-121,440"""
    import re
    m = re.search(r"ranges\s+from\s+\$([\d,]+)-([\d,]+)", tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            if _sanity(lo, hi, "year"): return lo, hi, "year"
    return None


def _try_twilio_colon_range(tline: str):
    """Twilio: Washington D.C. : $188,240 - 235,300"""
    import re
    m = re.search(r":\s+\$([\d,\.]+)\s*[-–—]\s*([\d,\.]+)(?:\.|\s|$)", tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            p = _period_from_context(tline, lo)
            if _sanity(lo, hi, p): return lo, hi, p
    return None


def _try_iqvia_french(tline: str):
    """Iqvia french: est de $93,200.00 - $143,200.00"""
    import re
    m = re.search(r"est\s+de\s+\$([\d,\.]+)\s*[-–—]\s*\$?([\d,\.]+)", tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            if _sanity(lo, hi, "year"): return lo, hi, "year"
    return None


def _try_low_high_range(tline: str):
    """PebblePost: Salary range: Low: $170,000 - High: $190,000"""
    import re
    m = re.search(r"low[:\s]+\$?([\d,\.]+)\s*[-–—]\s*high[:\s]+\$?([\d,\.]+)", tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            if _sanity(lo, hi, "year"): return lo, hi, "year"
    return None


def _try_cad_between(tline: str):
    """Fullscript: between $110,000 CAD and $140,000 CAD"""
    import re
    m = re.search(r"between\s+\$?([\d,\.]+)\s*CAD\s+and\s+\$?([\d,\.]+)\s*CAD", tline, re.IGNORECASE)
    if m:
        v1, v2 = _to_dec(m.group(1)), _to_dec(m.group(2))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            if _sanity(lo, hi, "year"): return lo, hi, "year"
    return None


def _try_kobold_truncated(tline: str):
    """KoBold: $140,00 - $240,000 — first value missing last digit"""
    import re
    from decimal import Decimal
    m = re.search(r"\$\s*(\d+),(\d{2})\s*[-–—]\s*\$?([\d,]+)", tline)
    if m and len(m.group(2)) == 2:
        # $140,00 -> try as $140,000
        v1 = _to_dec(m.group(1) + "000")
        v2 = _to_dec(m.group(3))
        if v1 and v2:
            lo, hi = min(v1,v2), max(v1,v2)
            if _sanity(lo, hi, "year"): return lo, hi, "year"
    return None


def _try_single_value(tline: str, low: str):
    """Single salary values with explicit context."""
    # offering $X+ or offering $X
    m = re.search(r"offering\s+\$\s*([\d,\.]+[kKmM]?)\+?", tline, re.IGNORECASE)
    if m:
        v = _to_dec(m.group(1))
        if v and v >= 50000:
            return v, v, "year"

    # starting from/at $X
    m = re.search(r"(?:starting\s+from|starting\s+at)\s+\$\s*([\d,\.]+[kKmM]?)", tline, re.IGNORECASE)
    if m:
        v = _to_dec(m.group(1))
        if v and v >= 15000:
            return v, v, "year"

    # minimum base salary starts at $X
    m = re.search(r"minimum\s+base\s+salary\s+starts\s+at\s+\$\s*([\d,\.]+[kKmM]?)", tline, re.IGNORECASE)
    if m:
        v = _to_dec(m.group(1))
        if v and v >= 15000:
            return v, v, "year"

    # Single labeled: Compensation: $X or Salary: $X (no range indicator)
    m = re.search(
        r"(?:compensation|salary)[:\s]+\$\s*([\d,\.]+[kKmM]?)(?:\s*(?:USD|per\s+year|annually|/yr))?\s*$",
        tline, re.IGNORECASE)
    if m and not re.search(r"range|band|package", tline, re.IGNORECASE):
        v = _to_dec(m.group(1))
        if v and v >= 50000:
            return v, v, "year"

    # $X/hour single
    m = re.search(r"\$\s*([\d,\.]+)\s*/\s*hou", tline, re.IGNORECASE)
    if m:
        v = _to_dec(m.group(1))
        if v and Decimal("7") <= v <= Decimal("500"):
            return v, v, "hour"

    # "$X/hr" single
    m = re.search(r"\$\s*([\d,\.]+)\s*/\s*hr\b", tline, re.IGNORECASE)
    if m:
        v = _to_dec(m.group(1))
        if v and Decimal("7") <= v <= Decimal("500"):
            return v, v, "hour"

    return None


def parse_salary_range(text: str, *, skip_llm: bool = False) -> tuple:
    """
    Clean architecture salary parser.
    Tries extractors in priority order per line, returns first valid result.
    """
    raw = strip_linkedin_chrome(text or "")
    if not raw.strip():
        return None, None, None

    # Normalize HTML entities and unicode
    raw = (raw
           .replace("&mdash;", "-").replace("&ndash;", "-")
           .replace("&#8212;", "-").replace("&#8211;", "-")
           .replace("\u2013", "-").replace("\u2014", "-"))

    lines = [clean_text(x) for x in raw.splitlines() if clean_text(x)]
    if not lines:
        return None, None, None

    for idx, tline in enumerate(lines[:220]):
        if "$" not in tline:
            continue
        low = tline.lower()
        if "try premium" in low:
            continue
        if re.search(r"\bfor\s*\$0\b", low):
            continue

        # Build window for multi-line patterns
        window = " ".join(lines[max(0, idx - 1):idx + 3])

        result = (
            _try_slash_period(tline) or
            _try_labeled_range(tline, window) or
            _try_min_max_labels(tline) or
            _try_min_max_pay_range(tline) or
            _try_targeted_pay_range(tline) or
            _try_annual_salary_range(tline) or
            _try_spaced_number_range(tline) or
            _try_salary_slash_annually(tline) or
            _try_salary_plus(tline) or
            _try_point72_typo(tline) or
            _try_ford_salary_grades(tline) or
            _try_twilio_colon_range(tline) or
            _try_iqvia_french(tline) or
            _try_low_high_range(tline) or
            _try_cad_between(tline) or
            _try_kobold_truncated(tline) or
            _try_bare_range(tline) or
            _try_single_value(tline, low)
        )

        if result and _sanity(result[0], result[1], result[2]):
            lo, hi = min(result[0], result[1]), max(result[0], result[1])
            return lo, hi, result[2]

    # All regex patterns exhausted — try LLM fallback on the full text
    if not skip_llm:
        llm_result = _try_llm_fallback(raw)
        if llm_result:
            return llm_result

    return None, None, None

_LOC_LINE_RE = re.compile(r"\b([A-Za-z .'-]+),\s*([A-Z]{2})\b")

def normalize_company_candidate(s: str) -> str:
    s = (s or "").lower().replace("\u00a0", " ")
    # Normalize ALL dash variants → space
    s = re.sub(r"[\u2010\u2011\u2012\u2013\u2014\u2015\-]", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()

def is_bad_company_name(name: str) -> bool:
    if not name:
        return True
    norm = normalize_company_candidate(name)
    if not norm:
        return True
    if _BAD_COMPANY_TOKEN_RE.search(norm):
        return True
    if norm in _COMPANY_STOPWORDS:
        return True
    # “City, ST” is location, not company
    if _LOC_LINE_RE.search(name):
        return True
    return False

def score_company_candidate(raw_line: str) -> int:
    """Higher score = more likely a real company name."""
    s = clean_text(raw_line)
    s = re.sub(r"\s+logo$", "", s, flags=re.IGNORECASE).strip()
    if not s:
        return -999

    if is_bad_company_name(s):
        return -100

    if len(s) < 2 or len(s) > 80:
        return -40

    # hard rejects: pure symbols/digits
    if re.fullmatch(r"[\d\W_]+", s):
        return -80

    score = 0
    words = [w for w in re.split(r"\s+", s) if w]
    wc = len(words)

    # plausible word counts
    if 1 <= wc <= 7:
        score += 10
    else:
        score -= 10

    # titlecase-ish signal
    titlecase_words = sum(1 for w in words if w[:1].isupper())
    if titlecase_words >= max(1, wc // 2):
        score += 6

    # penalize separators typical of UI lines
    if any(ch in s for ch in [":", ";", "/"]):
        score -= 10
    if "·" in s:
        score -= 6

    # penalize obvious “role” words (often a title, not company)
    if _TITLE_KEYWORDS_RE.search(s):
        score -= 8

    return score

def _split_linkedin_line(line: str) -> str:
    s = (line or "").strip()
    s = re.sub(r"\s+logo$", "", s, flags=re.IGNORECASE).strip()
    for sep in [" · ", " • ", " | "]:
        if sep in s:
            s = s.split(sep, 1)[0].strip()
            break
    return s

def _looks_like_title(line: str) -> bool:
    low = normalize_for_matching(line)
    if not line or len(line) < 4 or len(line) > 120:
        return False
    if _is_junk_line(line):
        return False
    if low in _COMPANY_STOPWORDS:
        return False
    if re.fullmatch(r"[\d\W_]+", line):
        return False
    if _TITLE_KEYWORDS_RE.search(line):
        # reject “how you match” etc
        if any(bad in low for bad in ["job alert", "reposted", "clicked apply", "promoted", "matches your job preferences"]):
            return False
        return True
    return False

def extract_title_company_location_from_description(desc: str) -> Dict[str, Optional[str]]:
    """
    Returns: {title, company, location, state}
    """
    cleaned = strip_linkedin_chrome(desc or "")
    lines = [clean_text(x) for x in cleaned.splitlines()]
    lines = [x for x in lines if x]
    top = lines[:40]

    # location/state from top lines
    location = None
    state = None
    for line in top:
        m = _LOC_LINE_RE.search(line)
        if m:
            st = (m.group(2) or "").strip().upper()
            if st in US_STATES_PLUS_DC:
                location = m.group(1).strip()
                state = st
                break

    # title: first strong-looking title line
    title = None
    for line in top:
        if _looks_like_title(line):
            title = line.strip(" -•")
            break

    # company: best scored candidate among top lines
    best_company = None
    best_score = -999
    for raw_line in top[:18]:
        line = _split_linkedin_line(raw_line)
        if not line:
            continue
        if title and line == title:
            continue
        if _looks_like_title(line):
            continue
        if _LOC_LINE_RE.search(line):
            continue
        sc = score_company_candidate(line)
        if sc > best_score:
            best_score = sc
            best_company = line.strip(" -•")

    # threshold
    company = best_company if (best_company and best_score >= 10) else None

    return {"title": title, "company": company, "location": location, "state": state}

# ============================================================
# EXPERIENCE LEVEL (CONTROLLED, NOT GUESSY)
# ============================================================

def _extract_years_experience_requirements(desc: str) -> Optional[int]:
    lines = [clean_text(x) for x in (desc or "").splitlines() if clean_text(x)]
    dash = r"(?:-|–|—)"
    candidates: List[int] = []

    for line in lines[:400]:
        l = normalize_for_matching(line)
        if "year" not in l or "experience" not in l:
            continue

        m_plus = re.search(r"\b(\d+)\s*\+\s*years?\b", l)
        if m_plus:
            candidates.append(int(m_plus.group(1)))
            continue

        m_range = re.search(rf"\b(\d+)\s*(?:{dash}|to)\s*(\d+)\s*years?\b", l)
        if m_range:
            candidates.append(int(m_range.group(1)))  # conservative: lower bound
            continue

        m_atleast = re.search(r"\b(at\s*least|min(?:imum)?)\s*(\d+)\s*years?\b", l)
        if m_atleast:
            candidates.append(int(m_atleast.group(2)))
            continue

        m_plain = re.search(r"\b(\d+)\s*years?\b", l)
        if m_plain:
            candidates.append(int(m_plain.group(1)))
            continue

    return min(candidates) if candidates else None


def infer_experience_level(
    desc: str,
    title_hint: Optional[str] = None,
    salary_max: Optional[float] = None,
) -> Optional[str]:
    """
    Title-dominant experience level inference.
    Order of precedence (title regex wins over everything):
      1. intern/co-op → entry
      2. junior/jr/entry/associate → entry
      3. senior/sr/staff/principal/lead/distinguished/fellow → senior
      4. manager/director/vp/head of/chief/CxO → senior
      5. mid/mid-level/II/III in title → mid
      6. Salary tiebreaker for ambiguous titles
      7. Default → mid (Entry requires explicit signal)

    Returns one of: entry, mid, senior
    """
    title_lower = (title_hint or '').lower()

    # === TIER 1: Intern / co-op (checked before senior to handle "Senior Intern" edge cases) ===
    if re.search(r'\b(intern|internship|co-op|coop)\b', title_lower):
        return 'entry'

    # === TIER 2: Junior / explicit entry ===
    if re.search(r'\b(junior|jr\.?|entry[\s-]?level|associate|new\s+grad|new\s+graduate|graduate\s+program|apprentice|trainee|early\s+career)\b', title_lower):
        return 'entry'

    # === TIER 3: Senior IC markers ===
    if re.search(r'\b(senior|sr\.?|staff|principal|lead|distinguished|fellow)\b', title_lower):
        return 'senior'

    # === TIER 4: Management / executive markers ===
    if re.search(r'\b(manager|director|vp|vice\s+president|head\s+of|chief|c[a-z]o)\b', title_lower):
        return 'senior'

    # === TIER 5: Explicit mid signals in title ===
    if re.search(r'\b(mid[\s-]?level|mid\b|\bii\b|\biii\b|\biv\b|\blevel\s*[23]\b)', title_lower):
        return 'mid'

    # === TIER 6: Salary tiebreaker for ambiguous titles ===
    if salary_max is not None:
        if salary_max > 200_000:
            return 'senior'
        if salary_max < 80_000:
            return 'entry'

    # === TIER 7: YOE extraction from description ===
    yrs = _extract_years_experience_requirements(desc or '')
    if yrs is not None:
        if yrs >= 7:
            return 'senior'
        if yrs >= 3:
            return 'mid'
        return 'entry'

    # === TIER 8: Default mid — Entry requires an explicit signal ===
    return 'mid'


def _canon_key(s: str) -> str:
    # normalize canonical key for map lookups
    return re.sub(r"\s+", " ", (s or "").strip())

def _canon_norm(s: str) -> str:
    return normalize_for_matching(_canon_key(s))

def _is_bulletish(raw: str) -> bool:
    r = (raw or "").strip()
    return bool(re.match(r"^(\-|\*|•|\d+[\.\)])\s+", r))

def _header_match(low: str, headers: set) -> bool:
    if not low:
        return False
    low = low.rstrip(":").strip()
    return any(low == h or low.startswith(h + ":") for h in headers)

def extract_skill_section_lines(desc: str) -> List[str]:
    """
    Return ONLY lines that are inside a skills/requirements/tools section.
    Very strict gating to reduce prose false positives.
    """
    raw = strip_linkedin_chrome(desc or "")
    lines_raw = raw.splitlines()
    lines = [clean_text(x) for x in lines_raw]

    out: List[str] = []
    in_block = False

    for raw_line, ln in zip(lines_raw, lines):
        low = normalize_for_matching(ln)

        if not ln:
            continue

        # enter skills block
        if _header_match(low, SKILL_SECTION_HEADERS):
            in_block = True
            continue

        # exit skills block
        if in_block:
            if _header_match(low, NONSKILL_SECTION_HEADERS):
                in_block = False
                continue
            if _is_headerish(raw_line):
                in_block = False
                continue
            out.append(ln)

    return out

def load_skill_aliases_from_db(cur) -> Dict[str, List[str]]:
    """
    Optional: use your skill_aliases table if it exists and is populated.
    Expected columns: skill_name (canonical) + alias (variant)
    If table doesn't exist, we fall back to FALLBACK_ALIASES only.
    """
    aliases: Dict[str, List[str]] = {k: list(v) for k, v in FALLBACK_ALIASES.items()}

    # Load all skill aliases from DB — domain filtering happens downstream
    # in build_skill_patterns_for_domain(), not here.
    try:
        cur.execute("""
            SELECT s.skill_name, sa.alias_text as alias
            FROM skill_aliases sa
            JOIN skills s ON s.skill_id = sa.skill_id
            WHERE sa.alias_text IS NOT NULL
        """)
        rows = cur.fetchall()
        for r in rows:
            canon = _canon_key(r["skill_name"])
            alias = clean_text(r["alias"])
            if not canon or not alias:
                continue
            aliases.setdefault(canon, [])
            # de-dupe alias list (case-insensitive)
            if _canon_norm(alias) not in {_canon_norm(a) for a in aliases[canon]}:
                aliases[canon].append(alias)
    except Exception:
        # IMPORTANT: any SQL error aborts the transaction until rollback
        cur.connection.rollback()
        return aliases
        # table missing or schema mismatch; ignore safely
        pass

    # ensure every skill loaded from DB has at least itself as an alias
    for canon in list(aliases.keys()):
        if _canon_norm(canon) not in {_canon_norm(a) for a in aliases[canon]}:
            aliases[canon].append(canon)

    return aliases

# Skills we do NOT want to extract even if they exist in skills table (noise / soft skills).
# You can shrink/expand this list over time.
SKILL_DENYLIST = {
    "insights",
    "collaborate",
    "reporting",
    "data analysis",
    "business analysis",
    "modeling",
    "presentation",
    "consulting",
    "marketing",
    "sales",
    "detail-oriented",
}

def build_skill_patterns_anywhere(canon_to_skill_id: Dict[str, str],
                                  skill_aliases: Dict[str, List[str]]) -> List[Tuple[re.Pattern, str, str]]:
    """
    Build regex patterns for:
      - every existing skill in skills table (canon_to_skill_id keys)
      - plus any aliases from DB + FALLBACK_ALIASES (merged already in load_skill_aliases_from_db)
    Returns [(compiled_pattern, canon, alias_used)].
    """
    patterns: List[Tuple[re.Pattern, str, str]] = []

    def _mk_pat(term: str) -> re.Pattern:
        # Escape, but allow flexible whitespace between words
        esc = re.escape(term.strip())
        esc = esc.replace(r"\ ", r"\s+")
        # default word boundary match
        return re.compile(rf"\b{esc}\b", flags=re.IGNORECASE)

    existing_canons = set(canon_to_skill_id.keys())

    # 1) Canon names themselves
    for canon in sorted(existing_canons):
        if _canon_norm(canon) in SKILL_DENYLIST:
            continue
        patterns.append((_mk_pat(canon), canon, canon))

    # 2) Aliases
    for canon, aliases in (skill_aliases or {}).items():
        canon_k = _canon_key(canon)
        if not canon_k:
            continue
        if canon_k not in existing_canons:
            continue
        if _canon_norm(canon_k) in SKILL_DENYLIST:
            continue

        for a in aliases:
            a = clean_text(a)
            if not a:
                continue

            # AI special handling: require standalone "AI"
            if canon_k == "AI":
                if a.strip().lower() != "ai":
                    continue
                pat = re.compile(r"\bAI\b")
            else:
                pat = _mk_pat(a)

            patterns.append((pat, canon_k, a))

    # de-dupe by (pattern text, canon)
    dedup = {}
    for p, canon, a in patterns:
        key = (p.pattern.lower(), canon)
        if key not in dedup:
            dedup[key] = (p, canon, a)

    return list(dedup.values())


# === DOMAIN_SKILL_FILTER_v1 ===

def _relevant_skill_names_for_domain(domain: str) -> set:
    """
    Returns canonical skill names from vertical_taxonomy relevant to this domain.

    Two sources:
      1. Skills defined directly in VERTICALS[domain]['skills']
      2. Skills in other verticals whose 'also_in' list includes this domain

    Computed from the taxonomy at call time — no DB query needed.
    """
    relevant: set = set()
    if domain in VERTICALS:
        relevant.update(VERTICALS[domain]["skills"].keys())
    for vkey, vdata in VERTICALS.items():
        if vkey == domain:
            continue
        for skill_name, meta in vdata["skills"].items():
            if domain in meta.get("also_in", []):
                relevant.add(skill_name)
    return relevant


def build_skill_patterns_for_domain(
    domain: Optional[str],
    canon_to_skill_id: Dict[str, str],
    skill_aliases: Dict[str, List[str]],
) -> List[Tuple[re.Pattern, str, str]]:
    """
    Domain-filtered variant of build_skill_patterns_anywhere().

    If domain is None: returns empty list — skip skill extraction for
    unclassified jobs. They are re-enriched after classify_domain assigns domain.

    Otherwise: filters to skills relevant to this domain (direct + also_in),
    then delegates to build_skill_patterns_anywhere() for regex compilation.
    All existing SKILL_DENYLIST, AI special-case, and dedup logic is preserved.
    """
    if domain is None:
        return []
    relevant = _relevant_skill_names_for_domain(domain)
    filtered_ids = {k: v for k, v in canon_to_skill_id.items() if k in relevant}
    filtered_aliases = {k: v for k, v in skill_aliases.items() if k in relevant}
    return build_skill_patterns_anywhere(filtered_ids, filtered_aliases)

# === END_DOMAIN_SKILL_FILTER_v1 ===


def infer_priority_from_context(line: str, current_section: Optional[str]) -> str:
    """
    Priority rules:
      - If a line contains explicit 'preferred' / 'nice-to-have' markers, obey.
      - Else inherit from current section.
      - Else default required (conservative).
    """
    low = normalize_for_matching(line)

    if re.search(r"\b(nice to have|nice-to-have|bonus|additional)\b", low):
        return "nice-to-have"
    if re.search(r"\b(preferred)\b", low):
        return "preferred"
    if re.search(r"\b(required|must have|requirements)\b", low):
        return "required"

    if current_section in {"required", "preferred", "nice-to-have"}:
        return current_section

    return "required"

def infer_section_priority_header(line: str) -> Optional[str]:
    low = normalize_for_matching(line).rstrip(":")
    if "preferred" in low:
        return "preferred"
    if "nice to have" in low or "nice-to-have" in low or "bonus" in low:
        return "nice-to-have"
    if "required" in low or "requirements" in low or "qualifications" in low:
        return "required"
    return None

def extract_skills_allowlist(desc: str, patterns: List[Tuple[re.Pattern, str, str]]) -> Dict[str, str]:
    """
    BROADENED extraction:
      - Still returns {canonical_skill: priority}
      - Pass 1: section-aware priority detection (required/preferred/nice-to-have)
      - Pass 2: anywhere scan fallback (required)
      - Only extracts skills that exist in skills table + aliases (because patterns are built from those)
      - Denylist prevents resurrecting noise like insights/collaborate/reporting/etc.
      - AI is treated conservatively to avoid "powered by AI" style false positives.
    """
    cleaned = strip_linkedin_chrome(desc or "")
    lines_raw = cleaned.splitlines()

    out: Dict[str, str] = {}

    # -----------------------
    # PASS 1 — Section-aware
    # -----------------------
    section_lines = extract_skill_section_lines(cleaned)
    section_norm = {_canon_norm(x) for x in section_lines if x}

    current_section: Optional[str] = None

    for raw in lines_raw:
        ln = clean_text(raw)
        if not ln:
            continue
        if _is_junk_line(ln):
            continue

        new_sec = infer_section_priority_header(ln)
        if new_sec:
            current_section = new_sec
            continue

        in_skill_section = _canon_norm(ln) in section_norm
        if not in_skill_section:
            continue

        for pat, canon, _alias in patterns:
            if _canon_norm(canon) in SKILL_DENYLIST:
                continue

            if canon == "AI":
                # only allow AI inside skill sections as standalone token
                if not re.search(r"\bAI\b", ln):
                    continue

            if pat.search(ln):
                pr = current_section or "required"
                if canon not in out or PRIORITY_RANK[pr] > PRIORITY_RANK[out[canon]]:
                    out[canon] = pr

    # -----------------------
    # PASS 2 — Anywhere scan
    # -----------------------
    full_text = cleaned

    for pat, canon, _alias in patterns:
        if canon in out:
            continue
        if _canon_norm(canon) in SKILL_DENYLIST:
            continue

        if canon == "AI":
            # Conservative: allow AI only if it appears in a bullet-ish line anywhere
            # (prevents massive AI spam from marketing prose)
            ai_ok = False
            for raw in lines_raw:
                if _is_bulletish(raw) and re.search(r"\bAI\b", raw):
                    ai_ok = True
                    break
            if not ai_ok:
                continue

        if pat.search(full_text):
            out[canon] = "required"

    return out

def infer_company_type(company_name: Optional[str], desc: str) -> Optional[str]:
    """
    Extremely conservative:
      - only sets company_type if a strong signal exists in text.
    """
    t = normalize_for_matching(desc or "")

    if re.search(r"\b(agency|staffing|recruiting|talent solutions|search firm)\b", t):
        return "staffing"
    if re.search(r"\b(consulting|advisory|professional services)\b", t):
        return "consulting"
    if re.search(r"\b(nonprofit|non-profit|501\(c\))\b", t):
        return "nonprofit"
    if re.search(r"\b(university|college|school district|higher education)\b", t):
        return "education"
    if re.search(r"\b(hospital|health system|clinic|healthcare)\b", t):
        return "healthcare"
    if re.search(r"\b(federal|state of|city of|county|municipal|government)\b", t):
        return "government"

    # public/private are hard without explicit text; only set if it’s literally stated
    if re.search(r"\b(publicly traded|listed on|nyse|nasdaq)\b", t):
        return "public"
    if re.search(r"\b(privately held|private company)\b", t):
        return "private"

    return None

def infer_role_archetype(title: Optional[str], desc: str) -> Optional[str]:
    """
    Strict mapping from title keywords. Returns None if uncertain.
    """
    tt = normalize_for_matching(title or "")
    t = tt  # don't lean on description unless you later want it

    if re.search(r"\b(analytics engineer)\b", t):
        return "analytics_engineer"
    if re.search(r"\b(data engineer)\b", t):
        return "data_engineer"
    if re.search(r"\b(data scientist)\b", t):
        return "data_scientist"
    if re.search(r"\b(product analyst|product analytics)\b", t):
        return "product_analyst"
    if re.search(r"\b(marketing analyst|marketing analytics)\b", t):
        return "marketing_analytics"
    if re.search(r"\b(financial analyst|finance analyst|fp&a)\b", t):
        return "finance_analytics"
    if re.search(r"\b(revenue operations|revops)\b", t):
        return "revops"
    if re.search(r"\b(business intelligence|bi analyst|bi developer)\b", t):
        return "bi"
    if re.search(r"\b(analyst)\b", t):
        return "analyst"
    if re.search(r"\b(consultant)\b", t):
        return "consultant"

    return None


# ============================================================
# DB CONNECTION + UPSERTS
# ============================================================

@dataclass
class ParsedJob:
    company: Optional[str] = None
    title: Optional[str] = None
    location: Optional[str] = None
    state: Optional[str] = None
    workplace_type: Optional[str] = None
    employment_type: Optional[str] = None
    experience_level: Optional[str] = None
    salary_min: Optional[Decimal] = None
    salary_max: Optional[Decimal] = None
    salary_period: Optional[str] = None
    company_type: Optional[str] = None
    role_archetype: Optional[str] = None


def get_conn():
    return psycopg2.connect(
        host=os.getenv("PGHOST", "localhost"),
        port=int(os.getenv("PGPORT", "5432")),
        dbname=os.getenv("PGDATABASE", "job_analytics"),
        user=os.getenv("PGUSER"),
        password=os.getenv("PGPASSWORD"),
    )

def upsert_company(cur, company_name: str, company_type: Optional[str] = None) -> Optional[str]:
    company_name = clean_text(company_name)
    if not company_name:
        return None
    company_id = _md5_id("C", company_name)

    # only write company_type if it's valid
    ct = (company_type or "").strip().lower() or None
    if ct and 'COMPANY_TYPES' in globals() and ct not in COMPANY_TYPES:
        ct = None

    if ct:
        cur.execute(
            """
            INSERT INTO companies (company_id, company_name, company_type)
            VALUES (%s, %s, %s)
            ON CONFLICT (company_id) DO UPDATE
              SET company_name = EXCLUDED.company_name,
                  company_type = COALESCE(companies.company_type, EXCLUDED.company_type)
            """,
            (company_id, company_name, ct),
        )
    else:
        cur.execute(
            """
            INSERT INTO companies (company_id, company_name)
            VALUES (%s, %s)
            ON CONFLICT (company_id) DO UPDATE
              SET company_name = EXCLUDED.company_name
            """,
            (company_id, company_name),
        )

    return company_id

def upsert_role(cur, role_name: str, role_archetype: Optional[str] = None) -> Optional[str]:
    role_name = clean_text(role_name)
    if not role_name:
        return None
    role_id = _md5_id("R", role_name)

    ra = (role_archetype or "").strip().lower() or None
    if ra and 'ROLE_ARCHETYPES' in globals() and ra not in ROLE_ARCHETYPES:
        ra = None

    if ra:
        cur.execute(
            """
            INSERT INTO roles (role_id, role_name, role_archetype)
            VALUES (%s, %s, %s)
            ON CONFLICT (role_id) DO UPDATE
              SET role_name = EXCLUDED.role_name,
                  role_archetype = COALESCE(roles.role_archetype, EXCLUDED.role_archetype)
            """,
            (role_id, role_name, ra),
        )
    else:
        cur.execute(
            """
            INSERT INTO roles (role_id, role_name)
            VALUES (%s, %s)
            ON CONFLICT (role_id) DO UPDATE
              SET role_name = EXCLUDED.role_name
            """,
            (role_id, role_name),
        )
    return role_id

def upsert_location(cur, location: str, state: Optional[str]) -> Optional[str]:
    location = clean_text(location)
    st = (state or "").strip().upper() or None
    if st and st not in US_STATES_PLUS_DC:
        st = None
    if not location and not st:
        return None
    location_id = _md5_id("L", f"{location}|{st or ''}")

    cur.execute(
        """
        INSERT INTO locations (location_id, location, state)
        VALUES (%s, %s, %s)
        ON CONFLICT (location_id) DO UPDATE
          SET location = EXCLUDED.location,
              state = EXCLUDED.state
        """,
        (location_id, location or None, st),
    )
    return location_id

def ensure_skill_ids(cur) -> Dict[str, str]:
    """
    Ensure each canonical allowlist skill exists in skills table.
    Returns: {canon_skill_name: skill_id}
    """
    canon_to_id: Dict[str, str] = {}

    cur.execute("SELECT skill_id, skill_name FROM skills")
    for r in cur.fetchall():
        name = _canon_key(r["skill_name"] or "")
        if name:
            canon_to_id[name] = r["skill_id"]

    for canon in sorted(ALLOWED_CANON_SKILLS):
        if canon in canon_to_id:
            continue
        sid = _md5_id("S", canon)
        cur.execute(
            """
            INSERT INTO skills (skill_id, skill_name)
            VALUES (%s, %s)
            ON CONFLICT (skill_id) DO UPDATE SET skill_name = EXCLUDED.skill_name
            """,
            (sid, canon),
        )
        canon_to_id[canon] = sid

    return canon_to_id

def job_has_skill(cur, job_id: str, skill_id: str) -> bool:
    cur.execute("SELECT 1 FROM job_skills WHERE job_id=%s AND skill_id=%s LIMIT 1", (job_id, skill_id))
    return cur.fetchone() is not None

def _md5_id(prefix: str, s: str) -> str:
    # Stable 10-hex suffix, matches your SQL style: 'S' || substring(md5(...) for 10)
    h = hashlib.md5(s.encode("utf-8")).hexdigest()[:10]
    return f"{prefix}{h}"

def insert_job_skills(cur, job_id: str,
                      canon_to_priority: Dict[str, str],
                      canon_to_skill_id: Dict[str, str]) -> int:

    inserted = 0

    for canon, pr in canon_to_priority.items():
        sid = canon_to_skill_id.get(canon)
        if not sid:
            continue

        job_skills_id = _md5_id("JS", f"{job_id}|{sid}")

        cur.execute(
            """
            INSERT INTO job_skills
            (job_skills_id, job_id, skill_id, skill_priority, confidence, extraction_src)
            VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT (job_skills_id) DO UPDATE
            SET skill_priority = EXCLUDED.skill_priority
            """,
            (job_skills_id, job_id, sid, pr, 0.9, "two_pass")
        )
        inserted += 1

    return inserted
def parse_dimensions(desc: str, *, skip_salary_llm: bool = False) -> ParsedJob:
    dims = extract_title_company_location_from_description(desc or "")

    pj = ParsedJob()
    pj.company = dims.get("company")
    pj.title = dims.get("title")
    pj.location = dims.get("location")
    pj.state = dims.get("state")

    pj.workplace_type = parse_workplace_type(desc or "")
    pj.employment_type = parse_employment_type(desc or "")

    smin, smax, sper = parse_salary_range(desc or "", skip_llm=skip_salary_llm)
    pj.salary_min, pj.salary_max, pj.salary_period = smin, smax, sper

    pj.experience_level = infer_experience_level(desc or "", pj.title, salary_max=pj.salary_max)

    # optional strict enrichments
    pj.company_type = infer_company_type(pj.company, desc or "")
    pj.role_archetype = infer_role_archetype(pj.title, desc or "")

    return pj


# ============================================================
# ASYNC LLM HELPERS
# ============================================================

def _load_cat_cache(cur) -> dict:
    """Pre-load role_category_cache into memory: {(company_lower, role_lower): category}."""
    try:
        cur.execute("""
            SELECT company_lower, role_lower, role_category
            FROM role_category_cache
            WHERE cached_at > NOW() - INTERVAL '30 days'
        """)
        return {(r[0], r[1]): r[2] for r in cur.fetchall()}
    except Exception as e:
        log.warning(f"Failed to load role_category_cache: {e}")
        return {}


async def _one_classify(job_id, role_name, desc, domain, company, client, sem):
    from llm_client import classify_role_async
    result = await classify_role_async(role_name, desc, domain, company, client, sem)
    return job_id, result


async def _one_salary(job_id, snippet, client, sem):
    from llm_client import extract_salary_llm_async
    result = await extract_salary_llm_async(snippet, client, sem)
    return job_id, result


class _AsyncRateLimiter:
    """Token bucket: space out calls so we never exceed rpm requests/minute."""

    def __init__(self, rpm: int):
        self._delay = 60.0 / rpm   # seconds between token grants
        self._lock = asyncio.Lock()
        self._next_token: float = 0.0

    async def acquire(self):
        async with self._lock:
            now = asyncio.get_event_loop().time()
            wait = max(0.0, self._next_token - now)
            self._next_token = max(now, self._next_token) + self._delay
        if wait > 0:
            await asyncio.sleep(wait)


async def _one_classify(job_id, role_name, desc, domain, company, client, sem, rl):
    await rl.acquire()
    async with sem:
        from llm_client import classify_role_async
        result = await classify_role_async(role_name, desc, domain, company, client, sem)
    return job_id, result


async def _one_salary(job_id, snippet, client, sem, rl):
    await rl.acquire()
    async with sem:
        from llm_client import extract_salary_llm_async
        result = await extract_salary_llm_async(snippet, client, sem)
    return job_id, result


async def _run_llm_concurrent(classify_jobs, salary_jobs, semaphore_size=50, rpm=45):
    """Run classify_role + salary LLM calls concurrently, rate-limited to rpm/min."""
    from anthropic import AsyncAnthropic
    api_key = os.getenv("ANTHROPIC_API_KEY")
    sem = asyncio.Semaphore(semaphore_size)
    rl = _AsyncRateLimiter(rpm=rpm)

    async with AsyncAnthropic(api_key=api_key, max_retries=2) as client:
        classify_coros = [
            _one_classify(job_id, rn, desc, dom, co, client, sem, rl)
            for job_id, rn, desc, dom, co in classify_jobs
        ]
        salary_coros = [
            _one_salary(job_id, snippet, client, sem, rl)
            for job_id, snippet in salary_jobs
        ]
        classify_raw = await asyncio.gather(*classify_coros, return_exceptions=True)
        salary_raw = await asyncio.gather(*salary_coros, return_exceptions=True)

    classify_results = {
        job_id: (r.get("category") if isinstance(r, dict) and r else None)
        for job_id, r in (
            item if not isinstance(item, BaseException) else (None, None)
            for item in classify_raw
        )
        if job_id is not None
    }
    salary_results = {
        job_id: r
        for job_id, r in (
            item if not isinstance(item, BaseException) else (None, None)
            for item in salary_raw
        )
        if job_id is not None
    }
    return classify_results, salary_results


def _run_llm_batch(classify_jobs, salary_jobs):
    """Submit classify + salary jobs as Anthropic Message Batch API requests.

    Applies Phase-2 pre-checks before batching so fast-path verdicts are returned
    immediately without consuming batch quota.

    Polls every 5 minutes until all batches end, then streams results.

    Returns the same (classify_results, salary_results) dict format as
    _run_llm_concurrent.
    """
    import time as _time
    from anthropic import Anthropic
    from llm_client import (
        _data_title_subcategory, _is_federal_staffing, is_blocked_aggregator,
        _federal_staffing_verdict, _data_title_verdict, _build_domain_prompt,
        build_salary_prompt, _parse_classify_json, _parse_salary_json,
        _is_breaker_tripped,
    )
    from llm_client import MODEL as LLM_MODEL

    classify_results = {}
    salary_results = {}

    if _is_breaker_tripped():
        return classify_results, salary_results

    api_key = os.getenv("ANTHROPIC_API_KEY")
    client = Anthropic(api_key=api_key)

    batch_requests = []
    id_map = {}  # custom_id -> ("classify"|"salary", job_id)

    # Classify: apply pre-checks, queue residual
    # custom_ids must match ^[a-zA-Z0-9_-]{1,64}$ — use short index-based ids
    precheck_hits = 0
    classify_idx = 0
    for job_id, role_name, desc, domain, company in classify_jobs:
        if is_blocked_aggregator(company):
            precheck_hits += 1
            continue
        if domain == "data_ml":
            if _is_federal_staffing(company):
                v = _federal_staffing_verdict()
                classify_results[job_id] = v.get("category")
                precheck_hits += 1
                continue
            subcat = _data_title_subcategory(role_name)
            if subcat:
                v = _data_title_verdict(subcat, role_name)
                classify_results[job_id] = v.get("category")
                precheck_hits += 1
                continue
        snippet = (desc or "")[:600]
        prompt = _build_domain_prompt(domain, role_name, snippet)
        cid = f"c{classify_idx:06d}"
        classify_idx += 1
        batch_requests.append({
            "custom_id": cid,
            "params": {
                "model": LLM_MODEL,
                "max_tokens": 200,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
        })
        id_map[cid] = ("classify", job_id)

    # Salary: no additional pre-checks needed (already gated in Phase 1)
    salary_idx = 0
    for job_id, snippet in salary_jobs:
        prompt = build_salary_prompt(snippet)
        cid = f"s{salary_idx:06d}"
        salary_idx += 1
        batch_requests.append({
            "custom_id": cid,
            "params": {
                "model": LLM_MODEL,
                "max_tokens": 200,
                "temperature": 0,
                "messages": [{"role": "user", "content": prompt}],
            },
        })
        id_map[cid] = ("salary", job_id)

    if precheck_hits:
        print(f"  Batch pre-checks: {precheck_hits} fast-path verdicts, {len(batch_requests)} batch requests remaining")

    if not batch_requests:
        return classify_results, salary_results

    # Submit in chunks of 9 000 (API limit is 10 000; leave headroom)
    BATCH_LIMIT = 9_000
    batch_ids = []
    n_batches = (len(batch_requests) + BATCH_LIMIT - 1) // BATCH_LIMIT
    for i in range(0, len(batch_requests), BATCH_LIMIT):
        chunk = batch_requests[i : i + BATCH_LIMIT]
        batch = client.beta.messages.batches.create(requests=chunk)
        batch_ids.append(batch.id)
        print(f"  Submitted batch {len(batch_ids)}/{n_batches}: {batch.id} ({len(chunk):,} requests)")

    # Poll until all batches end
    POLL_INTERVAL = 300  # 5 minutes
    while True:
        statuses = [client.beta.messages.batches.retrieve(bid) for bid in batch_ids]
        pending = [b for b in statuses if b.processing_status != "ended"]
        if not pending:
            print(f"  [{_time.strftime('%H:%M')}] All batches complete.")
            break
        parts = [
            f"{b.id[-8:]} {b.request_counts.succeeded}ok/{b.request_counts.processing}pending"
            for b in pending
        ]
        print(f"  [{_time.strftime('%H:%M')}] Waiting — {' | '.join(parts)}")
        _time.sleep(POLL_INTERVAL)

    # Stream results
    ok = err = 0
    for bid in batch_ids:
        for result in client.beta.messages.batches.results(bid):
            cid = result.custom_id
            info = id_map.get(cid)
            if not info:
                continue
            kind, job_id = info
            if result.result.type != "succeeded":
                err += 1
                continue
            text = result.result.message.content[0].text.strip()
            if kind == "classify":
                parsed = _parse_classify_json(text)
                if parsed:
                    classify_results[job_id] = parsed.get("category")
                    ok += 1
                else:
                    err += 1
            else:
                parsed = _parse_salary_json(text)
                if parsed:
                    salary_results[job_id] = parsed
                    ok += 1
                else:
                    err += 1

    print(f"  Batch results: {ok} ok, {err} failed/null")
    return classify_results, salary_results


# ============================================================
# MAIN ENRICH LOOP
# ============================================================

COMMIT_INTERVAL = 500  # intermediate commit every N jobs written


def enrich_jobs(limit: int, apply: bool, only_missing: bool, rescan_skills: bool, rescan_salary: bool = False, use_batch: bool = False) -> None:
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)

    # Load existing skills + aliases (full set — domain filtering happens per-job)
    canon_to_skill_id = load_existing_skill_ids(cur)
    skill_aliases = load_skill_aliases_from_db(cur)

    # Pre-load role_category cache into memory (eliminates per-job DB round-trips)
    cat_cache = _load_cat_cache(cur) if only_missing else {}

    # ── Select jobs ────────────────────────────────────────────────────────────
    if rescan_salary:
        cur.execute(
            """
            SELECT jp.job_id, jp.description_text,
                   jp.company_id, jp.role_id, jp.location_id,
                   jp.workplace_type, jp.employment_type, jp.experience_level,
                   jp.salary_min, jp.salary_max, jp.salary_period,
                   jp.domain,
                   COALESCE(jp.data_tier, 1) AS data_tier,
                   EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id) AS has_skills
            FROM job_postings jp
            WHERE jp.description_text IS NOT NULL
              AND length(jp.description_text) > 0
              AND jp.data_tier = 1
              AND jp.status = 'raw'
              AND jp.salary_max IS NULL
              AND jp.description_text LIKE '%%$%%'
              AND (
                  lower(jp.description_text) LIKE '%%salary%%'
                  OR lower(jp.description_text) LIKE '%%compensation%%'
                  OR lower(jp.description_text) LIKE '%%pay range%%'
                  OR lower(jp.description_text) LIKE '%%base pay%%'
                  OR lower(jp.description_text) LIKE '%%pay rate%%'
              )
            ORDER BY jp.ingested_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        jobs = cur.fetchall()
    elif only_missing:
        cur.execute(
            """
            SELECT jp.job_id, jp.description_text,
                   jp.company_id, jp.role_id, jp.location_id,
                   jp.workplace_type, jp.employment_type, jp.experience_level,
                   jp.salary_min, jp.salary_max, jp.salary_period,
                   jp.domain,
                   COALESCE(jp.data_tier, 1) AS data_tier,
                   jp.role_category,
                   r.role_name,
                   c.company_name,
                   EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id) AS has_skills
            FROM job_postings jp
            LEFT JOIN roles r ON r.role_id = jp.role_id
            LEFT JOIN companies c ON c.company_id = jp.company_id
            WHERE jp.description_text IS NOT NULL AND length(jp.description_text) > 0
              AND jp.status = 'raw'
              AND (
                -- Tier 1: full NLP enrichment needed (including unclassified)
                (COALESCE(jp.data_tier,1) = 1 AND (
                   jp.company_id IS NULL OR jp.role_id IS NULL OR jp.location_id IS NULL
                OR jp.workplace_type IS NULL OR jp.employment_type IS NULL OR jp.experience_level IS NULL
                OR jp.salary_min IS NULL OR jp.salary_max IS NULL OR jp.salary_period IS NULL
                OR jp.role_category IS NULL
                OR NOT EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id)
                ))
                OR
                -- Tier 2: only experience_level inference needed
                (COALESCE(jp.data_tier,1) = 2 AND jp.experience_level IS NULL)
              )
            ORDER BY jp.ingested_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        jobs = cur.fetchall()
    else:
        cur.execute(
            """
            SELECT jp.job_id, jp.description_text,
                   jp.company_id, jp.role_id, jp.location_id,
                   jp.workplace_type, jp.employment_type, jp.experience_level,
                   jp.salary_min, jp.salary_max, jp.salary_period,
                   jp.domain,
                   EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id) AS has_skills
            FROM job_postings jp
            WHERE jp.description_text IS NOT NULL AND length(jp.description_text) > 0
            ORDER BY jp.ingested_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        jobs = cur.fetchall()

    print(f"Fetched {len(jobs):,} jobs. cat_cache={len(cat_cache):,} entries.")

    # ── Pre-pass: classify NULL-domain jobs before Phase 1 skill gate ─────────
    # Jobs that arrived with domain=NULL skip skill extraction and LLM role_category.
    # Classify them here so Phase 1 sees a real domain value.
    if only_missing:
        null_domain_jobs = [j for j in jobs if not j.get("domain")]
        if null_domain_jobs:
            pre_alias_map = _build_domain_alias_map(conn)
            pre_classified: list[tuple] = []  # (job_id, domain, secondary)
            for job in null_domain_jobs:
                title = (job["role_name"] if "role_name" in job.keys() else None) or ""
                desc = job["description_text"] or ""
                domain, secondary, _ = _classify_domain_fn(title, desc, pre_alias_map, use_llm=False)
                if domain:
                    pre_classified.append((job["job_id"], domain, secondary or None))

            print(f"Pre-pass: {len(null_domain_jobs):,} NULL-domain → {len(pre_classified):,} newly classified")

            if apply and pre_classified:
                execute_batch(
                    cur,
                    """
                    UPDATE job_postings
                    SET domain = %s, domain_secondary = %s, domain_classified_at = now()
                    WHERE job_id = %s
                    """,
                    [(d, s, jid) for jid, d, s in pre_classified],
                    page_size=500,
                )
                conn.commit()
                # Update in-memory rows so Phase 1's LLM and skill gates see the new domain
                classified_lookup = {jid: d for jid, d, _ in pre_classified}
                jobs = [
                    {**dict(job), "domain": classified_lookup[job["job_id"]]}
                    if job["job_id"] in classified_lookup else job
                    for job in jobs
                ]

    # ── Phase 1: sync parse — no LLM calls ────────────────────────────────────
    # Collect pending LLM work; accumulate per-job parsed dims.
    t1 = time.time()
    sync_results = {}  # job_id -> dict with pj, scalar_fields/params, metadata
    pending_classify = []   # [(job_id, role_name, desc, domain, company)]
    pending_salary_llm = [] # [(job_id, snippet)]

    for job in jobs:
        job_id = job["job_id"]
        desc = job["description_text"] or ""
        job_domain = job["domain"] if "domain" in job.keys() else None

        # Parse all dims without LLM (salary LLM deferred to Phase 2)
        pj = parse_dimensions(desc, skip_salary_llm=True)

        # Scalar field updates (workplace, employment, experience, salary from regex)
        scalar_fields, scalar_params = [], []
        if pj.workplace_type and pj.workplace_type != job["workplace_type"]:
            scalar_fields.append("workplace_type=%s"); scalar_params.append(pj.workplace_type)
        if pj.employment_type and pj.employment_type != job["employment_type"]:
            scalar_fields.append("employment_type=%s"); scalar_params.append(pj.employment_type)
        if pj.experience_level and pj.experience_level != job["experience_level"]:
            scalar_fields.append("experience_level=%s"); scalar_params.append(pj.experience_level)
        if pj.salary_min is not None and job["salary_min"] is None:
            scalar_fields.append("salary_min=%s"); scalar_params.append(pj.salary_min)
        if pj.salary_max is not None and job["salary_max"] is None:
            scalar_fields.append("salary_max=%s"); scalar_params.append(pj.salary_max)
        if pj.salary_min is not None and pj.salary_period == "year" and job["salary_min"] is None:
            if float(pj.salary_min) <= 1000000:
                scalar_fields.append("salary_min_annual=%s"); scalar_params.append(pj.salary_min)
        if pj.salary_max is not None and pj.salary_period == "year" and job["salary_max"] is None:
            if float(pj.salary_max) <= 1000000:
                scalar_fields.append("salary_max_annual=%s"); scalar_params.append(pj.salary_max)
        if pj.salary_period is not None and job["salary_period"] is None:
            scalar_fields.append("salary_period=%s"); scalar_params.append(pj.salary_period)

        # Salary LLM: needed when regex found nothing and job has no salary yet
        salary_snippet = None
        if pj.salary_min is None and job["salary_min"] is None:
            raw_stripped = strip_linkedin_chrome(desc)
            salary_snippet = _salary_llm_snippet(raw_stripped)
            if salary_snippet:
                pending_salary_llm.append((job_id, salary_snippet))

        # Role category: check memory cache first, queue LLM if miss
        cat_from_cache = None
        needs_classify = False
        role_name_for_cls = ""
        company_for_cls = None
        company_lower = ""
        role_lower = ""

        if _LLM_AVAILABLE and only_missing and job_domain is not None:
            existing_cat = job["role_category"] if "role_category" in job.keys() else None
            if existing_cat is None:
                role_name_for_cls = (job["role_name"] if "role_name" in job.keys() else None) or pj.title or ""
                if role_name_for_cls:
                    company_for_cls = job["company_name"] if "company_name" in job.keys() else None
                    company_lower = (company_for_cls or "").strip().lower()
                    role_lower = role_name_for_cls.strip().lower()
                    ck = (company_lower, role_lower)
                    cat_from_cache = cat_cache.get(ck)
                    if not cat_from_cache:
                        needs_classify = True
                        pending_classify.append((job_id, role_name_for_cls, desc, job_domain, company_for_cls))

        needs_skill_scan = (rescan_skills or not job["has_skills"]) and len(desc) > 500

        sync_results[job_id] = {
            "pj": pj,
            "scalar_fields": scalar_fields,
            "scalar_params": scalar_params,
            "job_domain": job_domain,
            "cat_from_cache": cat_from_cache,
            "company_lower": company_lower,
            "role_lower": role_lower,
            "needs_skill_scan": needs_skill_scan,
        }

    print(f"Phase 1: {time.time()-t1:.1f}s — {len(pending_classify)} classify LLM, {len(pending_salary_llm)} salary LLM queued")

    # ── Phase 2: LLM calls — batch or concurrent ──────────────────────────────
    llm_classify_results = {}  # job_id -> category str or None
    llm_salary_results = {}    # job_id -> (lo, hi, period) or None

    if _LLM_AVAILABLE and (pending_classify or pending_salary_llm):
        budget = (_LLM_CAP - _llm_state["calls"]) if _LLM_CAP is not None else (len(pending_classify) + len(pending_salary_llm))
        classify_to_run = pending_classify[:max(0, budget)]
        salary_budget = max(0, budget - len(classify_to_run))
        salary_to_run = pending_salary_llm[:salary_budget]

        if classify_to_run or salary_to_run:
            n_llm = len(classify_to_run) + len(salary_to_run)
            t2 = time.time()
            if use_batch:
                print(f"Phase 2 (batch): {len(classify_to_run)} classify + {len(salary_to_run)} salary — submitting to Anthropic Batch API ...")
                llm_classify_results, llm_salary_results = _run_llm_batch(classify_to_run, salary_to_run)
            else:
                print(f"Phase 2: {len(classify_to_run)} classify + {len(salary_to_run)} salary — {n_llm} LLM calls, semaphore=50 ...")
                llm_classify_results, llm_salary_results = asyncio.run(
                    _run_llm_concurrent(classify_to_run, salary_to_run, semaphore_size=50)
                )
            elapsed2 = time.time() - t2
            got_cat = sum(1 for v in llm_classify_results.values() if v)
            got_sal = sum(1 for v in llm_salary_results.values() if v)
            print(f"Phase 2: {elapsed2:.1f}s — {got_cat}/{len(classify_to_run)} classified, {got_sal}/{len(salary_to_run)} salary")
            _llm_state["calls"] += n_llm

        if budget < len(pending_classify) + len(pending_salary_llm):
            log.warning(f"ENRICH_MAX_LLM_CALLS={_LLM_CAP} reached after {budget} calls — remaining jobs get no LLM classification")

    # ── Phase 3: write to DB, commit every COMMIT_INTERVAL jobs ───────────────
    t3 = time.time()
    touched = 0
    updates = 0
    total_skills = 0
    pending_skill_rows = []  # batched for execute_batch at each commit point

    for i, job in enumerate(jobs):
        job_id = job["job_id"]
        desc = job["description_text"] or ""
        sr = sync_results[job_id]
        pj = sr["pj"]
        job_domain = sr["job_domain"]

        fields, params = [], []

        # Company / role / location upserts
        if pj.company and job["company_id"] is None:
            cid = upsert_company(cur, pj.company, pj.company_type)
            if cid:
                fields.append("company_id=%s"); params.append(cid)
        if pj.title and job["role_id"] is None:
            rid = upsert_role(cur, pj.title, pj.role_archetype)
            if rid:
                fields.append("role_id=%s"); params.append(rid)
        if (pj.location or pj.state) and job["location_id"] is None:
            lid = upsert_location(cur, pj.location or "", pj.state)
            if lid:
                fields.append("location_id=%s"); params.append(lid)

        # Scalar dims from Phase 1
        fields.extend(sr["scalar_fields"])
        params.extend(sr["scalar_params"])

        # === DOMAIN_AWARE_ENRICH_v1 ===
        # Role category — cache hit (Phase 1) or LLM result (Phase 2)
        if sr["cat_from_cache"]:
            fields.extend(["role_category=%s", "role_classified_at=NOW()"])
            params.append(sr["cat_from_cache"])
        elif job_id in llm_classify_results and llm_classify_results[job_id]:
            cat_val = llm_classify_results[job_id]
            fields.extend(["role_category=%s", "role_classified_at=NOW()"])
            params.append(cat_val)
            if apply and sr["company_lower"] and sr["role_lower"]:
                try:
                    cur.execute("""
                        INSERT INTO role_category_cache (company_lower, role_lower, role_category, cached_at)
                        VALUES (%s, %s, %s, NOW())
                        ON CONFLICT (company_lower, role_lower)
                        DO UPDATE SET role_category=EXCLUDED.role_category, cached_at=NOW()
                    """, (sr["company_lower"], sr["role_lower"], cat_val))
                except Exception as _e:
                    log.warning(f"role_category_cache write failed: {_e}")

        # Salary from LLM (Phase 2) — only fills what regex missed
        if job_id in llm_salary_results and llm_salary_results[job_id]:
            lo, hi, period = llm_salary_results[job_id]
            if pj.salary_min is None and job["salary_min"] is None:
                fields.append("salary_min=%s"); params.append(lo)
            if pj.salary_max is None and job["salary_max"] is None:
                fields.append("salary_max=%s"); params.append(hi)
            if pj.salary_period is None and job["salary_period"] is None:
                fields.append("salary_period=%s"); params.append(period)
            if period == "year" and job["salary_min"] is None and float(lo) <= 1000000:
                fields.append("salary_min_annual=%s"); params.append(lo)
            if period == "year" and job["salary_max"] is None and float(hi) <= 1000000:
                fields.append("salary_max_annual=%s"); params.append(hi)

        # Skills — collect rows for batch insert; NULL domain skips entirely
        ins = 0
        if sr["needs_skill_scan"]:
            patterns = build_skill_patterns_for_domain(job_domain, canon_to_skill_id, skill_aliases)
            if patterns:
                canon_to_priority = extract_skills_allowlist(desc, patterns)
                for canon, pr in canon_to_priority.items():
                    sid = canon_to_skill_id.get(canon)
                    if sid:
                        jsid = _md5_id("JS", f"{job_id}|{sid}")
                        pending_skill_rows.append((jsid, job_id, sid, pr, 0.9, "two_pass"))
                        ins += 1
        # === END_DOMAIN_AWARE_ENRICH_v1 ===

        if fields or ins:
            touched += 1
            updates += (1 if fields else 0)
            total_skills += ins

        if apply and fields:
            params.append(job_id)
            cur.execute(f"UPDATE job_postings SET {', '.join(fields)} WHERE job_id=%s", tuple(params))

        # Intermediate commit every COMMIT_INTERVAL: flush skills + commit
        if apply and (i + 1) % COMMIT_INTERVAL == 0:
            if pending_skill_rows:
                execute_batch(
                    cur,
                    """INSERT INTO job_skills (job_skills_id, job_id, skill_id, skill_priority, confidence, extraction_src)
                       VALUES (%s,%s,%s,%s,%s,%s)
                       ON CONFLICT (job_skills_id) DO UPDATE SET skill_priority=EXCLUDED.skill_priority""",
                    pending_skill_rows, page_size=500,
                )
                pending_skill_rows = []
            conn.commit()
            log.info(f"  checkpoint {i+1}/{len(jobs)} ({time.time()-t3:.0f}s) — {touched} touched so far")

    print(f"Phase 3: {time.time()-t3:.1f}s — {touched} touched, {updates} updates, {total_skills} skills")

    if apply:
        if pending_skill_rows:
            execute_batch(
                cur,
                """INSERT INTO job_skills (job_skills_id, job_id, skill_id, skill_priority, confidence, extraction_src)
                   VALUES (%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (job_skills_id) DO UPDATE SET skill_priority=EXCLUDED.skill_priority""",
                pending_skill_rows, page_size=500,
            )
        conn.commit()
        print("✅ Applied (COMMIT).")
    else:
        conn.rollback()
        print("Dry-run only (ROLLBACK). Re-run with --apply to write.")

    cur.close()
    conn.close()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--only-missing", action="store_true", help="Only jobs missing dims or skills")
    ap.add_argument("--rescan-skills", action="store_true", help="Re-scan skills even if job already has skills")
    ap.add_argument("--rescan-salary", action="store_true", help="Re-scan salary for jobs with no salary but salary text in description")
    ap.add_argument("--batch", action="store_true", help="Use Anthropic Batch API for LLM calls (async, cheaper, for large backfills)")
    args = ap.parse_args()

    enrich_jobs(limit=args.limit, apply=args.apply, only_missing=args.only_missing, rescan_skills=args.rescan_skills, rescan_salary=args.rescan_salary, use_batch=args.batch)


if __name__ == "__main__":
    main()
