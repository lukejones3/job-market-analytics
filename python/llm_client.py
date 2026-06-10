"""
Shared LLM client for enrichment tasks.
Provides caching, cost logging, and retry logic for all LLM-powered features.
"""
import asyncio
import os
import sys
import json
import time
import hashlib
import logging
from pathlib import Path
from decimal import Decimal
from typing import Optional, Tuple
from collections import deque
from anthropic import Anthropic, AsyncAnthropic, APIError

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vertical_taxonomy import VERTICALS
import role_taxonomy

log = logging.getLogger(__name__)

# Haiku 4.5 — cheap, fast, great for structured extraction
MODEL = "claude-haiku-4-5-20251001"

# CIRCUIT_BREAKER_v1
# Module-level flag set when API rejects calls due to billing/auth issues.
# Once True, all classify_role / extract_salary_llm calls short-circuit to None
# instead of repeatedly hitting the API for every job in the batch.
# Resets on process restart (each cron run gets a fresh attempt).
_API_DISABLED = False
_API_DISABLED_REASON = ""


def _is_fatal_api_error(err) -> bool:
    """Return True for errors that mean we should stop calling the API."""
    s = str(err).lower()
    fatal_patterns = (
        "credit balance is too low",
        "billing",
        "invalid_api_key",
        "authentication",
        "401",
        "403",
    )
    return any(p in s for p in fatal_patterns)


def _trip_breaker(reason: str):
    """Trip the circuit breaker. All subsequent LLM calls short-circuit."""
    global _API_DISABLED, _API_DISABLED_REASON
    if not _API_DISABLED:
        _API_DISABLED = True
        _API_DISABLED_REASON = reason[:200]
        log.error(f"LLM circuit breaker TRIPPED: {reason[:200]}")


def _is_breaker_tripped() -> bool:
    return _API_DISABLED
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
    # CIRCUIT_BREAKER_v1: short-circuit if breaker tripped this run
    if _is_breaker_tripped():
        return None
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
            # CIRCUIT_BREAKER_v1: trip on billing/auth errors
            if _is_fatal_api_error(e):
                _trip_breaker(f"extract_salary_llm: {e}")
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


# === BLOCKED_AGGREGATORS_v1 ===
# Companies that are spam aggregators reposting other companies' listings.
# Drop their jobs at ingest entirely (status='ignored').
BLOCKED_AGGREGATORS = frozenset({
    "jobgether",
    "tsmg",
    "jobs for humanity",
})


def is_blocked_aggregator(company_name):
    """Return True if company is on the spam-aggregator block list."""
    if not company_name:
        return False
    c = company_name.strip().lower()
    if c in BLOCKED_AGGREGATORS:
        return True
    for agg in BLOCKED_AGGREGATORS:
        if agg in c:
            return True
    return False
# === END_BLOCKED_AGGREGATORS_v1 ===


# === FEDERAL_STAFFING_PRECHECK_v1 ===
# Hard-coded list of known federal staffing firms. Any job at these companies
# is auto-classified as non_data without an LLM call. Add to this list as
# new offenders surface in the eval set.
FEDERAL_STAFFING_FIRMS = frozenset({
    "prosidian", "prosidian consulting",
    "booz allen", "booz allen hamilton",
    "mantech", "man tech",
    "saic",
    "caci",
    "leidos",
    "engility",
    "mitre",
    "gdit", "general dynamics it", "general dynamics information technology",
    "accenture federal services", "accenture federal",
    "guidehouse",
    "deloitte federal",
    "kbr",
    "peraton",
    "v2x",
    "amentum",
    "parsons federal",
    "noblis",
    "two six technologies",
    "streamline defense",
    "clarity innovations",
    "scientific research corporation", "src inc",
    "csra",
    "vectrus",
    "serco federal",
    "cgsfederal", "cgs federal",  # CGSFEDERAL_v1
})


def _is_federal_staffing(company_name):
    """Return True if company is on the known federal staffing firm list."""
    if not company_name:
        return False
    c = company_name.strip().lower()
    # Exact match first
    if c in FEDERAL_STAFFING_FIRMS:
        return True
    # Substring match for variants ("ProSidian Consulting, LLC" → matches "prosidian")
    for firm in FEDERAL_STAFFING_FIRMS:
        if firm in c:
            return True
    return False


def _federal_staffing_verdict():
    """Standard verdict for known-federal companies. Mirrors LLM dict shape."""
    return {
        "is_data_ml": False,
        "category": "non_data",
        "confidence": "high",
        "reason": "known federal staffing firm (pre-check, no LLM call)",
    }
# === END_FEDERAL_STAFFING_PRECHECK_v1 ===

# === DATA_TITLE_PRECHECK_v1 ===
# Title patterns that map UNAMBIGUOUSLY to a data subcategory.
# These bypass the LLM (saves $, eliminates misclassification of clear titles).
# Order matters within each entry — we use re.search with re.IGNORECASE.
import re as _re

# Sourced from config/role_taxonomy.json (domains.data_ml.title_precheck) via the loader.
# Ordered [(pattern_str, slug)]; slug 'auto_subcat' is resolved in _data_title_subcategory.
DATA_TITLE_PATTERNS = role_taxonomy.data_title_patterns()

def _data_title_subcategory(title):
    """Return data subcategory if title matches an unambiguous pattern, else None."""
    if not title:
        return None
    t = title.lower()
    for pat, cat in DATA_TITLE_PATTERNS:
        m = _re.search(pat, t)
        if m:
            if cat == "auto_subcat":
                # "Senior Data Engineer" → engineering, "Sr Data Scientist" → science, etc.
                # The captured 3rd group should be the subcategory keyword
                tail = m.group(3) if m.lastindex and m.lastindex >= 3 else ""
                if "engineer" in tail or "architect" in tail:
                    return "data_engineering"
                if "scientist" in tail:
                    return "data_science"
                if "analyst" in tail:
                    return "data_analytics"
                return "data_engineering"  # default
            return cat
    return None


def _data_title_verdict(category, title):
    """Standard verdict for data-title pre-check matches. Mirrors LLM dict shape."""
    return {
        "is_data_ml": True,
        "category": category,
        "confidence": "high",
        "reason": f"unambiguous data title '{title}' (pre-check, no LLM call)",
    }
# === END_DATA_TITLE_PRECHECK_v1 ===

# CLASSIFY_ROLE_REWRITE_v1: BLOCKED_AGGREGATORS at module level (was incorrectly inside classify_role)
BLOCKED_AGGREGATORS = frozenset({
    "jobgether",
    "tsmg",
    "jobs for humanity",
})


def is_blocked_aggregator(company_name):
    """Return True if company is on the spam-aggregator block list."""
    if not company_name:
        return False
    c = company_name.strip().lower()
    if c in BLOCKED_AGGREGATORS:
        return True
    for agg in BLOCKED_AGGREGATORS:
        if agg in c:
            return True
    return False


# === DOMAIN_AWARE_CLASSIFY_v1 ===
# Hand-written prompts for the three domains that need explicit disambiguation rules.
# All other domains use the generic template generated from vertical_taxonomy subcategories.

_PROMPT_DATA_ML = """\
You are classifying a data/analytics/ML job posting into its precise subcategory.

SUBCATEGORIES — return exactly one:
{subcat_lines}
RULES:
- TITLE-FIRST: trust a clear title. "Data Engineer" → data_engineering regardless of description.
- "Analytics Engineer" → analytics_engineering (not data_analytics).
- "Business Analyst" is ambiguous — use description:
    SQL/Python/dashboards/statistical work → data_analytics
    Requirements/SAP/process documentation → data_analytics only if real analytics work is evident.
- Risk/Fraud/Pricing analysts using Python or statistical modeling → data_analytics.
- Federal contracting signals (GS grades, labor category, IDIQ, "contract contingent",
  explicit agency contract codes like [USDA001016]) → classify normally but set confidence "low".
- Defense product companies (Anduril, Palantir, Scale AI, Shield AI, Lockheed AI Labs,
  Northrop ML) → classify normally, confidence "high".
- THIN DESCRIPTIONS: if title is unambiguous, trust it and set confidence "high".

Return JSON only — no other text:
{{"category": "<subcategory>", "confidence": "high"|"low", "reason": "one sentence"}}

TITLE: {role_title}
DESCRIPTION: {snippet}"""

_PROMPT_ENGINEERING = """\
You are classifying a software/systems engineering job posting into its precise subcategory.

SUBCATEGORIES — return exactly one:
{subcat_lines}
RULES:
- TITLE-FIRST: "Backend Engineer" → backend. "iOS Developer" → mobile. "QA Engineer" → qa.
- "Software Engineer" with no qualifying context → general.
- "Growth Engineer" → fullstack (builds product features, not marketing campaigns).
- ML Engineers and Data Engineers are domain=data_ml — they will never reach this prompt.
- Solutions Engineers / Sales Engineers are domain=sales — they will never reach this prompt.

Return JSON only — no other text:
{{"category": "<subcategory>", "confidence": "high"|"low", "reason": "one sentence"}}

TITLE: {role_title}
DESCRIPTION: {snippet}"""

_PROMPT_SALES = """\
You are classifying a sales/revenue job posting into its precise subcategory.

SUBCATEGORIES — return exactly one:
{subcat_lines}
RULES:
- TITLE-FIRST: "Account Executive" → account_executive. "SDR" or "BDR" → bdr_sdr.
- Customer Success vs Account Management:
    CS  = adoption / health score / outcomes / onboarding
    AM  = renewal / upsell / expansion / quota on existing book
- "Solutions Architect" in sales context → sales_engineering.
- "Sales Manager" managing a team → sales_leadership (not account_executive).

Return JSON only — no other text:
{{"category": "<subcategory>", "confidence": "high"|"low", "reason": "one sentence"}}

TITLE: {role_title}
DESCRIPTION: {snippet}"""

# Generic template used for finance, marketing, product, design, ops.
# Subcategory list is built at call time from vertical_taxonomy so it stays in sync.
_PROMPT_GENERIC_TMPL = """\
You are classifying a {display_name} job posting into its precise subcategory.

SUBCATEGORIES — return exactly one:
{subcat_lines}

RULES:
- TITLE-FIRST: trust a clear title if it unambiguously maps to one subcategory.
- Set confidence "low" only when the title is ambiguous AND the description is unclear.

Return JSON only — no other text:
{{"category": "<subcategory>", "confidence": "high"|"low", "reason": "one sentence"}}

TITLE: {role_title}
DESCRIPTION: {snippet}"""

_HAND_WRITTEN = {"data_ml", "engineering", "sales"}


def _build_domain_prompt(domain: str, role_title: str, snippet: str) -> str:
    # Subcategory vocabulary for every domain comes from config/role_taxonomy.json
    # (single source of truth). The per-domain RULES text below stays here — it is
    # disambiguation guidance, not vocabulary.
    subcat_lines = role_taxonomy.llm_subcategory_block(domain)
    if domain == "data_ml":
        return _PROMPT_DATA_ML.format(subcat_lines=subcat_lines, role_title=role_title, snippet=snippet)
    if domain == "engineering":
        return _PROMPT_ENGINEERING.format(subcat_lines=subcat_lines, role_title=role_title, snippet=snippet)
    if domain == "sales":
        return _PROMPT_SALES.format(subcat_lines=subcat_lines, role_title=role_title, snippet=snippet)
    # Generic template for finance, marketing, product, design, ops
    display_name = role_taxonomy.domain_label(domain)
    return _PROMPT_GENERIC_TMPL.format(
        display_name=display_name,
        subcat_lines=subcat_lines,
        role_title=role_title,
        snippet=snippet,
    )


def classify_role(
    role_title: str,
    description: str,
    domain: str,
    company_name: str = None,
) -> Optional[dict]:
    """
    Classify a job posting into its domain subcategory.

    Args:
        role_title:   job title
        description:  job description text
        domain:       vertical key from vertical_taxonomy (data_ml, engineering, sales, ...)
                      Must not be None — callers skip this function for NULL-domain jobs.
        company_name: optional, used for federal-staffing pre-check on data_ml only

    Returns:
        {"category": str, "confidence": "high"|"low", "reason": str}
        or None on API failure / circuit-breaker tripped.

    Note: is_data_ml is no longer in the return dict. Domain already encodes that.
    ec.py and other eval tools that read is_data_ml will get False from .get("is_data_ml", False).
    """
    if not role_title or not domain:
        return None
    if _is_breaker_tripped():
        return None

    # Blocked aggregators — all domains
    if is_blocked_aggregator(company_name):
        return None

    # data_ml-only pre-checks
    if domain == "data_ml":
        if _is_federal_staffing(company_name):
            return _federal_staffing_verdict()
        _subcat = _data_title_subcategory(role_title)
        if _subcat:
            return _data_title_verdict(_subcat, role_title)

    snippet = (description or "")[:600]
    prompt = _build_domain_prompt(domain, role_title, snippet)

    text = None
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
            log.warning(f"classify_role API error (domain={domain}): {e}")
            if _is_fatal_api_error(e):
                _trip_breaker(f"classify_role: {e}")
            return None

    if text is None:
        return None

    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        if "category" not in data:
            return None
        return data
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"classify_role parse error (domain={domain}): {e}")
        return None
# === END_DOMAIN_AWARE_CLASSIFY_v1 ===


# ── Shared helpers used by both async and batch paths ─────────────────────────

_SALARY_PROMPT_TMPL = """\
Extract US salary information from this job description snippet.

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
{snippet}"""


def build_salary_prompt(snippet: str) -> str:
    return _SALARY_PROMPT_TMPL.format(snippet=snippet)


def _parse_classify_json(text: str) -> Optional[dict]:
    """Parse LLM classify response text into a result dict, or None on failure."""
    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        if "category" not in data:
            return None
        return data
    except (json.JSONDecodeError, KeyError):
        return None


def _parse_salary_json(text: str) -> Optional[Tuple[Decimal, Decimal, str]]:
    """Parse LLM salary response text into (min, max, period), or None on failure."""
    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
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
        period = data["period"]
        if period == "year" and not (Decimal("15000") <= min_val and max_val <= Decimal("1000000")):
            return None
        if period == "hour" and not (Decimal("7") <= min_val and max_val <= Decimal("500")):
            return None
        if period == "month" and not (Decimal("1000") <= min_val and max_val <= Decimal("50000")):
            return None
        return min(min_val, max_val), max(min_val, max_val), period
    except (json.JSONDecodeError, ValueError, KeyError):
        return None


# ── Async LLM functions (used by enrich_job_postings concurrent path) ─────────

async def classify_role_async(
    role_title: str,
    description: str,
    domain: str,
    company_name: Optional[str],
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
) -> Optional[dict]:
    """Async version of classify_role — shares all pre-checks and prompts."""
    if not role_title or not domain:
        return None
    if _is_breaker_tripped():
        return None
    if is_blocked_aggregator(company_name):
        return None
    if domain == "data_ml":
        if _is_federal_staffing(company_name):
            return _federal_staffing_verdict()
        subcat = _data_title_subcategory(role_title)
        if subcat:
            return _data_title_verdict(subcat, role_title)

    snippet = (description or "")[:600]
    prompt = _build_domain_prompt(domain, role_title, snippet)

    async with semaphore:
        try:
            # SDK handles 429 retries via retry-after headers (max_retries set on client)
            response = await client.messages.create(
                model=MODEL,
                max_tokens=200,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
        except Exception as e:
            log.warning(f"classify_role_async API error (domain={domain}): {e}")
            if _is_fatal_api_error(e):
                _trip_breaker(f"classify_role_async: {e}")
            return None

        try:
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text
                text = text.rsplit("```", 1)[0].strip()
            if text.startswith("json"):
                text = text[4:].strip()
            data = json.loads(text)
            if "category" not in data:
                return None
            return data
        except (json.JSONDecodeError, KeyError) as e:
            log.warning(f"classify_role_async parse error (domain={domain}): {e}")
            return None


async def extract_salary_llm_async(
    description_snippet: str,
    client: AsyncAnthropic,
    semaphore: asyncio.Semaphore,
) -> Optional[Tuple[Decimal, Decimal, str]]:
    """Async version of extract_salary_llm."""
    if not description_snippet or len(description_snippet.strip()) < 20:
        return None
    if _is_breaker_tripped():
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

    async with semaphore:
        try:
            response = await client.messages.create(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                temperature=TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
        except Exception as e:
            log.warning(f"extract_salary_llm_async API error: {e}")
            if _is_fatal_api_error(e):
                _trip_breaker(f"extract_salary_llm_async: {e}")
            return None
        try:
            if text.startswith("```"):
                text = text.split("\n", 1)[1] if "\n" in text else text
                text = text.rsplit("```", 1)[0].strip()
            if text.startswith("json"):
                text = text[4:].strip()
            data = json.loads(text)
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
            period = data["period"]
            if period == "year" and not (Decimal("15000") <= min_val and max_val <= Decimal("1000000")):
                return None
            if period == "hour" and not (Decimal("7") <= min_val and max_val <= Decimal("500")):
                return None
            if period == "month" and not (Decimal("1000") <= min_val and max_val <= Decimal("50000")):
                return None
            return min(min_val, max_val), max(min_val, max_val), period
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            log.warning(f"extract_salary_llm_async parse error: {e}")
            return None
