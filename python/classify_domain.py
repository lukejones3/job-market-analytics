"""
Domain classifier for job_postings.domain / domain_secondary.

Three-stage pipeline:
  1. Title pass  — compile each vertical's regex patterns against the job title
  2. Skills pass — count skill_aliases matches per vertical in the description
  3. LLM pass    — Haiku 4.5 when stages 1+2 fail to produce a winner

Public API
----------
build_alias_map(conn) -> dict[str, str]
    Load skill_aliases + skills from DB: {alias_lower: primary_vertical}.
    Call once at startup and pass the result into classify_domain.

classify_domain(title, description, alias_map, use_llm=False, llm_cache=None)
    Returns (domain, domain_secondary, method).
    method: 'title' | 'skills' | 'llm' | 'default'
"""

from __future__ import annotations

import re
import sys
import json
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent))
from vertical_taxonomy import VERTICALS

log = logging.getLogger(__name__)

VALID_VERTICALS: frozenset[str] = frozenset(VERTICALS.keys())

# ── Compile title patterns at import time ──────────────────────────────────────

def _compile_patterns() -> dict[str, re.Pattern]:
    compiled: dict[str, re.Pattern] = {}
    for vkey, vdata in VERTICALS.items():
        parts = [f"(?:{p})" for p in vdata["patterns"]]
        compiled[vkey] = re.compile("|".join(parts), re.IGNORECASE)
    return compiled

_PATTERN_MAP: dict[str, re.Pattern] = _compile_patterns()

MIN_SKILL_MATCHES = 2   # skills pass: minimum matches to trust a winner
SECONDARY_RATIO   = 0.4  # skills pass: secondary if score >= this fraction of winner


# ── Build alias map ────────────────────────────────────────────────────────────

def build_alias_map(conn) -> Dict[str, str]:
    """
    Load {alias_text_lower: primary_vertical} from DB.
    Only includes skills that have a non-null vertical.
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sa.alias_text, s.vertical
            FROM skill_aliases sa
            JOIN skills s ON s.skill_id = sa.skill_id
            WHERE s.vertical IS NOT NULL
        """)
        return {row[0].lower(): row[1] for row in cur.fetchall()}


# ── Stage 1: title patterns ────────────────────────────────────────────────────

def _classify_by_title(title: str) -> Tuple[Optional[str], List[str]]:
    if not title:
        return None, []

    scores: dict[str, int] = {}
    for vkey, pattern in _PATTERN_MAP.items():
        m = pattern.findall(title)
        if m:
            scores[vkey] = len(m)

    if not scores:
        return None, []

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    primary = ranked[0][0]
    primary_score = ranked[0][1]
    # Secondary: any other vertical that matched at all (title match = strong signal)
    secondary = [v for v, _ in ranked[1:]]
    return primary, secondary


# ── Stage 2: description skill aliases ────────────────────────────────────────

def _classify_by_skills(
    description: str, alias_map: Dict[str, str]
) -> Tuple[Optional[str], List[str]]:
    if not description or not alias_map:
        return None, []

    desc_lower = description.lower()
    scores: dict[str, int] = {}

    for alias, vertical in alias_map.items():
        if alias in desc_lower:
            scores[vertical] = scores.get(vertical, 0) + 1

    if not scores or max(scores.values()) < MIN_SKILL_MATCHES:
        return None, []

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    primary = ranked[0][0]
    primary_score = ranked[0][1]
    threshold = max(primary_score * SECONDARY_RATIO, MIN_SKILL_MATCHES)
    secondary = [v for v, s in ranked[1:] if s >= threshold]
    return primary, secondary


# ── Stage 3: LLM fallback ──────────────────────────────────────────────────────

_LLM_AVAILABLE: bool | None = None  # None = not yet attempted


def _ensure_llm() -> bool:
    global _LLM_AVAILABLE
    if _LLM_AVAILABLE is not None:
        return _LLM_AVAILABLE
    try:
        from llm_client import get_client, _is_breaker_tripped  # noqa: F401
        _LLM_AVAILABLE = True
    except ImportError:
        _LLM_AVAILABLE = False
    return _LLM_AVAILABLE


_VERTICAL_DESCRIPTIONS = {
    "data_ml":      "data scientists, ML/AI engineers, data engineers, data analysts, BI",
    "engineering":  "software engineers, DevOps, SRE, platform, security, mobile, embedded",
    "finance":      "FP&A, accounting, investment banking, financial analysis, treasury, actuarial",
    "marketing":    "marketing managers, growth, SEO/SEM, content, brand, paid media, comms",
    "product":      "product managers, UX researchers, product analysts",
    "sales":        "account executives, SDRs, sales managers, revenue operations",
    "design":       "UX/UI designers, graphic designers, brand designers, motion",
    "ops":          "operations managers, project/program managers, BizOps, HR, recruiting, facilities",
}

_VERTICAL_LIST = "\n".join(
    f"- {k}: {v}" for k, v in _VERTICAL_DESCRIPTIONS.items()
)


def _classify_by_llm(
    title: str,
    description: str,
    llm_cache: Dict[str, Tuple[str, List[str]]],
) -> Tuple[Optional[str], List[str]]:
    if not _ensure_llm():
        return None, []

    cache_key = (title or "").lower().strip()
    if cache_key in llm_cache:
        return llm_cache[cache_key]

    from llm_client import get_client, _is_breaker_tripped, _rate_limit, MODEL

    if _is_breaker_tripped():
        return None, []

    snippet = (description or "")[:500]
    prompt = f"""Classify this job posting into ONE primary professional vertical.

VERTICALS:
{_VERTICAL_LIST}

Rules:
- TITLE-FIRST: trust a clear title (e.g. "Software Engineer" → engineering, "Data Scientist" → data_ml)
- Secondary: list up to 2 other verticals only if the role clearly spans them
- Return JSON only: {{"domain": "<key>", "secondary": ["<key>", ...]}}

TITLE: {title}
DESCRIPTION: {snippet}"""

    text = None
    for attempt in range(3):
        try:
            _rate_limit()
            client = get_client()
            resp = client.messages.create(
                model=MODEL,
                max_tokens=80,
                temperature=0,
                messages=[{"role": "user", "content": prompt}],
            )
            text = resp.content[0].text.strip()
            break
        except Exception as e:
            if "rate_limit" in str(e).lower() or "429" in str(e):
                time.sleep(20 + attempt * 10)
                continue
            log.warning(f"classify_domain LLM error: {e}")
            return None, []

    if not text:
        return None, []

    try:
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text
            text = text.rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()
        data = json.loads(text)
        domain = data.get("domain", "").lower()
        if domain not in VALID_VERTICALS:
            return None, []
        secondary = [v for v in (data.get("secondary") or []) if v in VALID_VERTICALS and v != domain]
        llm_cache[cache_key] = (domain, secondary)
        return domain, secondary
    except (json.JSONDecodeError, KeyError) as e:
        log.warning(f"classify_domain LLM parse error: {e} — text={text!r}")
        return None, []


# ── Public API ─────────────────────────────────────────────────────────────────

def classify_domain(
    title: str,
    description: str,
    alias_map: Dict[str, str],
    use_llm: bool = False,
    llm_cache: Optional[Dict[str, Tuple[str, List[str]]]] = None,
) -> Tuple[str, List[str], str]:
    """
    Classify a job posting into a domain vertical.

    Returns (domain, domain_secondary, method) where method is one of:
      'title'      — matched via vertical title patterns
      'skills'     — matched via skill alias counts in description
      'llm'        — matched via LLM (Haiku 4.5)
      'llm_cached' — LLM result served from in-process cache
      'default'    — no signal found; fallback to data_ml
    """
    domain, secondary, method = _run_classify(title, description, alias_map, use_llm, llm_cache)
    return domain, secondary, method


def _run_classify(title, description, alias_map, use_llm, llm_cache):
    # Stage 1: title
    domain, secondary = _classify_by_title(title)
    if domain:
        return domain, secondary, "title"

    # Stage 2: skills
    domain, secondary = _classify_by_skills(description, alias_map)
    if domain:
        return domain, secondary, "skills"

    # Stage 3: LLM
    if use_llm:
        cache = llm_cache if llm_cache is not None else {}
        cache_key = (title or "").lower().strip()
        if cache_key in cache:
            return cache[cache_key][0], cache[cache_key][1], "llm_cached"
        domain, secondary = _classify_by_llm(title, description, cache)
        if domain:
            return domain, secondary, "llm"

    return None, [], "default"
