"""
Domain classifier for job_postings.domain / domain_secondary.

Three-stage pipeline:
  1. Title pass  — compile each vertical's regex patterns against the job title
  2. Skills pass — count skill_aliases matches per vertical in the description
  3. LLM pass    — Haiku 4.5 when stages 1+2 fail to produce a winner

Public API
----------
build_alias_map(conn) -> dict[str, list[str]]
    Load skill_aliases + skills from DB: {alias_lower: [primary_vertical, *also_in]}.
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
from typing import Any, Dict, List, Optional, Tuple

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

# Skills pass: aliases score weight-2 (core tools) or weight-1 (soft/generic terms).
# Soft aliases are short/common words that appear in many non-data JDs.  The weighted
# total must reach MIN_SKILL_SCORE before we trust skills as a vertical signal — this
# prevents "ai" + "ml" in a legal or marketing JD from triggering data_ml classification.
_SOFT_SKILL_ALIASES: frozenset[str] = frozenset({
    "ai", "ml", "data", "analytics", "intelligence", "analysis",
})
MIN_SKILL_SCORE = 6    # weighted total required to assign a vertical via skills alone
SECONDARY_RATIO = 0.4  # secondary vertical if its score >= this fraction of the winner

# ── NULL blocklist: titles that are definitively non-tech verticals ─────────────
# When a title matches here we return domain=NULL immediately — no skill scoring.
# These roles appear in tech-company job feeds but belong to domains we don't cover.
_NULL_BLOCKLIST: re.Pattern = re.compile(
    # ── Medical / clinical ─────────────────────────────────────────────────
    r"\bclinical\b"          # clinical strategy, clinical lead, clinical director
    r"|\bphysician\b"
    r"|\bnurse\b"
    r"|\bsurgeon\b"
    r"|\bpharmacist\b"
    r"|\bveterinari"         # veterinary, veterinarian

    # ── Legal ──────────────────────────────────────────────────────────────
    r"|\blegal\s+(?:counsel|fellow|engineer|advisor|operations|technology)\b"
    r"|\b(?:general|associate\s+general|commercial|corporate|regulatory|compliance|deputy|chief)\s+counsel\b"
    r"|\bcounsel\s*[-,]?\s*(?:emea|americas|apac|gtm|platform|technology|channel|fintech)\b"
    r"|\bparalegal\b"
    r"|\bhead\s+of\s+legal\b"
    r"|\bvp\s+legal\b"
    r"|\bchief\s+legal\s+officer\b"
    r"|\bdeputy\s+general\s+counsel\b"
    r"|\bsenior\s+counsel\b"

    # ── Biology / chemistry / wet lab ──────────────────────────────────────
    r"|\bscientist\b.*(?:chemistry|biology|biological|protein|assay|fluidic|sequencing|wet\s+lab|microbio|biochem|molecular|\bcell\b|tissue|\bgene\b|genome|biolog|pharma)"
    r"|\b(?:chemistry|biology|microbiology|biochemistry)\s+(?:scientist|researcher|engineer|director)\b"
    r"|\bbiologist\b"
    r"|\bbiochemist\b"
    r"|\bpharmaceutical\s+scientist\b"
    r"|\blab\s+manager\b"
    r"|\bresearch\s+associate\b.*(?:biology|chemistry|pharma)"
    r"|\bmedicinal\s+chemist\b"

    # ── K-12 education ─────────────────────────────────────────────────────
    r"|\b(?:elementary|middle|high)\s+school\s+teacher\b"
    r"|\bk-12\s+teacher\b"
    r"|\bschool\s+counselor\b"
    r"|\bsubstitute\s+teacher\b"

    # ── Construction / trades ──────────────────────────────────────────────
    r"|\bconstruction\s+(?:worker|laborer|foreman)\b"
    r"|\bcarpenter\b"
    r"|\belectrician\b"
    r"|\bplumber\b"
    r"|\bwelder\b"
    r"|\bhvac\s+technician\b"
    r"|\bequipment\s+operator\b",

    re.IGNORECASE,
)

# Hardware engineering disciplines with no software/ML context → NULL.
# These appear in aerospace/defense/manufacturing feeds and pull into finance
# via incidental skill matches (Anaplan, financial modeling vocabulary).
_HARDWARE_ENGINEER_RE: re.Pattern = re.compile(
    r"\b(mechanical|electrical|civil|structural)\s+(?:design\s+)?engineer",
    re.IGNORECASE,
)
_SOFTWARE_CONTEXT_RE: re.Pattern = re.compile(
    r"\b(ml|ai\b|software|robotics|embedded|firmware|controls|autonomous|fpga|vision)\b",
    re.IGNORECASE,
)

# Strip parenthetical clarifiers before title classification so "(Tax & Accounting)"
# in "CX/UX Designer (Tax & Accounting)" doesn't bleed into the finance scorer.
_PAREN_RE: re.Pattern = re.compile(r"\s*\([^)]*\)", re.IGNORECASE)

# Tiebreak order for title-stage ties: "software engineer" should beat "data platform",
# "designer" should beat anything, data_ml is last because its patterns are generic
# (e.g. "data platform" fires on SWE titles that happen to mention data infra).
_TITLE_TIEBREAK: dict[str, int] = {
    "engineering": 0,
    "design":      1,
    "product":     2,
    "sales":       3,
    "finance":     4,
    "marketing":   5,
    "ops":         6,
    "data_ml":     7,
}


# ── Build alias map ────────────────────────────────────────────────────────────

def build_alias_map(conn) -> Dict[str, List[str]]:
    """
    Load {alias_text_lower: [primary_vertical, *also_in_verticals]} from DB.

    Each alias credits ALL verticals the skill belongs to with equal weight.
    This prevents data_ml from dominating the skill scorer: SQL, Python, and
    Tableau are primary data_ml skills but also live in finance, marketing, etc.
    Using multi-vertical scoring lets those cross-vertical signals break ties
    correctly (e.g. a Finance JD with SQL+Excel+GAAP now scores finance first).
    """
    with conn.cursor() as cur:
        cur.execute("""
            SELECT sa.alias_text, s.vertical, s.also_in
            FROM skill_aliases sa
            JOIN skills s ON s.skill_id = sa.skill_id
            WHERE s.vertical IS NOT NULL
        """)
        result: Dict[str, List[str]] = {}
        for alias_text, primary, also_in in cur.fetchall():
            key = alias_text.lower()
            verticals: List[str] = [primary]
            if also_in:
                for v in also_in:
                    if v not in verticals:
                        verticals.append(v)
            result[key] = verticals
        return result


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

    ranked = sorted(scores.items(), key=lambda x: (-x[1], _TITLE_TIEBREAK.get(x[0], 99)))
    primary = ranked[0][0]
    # Secondary: any other vertical that matched at all (title match = strong signal)
    secondary = [v for v, _ in ranked[1:]]
    return primary, secondary


# ── Stage 2: description skill aliases ────────────────────────────────────────

def _classify_by_skills(
    description: str, alias_map: Dict[str, Any]
) -> Tuple[Optional[str], List[str]]:
    """
    Score verticals by counting alias matches in description.

    alias_map may be either:
      - Dict[str, str]        — {alias: primary_vertical}  (legacy, single-vertical)
      - Dict[str, List[str]]  — {alias: [primary, *also_in]}  (multi-vertical, preferred)

    Multi-vertical maps give each matched alias equal credit to every vertical
    it belongs to, preventing data_ml from dominating via its larger alias set.
    """
    if not description or not alias_map:
        return None, []

    desc_lower = description.lower()
    # Tokenize for word-boundary matching of short alphabetical aliases.
    # Substring matching lets 'r' hit "director", 'ai' hit "available", etc.
    # Pure-alpha aliases (sql, ml, ai, r) must appear as whole tokens; aliases
    # with non-alpha chars (c++, c#, asp.net) still use substring matching since
    # word-splitting destroys them.
    desc_words = set(re.split(r'\W+', desc_lower))
    desc_words.discard('')

    scores: dict[str, int] = {}

    for alias, vertical_or_list in alias_map.items():
        if alias.isalpha() and ' ' not in alias:
            matched = alias in desc_words
        else:
            matched = alias in desc_lower
        if matched:
            weight = 1 if alias in _SOFT_SKILL_ALIASES else 2
            verticals = (
                vertical_or_list
                if isinstance(vertical_or_list, list)
                else [vertical_or_list]
            )
            for v in verticals:
                scores[v] = scores.get(v, 0) + weight

    if not scores or max(scores.values()) < MIN_SKILL_SCORE:
        return None, []

    ranked = sorted(scores.items(), key=lambda x: -x[1])
    primary = ranked[0][0]
    primary_score = ranked[0][1]
    threshold = max(primary_score * SECONDARY_RATIO, MIN_SKILL_SCORE)
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
      'default'    — no signal found; domain=NULL
    """
    domain, secondary, method = _run_classify(title, description, alias_map, use_llm, llm_cache)
    return domain, secondary, method


def _run_classify(title, description, alias_map, use_llm, llm_cache):
    # Stage 0: NULL blocklist — explicitly non-tech roles we never cover
    if title and _NULL_BLOCKLIST.search(title):
        return None, [], "null_blocked"

    # Stage 0b: Hardware engineering without software context → NULL
    # Prevents mechanical/electrical engineers from scoring into finance via
    # incidental Anaplan / financial-modeling skill matches.
    if title and _HARDWARE_ENGINEER_RE.search(title) and not _SOFTWARE_CONTEXT_RE.search(title):
        return None, [], "null_blocked"

    # Strip parenthetical qualifiers before title classification so that
    # "CX/UX Designer (Tax & Accounting)" doesn't gain a finance score.
    clean_title = _PAREN_RE.sub("", title).strip() if title else title

    # Stage 1: title
    domain, secondary = _classify_by_title(clean_title)
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
