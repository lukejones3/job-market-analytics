"""
Single source of truth loader for the skills taxonomy (config/skill_taxonomy.json).

Replaces the skill vocabulary previously hardcoded in:
  - vertical_taxonomy.VERTICALS[*].skills   (aliases, category, weight, also_in)
  - enrich_job_postings.FALLBACK_ALIASES     (hardcoded alias dict)
  - enrich_job_postings.SKILL_DENYLIST       (hardcoded noise list)

The DB (skills / skill_aliases) remains the runtime index — discover_skills.py may
still add skills there dynamically. This JSON is the canonical seed/floor; consumers
union it with live DB aliases so the dynamic path keeps working.

Consumers:
  enrich_job_postings.py  -> skill_aliases_seed() (FALLBACK base), denylist()
  extract_skills_sql.py   -> relevant_skill_names_for_domain()
  seed_new_skills.py       -> skills()
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Dict, List, Set

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "skill_taxonomy.json",
)


def _path() -> str:
    return os.getenv("SKILL_TAXONOMY_PATH", _DEFAULT_PATH)


@lru_cache(maxsize=1)
def _data() -> dict:
    with open(_path(), encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    _data.cache_clear()


def _canon_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _canon_norm(s: str) -> str:
    return re.sub(r"\s+", " ", _canon_key(s).lower()).strip()


def skills() -> List[dict]:
    """Full skill records (skill_id, skill_name, skill_slug, vertical, also_in,
    category, skill_group, difficulty_relevant, weight, aliases)."""
    return _data()["skills"]


def denylist() -> Set[str]:
    """Normalized generic terms that must never be extracted (mirror SKILL_DENYLIST)."""
    return {_canon_norm(x) for x in _data().get("denylist", [])}


def skill_aliases_seed() -> Dict[str, List[str]]:
    """{canonical_skill_name: [aliases]} — replaces FALLBACK_ALIASES as the seed base
    for load_skill_aliases_from_db (then merged with live DB aliases)."""
    out: Dict[str, List[str]] = {}
    for s in skills():
        out[_canon_key(s["skill_name"])] = list(s.get("aliases", []))
    return out


def skills_by_vertical() -> Dict[str, Dict[str, dict]]:
    """Reproduce the old VERTICALS[v]['skills'] shape grouped by primary vertical:
    {vertical: {skill_name: {aliases, category, weight, also_in}}}."""
    out: Dict[str, Dict[str, dict]] = {}
    for s in skills():
        out.setdefault(s.get("vertical") or "", {})[s["skill_name"]] = {
            "aliases": list(s.get("aliases", [])),
            "category": s.get("category", ""),
            "weight": s.get("weight", 1),
            "also_in": list(s.get("also_in", [])),
        }
    return out


def relevant_skill_names_for_domain(domain: str) -> Set[str]:
    """Skills whose primary vertical is `domain` OR whose also_in includes it.
    Mirrors enrich_job_postings._relevant_skill_names_for_domain / extract_skills_sql."""
    out: Set[str] = set()
    for s in skills():
        if s.get("vertical") == domain or domain in (s.get("also_in") or []):
            out.add(s["skill_name"])
    return out
