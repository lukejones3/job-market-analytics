"""
Single source of truth loader for the domain taxonomy (config/domain_taxonomy.json).

Replaces vertical_taxonomy.VERTICALS for domain classification: the per-domain title
`patterns` (compiled by classify_domain to assign the `domain` column) plus domain
labels/descriptions. This is the last taxonomy migrated out of vertical_taxonomy.py,
which is now retired.

Consumers:
  classify_domain.py      -> compiled_patterns(), domains()
  extract_skills_sql.py / seed_new_skills.py / backfill_skill_verticals.py -> domains()
  scripts/gen_verticals_ts.py (frontend) -> label()
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Dict, List

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "domain_taxonomy.json",
)


def _path() -> str:
    return os.getenv("DOMAIN_TAXONOMY_PATH", _DEFAULT_PATH)


@lru_cache(maxsize=1)
def _data() -> dict:
    with open(_path(), encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    _data.cache_clear()
    compiled_patterns.cache_clear()


def domains() -> List[str]:
    return list(_data()["domains"].keys())


def label(domain: str) -> str:
    return _data()["domains"].get(domain, {}).get("label", domain)


def description(domain: str) -> str:
    return _data()["domains"].get(domain, {}).get("description", "")


def patterns(domain: str) -> List[str]:
    return list(_data()["domains"].get(domain, {}).get("patterns", []))


@lru_cache(maxsize=1)
def compiled_patterns() -> Dict[str, re.Pattern]:
    """{domain: compiled OR-joined title regex}. Mirrors the old
    classify_domain._compile_patterns() built from VERTICALS[*].patterns."""
    out: Dict[str, re.Pattern] = {}
    for dkey, d in _data()["domains"].items():
        parts = [f"(?:{p})" for p in d.get("patterns", [])]
        out[dkey] = re.compile("|".join(parts), re.IGNORECASE)
    return out
