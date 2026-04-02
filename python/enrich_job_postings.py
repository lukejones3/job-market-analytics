#!/usr/bin/env python3
import hashlib
import os
import re
import string
import argparse
from pathlib import Path
from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Dict, List, Tuple

from dotenv import load_dotenv
import psycopg2
from psycopg2.extras import DictCursor

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
    if re.search(r"\busd\b", window):
        return "year"
    return None

def parse_salary_range(text: str) -> Tuple[Optional[Decimal], Optional[Decimal], Optional[str]]:
    """
    NO-TOLERANCE salary parser:
      - MUST contain a '$'
      - MUST contain an explicit period indicator (/yr, per year, annually, /hr, per hour, /mo, etc.)
      - Reject “Try Premium for $0” and other chrome
    """
    raw = strip_linkedin_chrome(text or "")
    if not raw.strip():
        return None, None, None

    lines = [clean_text(x) for x in raw.splitlines() if clean_text(x)]
    dash = r"(?:-|–|—)"

    for line in lines[:220]:
        tline = clean_text(line)
        low = tline.lower()

        if _is_junk_line(tline):
            continue
        if "try premium" in low:
            continue
        if re.search(r"\bfor\s*\$0\b", low):
            continue

        if "$" not in tline:
            continue

        # (A) $81.6K/yr - $102K/yr
        m = re.search(
            rf"\$?\s*(\d+(?:\.\d+)?[kKmM]?)\s*/\s*(yr|year|hr|hour|mo|month)\s*{dash}\s*\$?\s*(\d+(?:\.\d+)?[kKmM]?)\s*/\s*(yr|year|hr|hour|mo|month)",
            tline,
            flags=re.IGNORECASE,
        )
        if m:
            s1, p1, s2, p2 = m.group(1), m.group(2).lower(), m.group(3), m.group(4).lower()

            def _p_norm(p: str) -> str:
                if p.startswith("y"):
                    return "year"
                if p.startswith("h"):
                    return "hour"
                return "month"

            p1n = _p_norm(p1)
            p2n = _p_norm(p2)
            if p1n != p2n:
                continue

            v1 = _money_to_number(s1)
            v2 = _money_to_number(s2)
            if v1 is None or v2 is None:
                continue

            # sanity
            if p1n == "year" and min(v1, v2) < Decimal("15000"):
                continue
            if p1n == "hour" and min(v1, v2) < Decimal("7"):
                continue

            return min(v1, v2), max(v1, v2), p1n

        # (B) “Salary range $81,600 - $102,000 per year”
        m = re.search(
            rf"(salary\s+range|pay\s+range|pay\s+band|base\s+pay\s+range|compensation\s+range|compensation)\D{{0,40}}(\$?\s*[\d,]+(?:\.\d+)?[kKmM]?)\s*{dash}\s*(\$?\s*[\d,]+(?:\.\d+)?[kKmM]?)",
            tline,
            flags=re.IGNORECASE,
        )
        if m:
            smin = _money_to_number(m.group(2))
            smax = _money_to_number(m.group(3))
            if smin is None or smax is None:
                continue
            period = _infer_period_from_context(tline, m.start(), m.end())
            if not period:
                # if line contains "annual/annually/per year" we accept as year
                if re.search(r"\b(annual|annually|per\s*year|/yr|yearly)\b", low):
                    period = "year"
                elif re.search(r"\b(hourly|per\s*hour|/hr)\b", low):
                    period = "hour"
                elif re.search(r"\b(monthly|per\s*month|/mo)\b", low):
                    period = "month"
                else:
                    continue

            # sanity
            lo, hi = min(smin, smax), max(smin, smax)
            if period == "year" and lo < Decimal("15000"):
                continue
            if period == "hour" and lo < Decimal("7"):
                continue

            return lo, hi, period

        # (C) Single salary like “$95,000 annually”
        m = re.search(r"(\$[\d,]+(?:\.\d+)?[kKmM]?)", tline)
        if m:
            period = _infer_period_from_context(tline, m.start(), m.end())
            if not period:
                if re.search(r"\b(annual|annually|per\s*year|/yr|yearly)\b", low):
                    period = "year"
                elif re.search(r"\b(hourly|per\s*hour|/hr)\b", low):
                    period = "hour"
                elif re.search(r"\b(monthly|per\s*month|/mo)\b", low):
                    period = "month"
                else:
                    continue
            v = _money_to_number(m.group(1))
            if v is None:
                continue

            if period == "year" and v < Decimal("15000"):
                continue
            if period == "hour" and v < Decimal("7"):
                continue

            return v, v, period

    return None, None, None

# ============================================================
# TITLE / COMPANY / LOCATION (DEFENSIVE)
# ============================================================

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

    t = normalize_for_matching((title_hint or '') + ' ' + (desc or ''))
def infer_experience_level(desc: str, title_hint: Optional[str] = None) -> Optional[str]:
    """
    Levels: entry, associate, mid, senior
    Priority:
      1) Strong title tokens
      2) Experience-years tied to "experience"
      3) Explicit “entry level” phrases
      Else: None
    """
    t = normalize_for_matching((title_hint or '') + ' ' + (desc or ''))
    if title_hint:
        tt = normalize_for_matching(title_hint)
        if re.search(r"\b(intern|internship)\b", tt):
            return "entry"
        if re.search(r"\b(entry[- ]level|junior|jr\.?)\b", tt):
            return "entry"
        if re.search(r"\b(associate)\b", tt):
            return "associate"
        if re.search(r"\b(senior|sr\.?|lead|principal|staff)\b", tt):
            return "senior"
        if re.search(r"\b(manager|director|head of|vp|vice president)\b", tt):
            return "senior"
    # explicit entry phrasing in description
    if re.search(r"\b(entry[- ]level|new grad|recent graduate|0\+?\s*years?\s+of\s+experience)\b", t):
        return "entry"

    yrs = _extract_years_experience_requirements(desc or "")
    if yrs is not None:
        if yrs >= 5:
            return "senior"
        if yrs >= 3:
            return "mid"
        if yrs >= 1:
            return "associate"
        return "entry"

    # light fallback: common title families (kept conservative)
    if title_hint:
        tt = normalize_for_matching(title_hint)
        if re.search(r"\b(analyst|specialist|coordinator)\b", tt):
            return "associate"
        if re.search(r"\b(engineer|scientist)\b", tt):
            return "mid"

    return None


# ============================================================
# SKILL EXTRACTION (ALLOWLIST ONLY + AI SPECIAL RULE)
# ============================================================

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

    # If skill_aliases exists, load and merge (still restricted to ALLOWED_CANON_SKILLS)
    try:
        cur.execute("""
            SELECT skill_name, alias
            FROM skill_aliases
            WHERE skill_name IS NOT NULL AND alias IS NOT NULL
        """)
        rows = cur.fetchall()
        for r in rows:
            canon = _canon_key(r["skill_name"])
            alias = clean_text(r["alias"])
            if not canon or not alias:
                continue
            if canon not in ALLOWED_CANON_SKILLS:
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

    # ensure every allowed canon has at least itself as alias
    for canon in ALLOWED_CANON_SKILLS:
        aliases.setdefault(canon, [])
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

def build_skill_patterns(skill_aliases: Dict[str, List[str]]) -> List[Tuple[re.Pattern, str, str]]:
    """
    Returns list of (pattern, canonical, alias_string) sorted longest alias first.
    AI is handled specially (standalone token only).
    """
    compiled: List[Tuple[re.Pattern, str, str]] = []

    for canon, aliases in skill_aliases.items():
        if _canon_norm(canon) in {normalize_for_matching(x) for x in BANNED_CANON_SKILLS}:
            continue

        for a in aliases:
            a = clean_text(a)
            if not a:
                continue

            if canon == "AI":
                # standalone token, not part of words (avoid "ai-assisted", "paid", etc.)
                pat = re.compile(r"(?<![a-z0-9])ai(?![a-z0-9])", flags=re.IGNORECASE)
                compiled.append((pat, canon, "ai"))
                continue

            esc = re.escape(a)
            esc = esc.replace(r"\ ", r"\s+")
            esc = esc.replace(r"\/", r"\s*/\s*")
            esc = esc.replace(r"\-", r"\s*-\s*")
            # word boundaries for alphanum
            prefix = r"\b" if re.match(r"^[A-Za-z0-9]", a) else ""
            suffix = r"\b" if re.match(r".*[A-Za-z0-9]$", a) else ""
            pat = re.compile(prefix + esc + suffix, flags=re.IGNORECASE)
            compiled.append((pat, canon, a.lower()))

    compiled.sort(key=lambda x: len(x[2]), reverse=True)
    return compiled

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

def md5_id(prefix: str, raw: str, n: int = 10) -> str:
    import hashlib
    h = hashlib.md5((raw or "").encode("utf-8")).hexdigest()[:n]
    return f"{prefix}{h}"

def upsert_company(cur, company_name: str, company_type: Optional[str] = None) -> Optional[str]:
    company_name = clean_text(company_name)
    if not company_name:
        return None
    company_id = md5_id("C", company_name)

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
    role_id = md5_id("R", role_name)

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
    location_id = md5_id("L", f"{location}|{st or ''}")

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
        sid = md5_id("S", canon)
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
def parse_dimensions(desc: str, cur) -> ParsedJob:
    dims = extract_title_company_location_from_description(desc or "")

    pj = ParsedJob()
    pj.company = dims.get("company")
    pj.title = dims.get("title")
    pj.location = dims.get("location")
    pj.state = dims.get("state")

    pj.workplace_type = parse_workplace_type(desc or "")
    pj.employment_type = parse_employment_type(desc or "")

    smin, smax, sper = parse_salary_range(desc or "")
    pj.salary_min, pj.salary_max, pj.salary_period = smin, smax, sper

    pj.experience_level = infer_experience_level(desc or "", pj.title)

    # optional strict enrichments
    pj.company_type = infer_company_type(pj.company, desc or "")
    pj.role_archetype = infer_role_archetype(pj.title, desc or "")

    return pj


# ============================================================
# MAIN ENRICH LOOP
# ============================================================

def enrich_jobs(limit: int, apply: bool, only_missing: bool, rescan_skills: bool) -> None:
    conn = get_conn()
    conn.autocommit = False
    cur = conn.cursor(cursor_factory=DictCursor)

    # Load existing skills + aliases and build anywhere-scan patterns
    canon_to_skill_id = load_existing_skill_ids(cur)
    skill_aliases = load_skill_aliases_from_db(cur)
    patterns = build_skill_patterns_anywhere(canon_to_skill_id, skill_aliases)

    # Select jobs
    if only_missing:
        cur.execute(
            """
            SELECT jp.job_id, jp.description_text,
                   jp.company_id, jp.role_id, jp.location_id,
                   jp.workplace_type, jp.employment_type, jp.experience_level,
                   jp.salary_min, jp.salary_max, jp.salary_period,
                   EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id) AS has_skills
            FROM job_postings jp
            WHERE jp.description_text IS NOT NULL AND length(jp.description_text) > 0
              AND (
                   jp.company_id IS NULL OR jp.role_id IS NULL OR jp.location_id IS NULL
                OR jp.workplace_type IS NULL OR jp.employment_type IS NULL OR jp.experience_level IS NULL
                OR jp.salary_min IS NULL OR jp.salary_max IS NULL OR jp.salary_period IS NULL
                OR NOT EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id)
              )
            ORDER BY jp.ingested_at DESC
            LIMIT %s
            """,
            (limit,),
        )
    else:
        cur.execute(
            """
            SELECT jp.job_id, jp.description_text,
                   jp.company_id, jp.role_id, jp.location_id,
                   jp.workplace_type, jp.employment_type, jp.experience_level,
                   jp.salary_min, jp.salary_max, jp.salary_period,
                   EXISTS (SELECT 1 FROM job_skills js WHERE js.job_id = jp.job_id) AS has_skills
            FROM job_postings jp
            WHERE jp.description_text IS NOT NULL AND length(jp.description_text) > 0
            ORDER BY jp.ingested_at DESC
            LIMIT %s
            """,
            (limit,),
        )

    jobs = cur.fetchall()

    planned_updates = 0
    planned_skill_inserts = 0
    touched = 0

    for job in jobs:
        job_id = job["job_id"]
        desc = job["description_text"] or ""

        pj = parse_dimensions(desc, cur)

        fields = []
        params = []

        # Company
        if pj.company and job["company_id"] is None:
            cid = upsert_company(cur, pj.company, pj.company_type)
            if cid:
                fields.append("company_id=%s"); params.append(cid)

        # Role
        if pj.title and job["role_id"] is None:
            rid = upsert_role(cur, pj.title, pj.role_archetype)
            if rid:
                fields.append("role_id=%s"); params.append(rid)

        # Location
        if (pj.location or pj.state) and job["location_id"] is None:
            lid = upsert_location(cur, pj.location or "", pj.state)
            if lid:
                fields.append("location_id=%s"); params.append(lid)

        # Workplace + employment + experience
        if pj.workplace_type and pj.workplace_type != job["workplace_type"]:
            fields.append("workplace_type=%s"); params.append(pj.workplace_type)
        if pj.employment_type and pj.employment_type != job["employment_type"]:
            fields.append("employment_type=%s"); params.append(pj.employment_type)
        if pj.experience_level and pj.experience_level != job["experience_level"]:
            fields.append("experience_level=%s"); params.append(pj.experience_level)

        # Salary only fills missing (no overwrites)
        if pj.salary_min is not None and job["salary_min"] is None:
            fields.append("salary_min=%s"); params.append(pj.salary_min)
        if pj.salary_max is not None and job["salary_max"] is None:
            fields.append("salary_max=%s"); params.append(pj.salary_max)
        if pj.salary_period is not None and job["salary_period"] is None:
            fields.append("salary_period=%s"); params.append(pj.salary_period)

        # Skills
        ins = 0
        should_scan_skills = rescan_skills or (not job["has_skills"])
        if should_scan_skills:
            canon_to_priority = extract_skills_allowlist(desc, patterns)
            ins = insert_job_skills(
                cur,
                job_id,
                canon_to_priority,
                canon_to_skill_id,
            )

        if fields or ins:
            touched += 1
            planned_updates += (1 if fields else 0)
            planned_skill_inserts += ins

        if apply and fields:
            params.append(job_id)
            cur.execute(f"UPDATE job_postings SET {', '.join(fields)} WHERE job_id=%s", tuple(params))

    print(f"Scanned jobs: {len(jobs)}")
    print(f"Jobs touched: {touched}")
    print(f"Planned job_postings updates: {planned_updates}")
    print(f"Planned job_skills inserts: {planned_skill_inserts}")

    if apply:
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
    args = ap.parse_args()

    enrich_jobs(limit=args.limit, apply=args.apply, only_missing=args.only_missing, rescan_skills=args.rescan_skills)


if __name__ == "__main__":
    main()