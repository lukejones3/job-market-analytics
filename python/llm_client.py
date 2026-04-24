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
