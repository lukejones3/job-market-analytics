"""Extract skills from resume text using DB skill_aliases table.

Three-layer approach (ported from legacy score_resume.py):
  1. Alias regex hits anywhere in body          → confidence 0.92
  2. Skills section line tokenization + variant → confidence 0.88
  3. Heuristic toolish tokens in section lines  → confidence 0.78

Returns a dict keyed by skill_id.
"""

import re
from typing import Dict, List, Tuple, Any


# Broadened from the legacy file's 7 headers — covers more resume formats
SKILL_SECTION_HEADERS = [
    "skills",
    "technical skills",
    "tools",
    "technologies",
    "tech stack",
    "stack",
    "core competencies",
    "competencies",
    "expertise",
    "areas of expertise",
    "proficiencies",
    "technical proficiencies",
    "languages",  # programming languages
    "frameworks",
    "platforms",
    "tools & technologies",
    "tools and technologies",
    "software",
    "key skills",
    "professional skills",
    "hard skills",
    "qualifications",  # sometimes lists tech
]


def _normalize_for_matching(s: str) -> str:
    s = s.lower().replace("\u00a0", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def _clean_token(s: str) -> str:
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def _split_skill_list_line(line: str) -> List[str]:
    """Split on commas/pipes/bullets/slashes. Careful with node.js-like tokens."""
    parts = re.split(r"[,\|•·\u2022]+", line)
    tokens = []
    for p in parts:
        p = _clean_token(p)
        if not p:
            continue
        # explode "etl/elt" but not full URLs/paths
        if "/" in p and len(p) <= 30:
            for sub in p.split("/"):
                sub = _clean_token(sub)
                if sub:
                    tokens.append(sub)
        else:
            tokens.append(p)
    return tokens


def _extract_skill_sections(text: str) -> List[str]:
    """Pull lines after a skills header until next blank/header-ish line."""
    lines = [_clean_token(x) for x in text.splitlines()]
    out = []
    in_skills = False
    for ln in lines:
        low = _normalize_for_matching(ln)
        # Accept exact match or "header:" prefix
        if any(h == low or low.startswith(h + ":") or low.startswith(h + " :") for h in SKILL_SECTION_HEADERS):
            in_skills = True
            continue
        if in_skills:
            # Blank line or all-caps-section-like = end of section
            if low == "" or re.fullmatch(r"[A-Z \-/&]{4,}", ln):
                in_skills = False
                continue
            out.append(ln)
    return out


def _variants(s: str) -> List[str]:
    """Generate punctuation/space-insensitive variants of a token for higher recall."""
    t = _normalize_for_matching(s)
    out = set()
    out.add(t)
    out.add(re.sub(r"\s+", " ", t).strip())
    out.add(re.sub(r"[^a-z0-9]+", "", t))            # "power bi" -> "powerbi"
    out.add(re.sub(r"[^a-z0-9]+", " ", t).strip())   # "a/b" -> "a b"
    out.add(t.replace("microsoft ", "ms "))
    out.add(t.replace("ms ", "microsoft "))
    out.add(t.replace(".", ""))
    return [v for v in out if v]


def _fetch_alias_patterns(cur) -> List[Tuple[re.Pattern, str, str]]:
    """Returns [(compiled_regex, skill_id, alias_text), ...]"""
    cur.execute("""
        SELECT sa.alias_text, sa.skill_id
        FROM skill_aliases sa
        WHERE sa.alias_text IS NOT NULL AND btrim(sa.alias_text) <> ''
    """)
    rows = cur.fetchall()
    out = []
    for r in rows:
        alias_text = r["alias_text"] if isinstance(r, dict) else r[0]
        skill_id = r["skill_id"] if isinstance(r, dict) else r[1]
        alias = _normalize_for_matching(alias_text)
        if not alias:
            continue
        # Word-boundary-ish, safe for tokens like "node.js"
        pat = re.compile(rf"(?i)(^|[^a-z0-9])({re.escape(alias)})([^a-z0-9]|$)")
        out.append((pat, skill_id, alias))
    return out


def _fetch_skill_names(cur, skill_ids: List[str]) -> Dict[str, str]:
    """Get human-readable skill names for the matched IDs."""
    if not skill_ids:
        return {}
    cur.execute(
        "SELECT skill_id, skill_name FROM skills WHERE skill_id = ANY(%s)",
        (skill_ids,),
    )
    rows = cur.fetchall()
    return {
        (r["skill_id"] if isinstance(r, dict) else r[0]):
        (r["skill_name"] if isinstance(r, dict) else r[1])
        for r in rows
    }


def extract_skills(text: str, db_cursor) -> Dict[str, Dict[str, Any]]:
    """Extract skills from resume text.

    Args:
        text: Raw resume text
        db_cursor: Active psycopg2 cursor with DictCursor or RealDictCursor

    Returns:
        {skill_id: {name, confidence, evidence, source}}
    """
    if not text:
        return {}

    alias_patterns = _fetch_alias_patterns(db_cursor)

    t_norm = _normalize_for_matching(text)
    t_sanitized = re.sub(r"[^a-z0-9]+", " ", t_norm)
    padded_sanitized = " " + re.sub(r"\s+", " ", t_sanitized).strip() + " "

    found: Dict[str, Dict[str, Any]] = {}

    def _record(sid: str, conf: float, evidence: str, source: str):
        prev = found.get(sid)
        if not prev or conf > prev["confidence"]:
            found[sid] = {
                "confidence": conf,
                "evidence": evidence,
                "source": source,
            }

    # (A) Alias regex hits anywhere in body
    for pat, sid, alias in alias_patterns:
        m = pat.search(t_norm)
        # Fallback: punctuation-insensitive token match
        if not m and alias and len(alias) >= 3:
            tok = " " + alias + " "
            if tok in padded_sanitized:
                m = re.search(re.escape(alias), t_norm)
        if m:
            start = max(0, m.start() - 60)
            end = min(len(t_norm), m.end() + 60)
            evidence = t_norm[start:end].strip()
            _record(sid, 0.92, evidence, "alias")

    # (B) Skills section parsing
    section_lines = _extract_skill_sections(text)
    section_tokens = []
    for ln in section_lines:
        for tok in _split_skill_list_line(ln):
            t = _clean_token(tok)
            if t:
                section_tokens.append(t)

    # Generate variants and bulk-lookup
    token_variants = {}
    variant_set = set()
    for tok in section_tokens:
        vars_ = _variants(tok)
        seen = set()
        deduped = []
        for v in vars_:
            if v not in seen:
                seen.add(v)
                deduped.append(v)
        token_variants[tok] = deduped
        for v in deduped:
            if len(v) >= 2:
                variant_set.add(v)

    alias_map = {}
    if variant_set:
        db_cursor.execute("""
            SELECT lower(alias_text) AS a, skill_id
            FROM skill_aliases
            WHERE lower(alias_text) = ANY(%s)
        """, (list(variant_set),))
        for r in db_cursor.fetchall():
            a = r["a"] if isinstance(r, dict) else r[0]
            sid = r["skill_id"] if isinstance(r, dict) else r[1]
            alias_map[a] = sid

    for tok in section_tokens:
        sid = None
        for v in token_variants.get(tok, []):
            sid = alias_map.get(v)
            if sid:
                break
        if sid:
            _record(sid, 0.88, tok, "section")

    # (C) Heuristic toolish tokens (lower confidence)
    toolish = set()
    for ln in section_lines[:40]:
        for tok in _split_skill_list_line(ln):
            tok_norm = _normalize_for_matching(tok)
            if re.fullmatch(r"[a-z][a-z0-9\.\+\#\-]{2,30}", tok_norm):
                toolish.add(tok_norm)
    if toolish:
        db_cursor.execute("""
            SELECT lower(alias_text) AS a, skill_id
            FROM skill_aliases
            WHERE lower(alias_text) = ANY(%s)
        """, (list(toolish),))
        for r in db_cursor.fetchall():
            a = r["a"] if isinstance(r, dict) else r[0]
            sid = r["skill_id"] if isinstance(r, dict) else r[1]
            _record(sid, 0.78, a, "heuristic")

    # Attach human-readable skill names
    skill_names = _fetch_skill_names(db_cursor, list(found.keys()))
    for sid, data in found.items():
        data["name"] = skill_names.get(sid, sid)

    return found
