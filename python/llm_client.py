"""
Shared LLM client for enrichment tasks.
Provides caching, cost logging, and retry logic for all LLM-powered features.
"""
import os
import json
import time
import hashlib
import logging
from decimal import Decimal
from typing import Optional, Tuple
from collections import deque
from anthropic import Anthropic, APIError

log = logging.getLogger(__name__)

# Haiku 4.5 — cheap, fast, great for structured extraction
MODEL = "claude-haiku-4-5-20251001"
MAX_TOKENS = 200
TEMPERATURE = 0

# Rate limiting — stay under 50 requests/min free-tier limit
# Using 45 to leave headroom for retries
RATE_LIMIT_RPM = 45
_call_times: deque = deque(maxlen=RATE_LIMIT_RPM)

_client: Optional[Anthropic] = None


def _rate_limit():
    """Block if we've hit 45 calls in the last 60 seconds."""
    now = time.time()
    # Remove calls older than 60s
    while _call_times and _call_times[0] < now - 60:
        _call_times.popleft()
    # If at limit, sleep until the oldest call ages out
    if len(_call_times) >= RATE_LIMIT_RPM:
        sleep_for = 60 - (now - _call_times[0]) + 0.5
        if sleep_for > 0:
            log.info(f"Rate limit: sleeping {sleep_for:.1f}s")
            time.sleep(sleep_for)
            # Re-purge after sleep
            now = time.time()
            while _call_times and _call_times[0] < now - 60:
                _call_times.popleft()
    _call_times.append(time.time())


def get_client() -> Anthropic:
    """Lazy-initialize the Anthropic client."""
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set in environment")
        _client = Anthropic(api_key=api_key)
    return _client


def _cache_key(task: str, input_text: str) -> str:
    """Deterministic hash for caching. Same input + task → same key."""
    h = hashlib.sha256(f"{task}:{input_text}".encode()).hexdigest()
    return h[:16]


def extract_salary_llm(description_snippet: str) -> Optional[Tuple[Decimal, Decimal, str]]:
    """
    LLM-based salary extraction fallback.
    Only call when regex parser has returned None.

    Args:
        description_snippet: ~500 chars around salary keyword in description

    Returns:
        (min, max, period) tuple where period is 'year'|'hour'|'month'
        or None if no salary found / low confidence
    """
    if not description_snippet or len(description_snippet.strip()) < 20:
        return None

    prompt = f"""Extract US salary information from this job description snippet.

STRICT RULES:
- Return JSON only, no other text.
- Schema: {{"min": number|null, "max": number|null, "period": "year"|"hour"|"month"|null, "currency": "USD"|"other"|null, "confidence": "high"|"low"}}
- If NO specific salary numbers are disclosed, return all null values.
- If salary is in non-USD currency (EUR, GBP, PLN, etc.), set currency to "other" — still extract numbers.
- For ranges like "$80K-$100K", convert to full numbers: 80000, 100000.
- period is "year" for annual, "hour" for hourly, "month" for monthly.
- DO NOT infer or estimate salaries from context. DO NOT use funding amounts, revenue figures, or non-salary dollar amounts.
- If the description says "competitive salary" with no number, return all null.
- Set confidence to "low" if the salary is ambiguous or you're unsure.

Snippet:
{description_snippet}"""

    # Retry on rate limit up to 3 times
    for attempt in range(3):
        try:
            _rate_limit()
            client = get_client()
            response = client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            break
        except APIError as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                wait = 20 + (attempt * 10)
                log.warning(f"Rate limited, sleeping {wait}s (attempt {attempt+1}/3)")
                time.sleep(wait)
                continue
            log.warning(f"LLM salary extraction API error: {e}")
            return None
    else:
        log.warning("LLM salary extraction: exhausted retries")
        return None

    try:

        # Strip markdown code fences if LLM adds them
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        data = json.loads(text)

        # Only accept USD, high-ish confidence, both numbers present
        if data.get("currency") != "USD":
            return None
        if data.get("confidence") == "low":
            return None
        if data.get("min") is None or data.get("max") is None:
            return None
        if data.get("period") not in ("year", "hour", "month"):
            return None

        min_val = Decimal(str(data["min"]))
        max_val = Decimal(str(data["max"]))

        # Sanity check magnitude
        period = data["period"]
        if period == "year" and not (Decimal("15000") <= min_val and max_val <= Decimal("1000000")):
            return None
        if period == "hour" and not (Decimal("7") <= min_val and max_val <= Decimal("500")):
            return None
        if period == "month" and not (Decimal("1000") <= min_val and max_val <= Decimal("50000")):
            return None

        return min(min_val, max_val), max(min_val, max_val), period

    except (APIError, json.JSONDecodeError, ValueError, KeyError) as e:
        log.warning(f"LLM salary extraction failed: {e}")
        return None

def classify_role(role_title: str, description: str) -> Optional[dict]:
    """
    Classify whether a job is genuinely data/ML/analytics and what subcategory.
    Returns dict: {is_data_ml: bool, category: str, confidence: str, reason: str}
    or None on API failure.
    """
    if not role_title:
        return None
    snippet = (description or "")[:600]

    prompt = f"""Classify this job posting. Is it genuinely a data, analytics, ML, or AI role?

CATEGORIES:
- "data_analytics": data analyst, business analyst working with SQL/dashboards, BI analyst, marketing analyst doing real data work
- "data_engineering": data engineer, ETL developer, analytics engineer, data platform engineer
- "ml_engineering": ML engineer, MLOps, applied ML, AI engineer building production systems
- "ai_research": research scientist, applied scientist on ML/AI specifically
- "data_science": data scientist, statistician, quant researcher
- "analytics_engineering": analytics engineer (dbt-style), data modeler
- "non_data": NOT a data/ML role — examples: software engineer, product manager (without analytics focus), sales ops, customer success, marketing manager, HR, finance/accounting, generic IT, non-analytics business analyst (e.g. requirements gathering for SAP/AMISYS/healthcare claims systems), data entry, GIS/mapping (unless ML), clinical data coordinator, master data management, data steward (if pure governance)

RULES:
- TITLE-FIRST: If the title clearly says "Data Engineer", "Data Scientist", "Data Analyst", "ML Engineer", "AI Engineer", "Analytics Engineer", "Quantitative Analyst/Researcher", "Applied Scientist", "Research Scientist (ML/AI)" → classify as data/ML by title regardless of description quality. Only use description to pick the subcategory.
- DESCRIPTION-DEPENDENT for ambiguous titles only:
  - "Business Analyst" alone is ambiguous. If description shows SQL/Python/dashboards/analytics → data_analytics. If it shows requirements gathering, SAP/Workday implementation, process documentation, healthcare claims systems (AMISYS, Facets) → non_data.
  - "Operations Analyst" / "Sales Operations" / "Revenue Operations" / "Marketing Operations" → default non_data UNLESS description shows heavy SQL/Python/analytics work → data_analytics.
  - "Data Quality Analyst" doing manual review → non_data. Automated quality with SQL/Python → data_analytics.
- KEEP these as data/ML even if borderline:
  - "Risk Analyst" / "Credit Risk Analyst" / "Market Risk Analyst" / "Quantitative Risk Analyst" (these use modeling, Python, R)
  - "Fraud Analyst" / "Fraud Strategy Analyst" (uses SQL, Python, ML models)
  - "Pricing Analyst" / "Pricing Strategy" (uses analytics)
  - "Actuarial Analyst" (statistics-heavy)
  - "Financial Analyst" with FP&A or modeling focus → data_analytics
- EXCLUDE these:
  - "Sales Coordinator", "Customer Success", "Account Executive" → non_data
  - "Project Manager", "Program Manager" without explicit data focus → non_data
  - "Technical Writer", "Solutions Architect" without data focus → non_data
  - "GIS Analyst" / "Geospatial Analyst" without ML → non_data
  - "Master Data Steward", "Data Coordinator" (governance only) → non_data
- EXCLUDE federal contracting / government staffing roles → non_data:
  - Roles that REQUIRE active US government security clearance (TS/SCI, Top Secret, Secret, Public Trust) for non-tech-product work — typical at federal staffing firms (ProSidian, Booz Allen, ManTech, SAIC, CACI, Leidos, Engility, MITRE)
  - Job IDs containing federal contract codes like [USDA001016], [DOE0062061], [NSF0113113], [GMRC007], [AMR9]
  - Roles describing themselves as "contract contingent", "GS-XX pay grade", "GS-09 / GS-12 / GS-14", "labor category", "BPA", "IDIQ"
  - Government job titles like "Budget Execution Data Analyst", "FSM Budget Analyst", "Federal Acquisition Data Analyst", "Mortgage Backed Securities Risk Analyst" at contractor firms
  - Direct municipal/state/federal employer job postings (City of New York, State of California DMV, USDA, Department of Energy, Department of Defense, Federal Reserve internships, county/city government data analyst roles)
  - Note: Distinguish carefully — DEFENSE/AEROSPACE PRODUCT COMPANIES are kept as data_ml: Anduril, Palantir, Scale AI, Shield AI, Helsing, Lockheed Martin AI Labs, Northrop Grumman AI/ML roles, Raytheon ML engineering, Boeing data science, RTX data engineering. The line is: building a commercial product that DoD buys = data_ml. Staffing a federal contract via labor categories = non_data.
  - Note: Bank holding companies and Federal Reserve regional bank ML/quant roles ARE data_ml (e.g., Federal Reserve Bank of NY quantitative researcher = data_science). Direct civil service / government agency staffing is non_data.
- THIN DESCRIPTIONS: If description is too short or generic to judge but title is clearly data/ML, trust the title and set confidence "high".
- Set confidence to "low" only when title is ambiguous AND description is unclear.

Return JSON only:
{{"is_data_ml": true|false, "category": "...", "confidence": "high"|"low", "reason": "1 sentence"}}

TITLE: {role_title}

DESCRIPTION:
{snippet}"""

    for attempt in range(3):
        try:
            _rate_limit()
            client = get_client()
            response = client.messages.create(
                model=MODEL,
                max_tokens=200,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            break
        except APIError as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                time.sleep(20 + attempt * 10)
                continue
            log.warning(f"classify_role API error: {e}")
            return None
    else:
        return None

    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        if "is_data_ml" not in data or "category" not in data:
            return None
        return data
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"classify_role parse error: {e}")
        return None
