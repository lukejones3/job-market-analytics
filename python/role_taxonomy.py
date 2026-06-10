"""
Single source of truth loader for the role_category taxonomy.

Reads config/role_taxonomy.json and exposes the structures the classifiers need,
so the role vocabulary lives in ONE place (the JSON) instead of being hardcoded
in enrich_job_postings.py, llm_client.py, vertical_taxonomy.py, and the frontend.

To add or rename a role: edit config/role_taxonomy.json. Nothing else.

Consumers:
  enrich_job_postings.py  -> build_title_rules(), cross_domain_fallbacks()
  llm_client.py           -> data_title_patterns(), llm_subcategory_block(domain),
                             role_domains() / is_known_domain()
  scripts/gen_verticals_ts.py (frontend codegen) -> roles_by_domain(), domain_label()
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "config", "role_taxonomy.json",
)


def _path() -> str:
    return os.getenv("ROLE_TAXONOMY_PATH", _DEFAULT_PATH)


@lru_cache(maxsize=1)
def _data() -> dict:
    with open(_path(), encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    """Drop cached taxonomy (tests / hot-edit)."""
    _data.cache_clear()
    build_title_rules.cache_clear()
    cross_domain_fallbacks.cache_clear()
    data_title_patterns.cache_clear()


def domains() -> List[str]:
    return list(_data()["domains"].keys())


def is_known_domain(domain: Optional[str]) -> bool:
    return bool(domain) and domain in _data()["domains"]


def domain_label(domain: str) -> str:
    return _data()["domains"].get(domain, {}).get("label", domain)


@lru_cache(maxsize=1)
def build_title_rules() -> Dict[str, List[Tuple[re.Pattern, str]]]:
    """{domain: [(compiled_pattern, slug)]} — ordered, drives the no-LLM heuristic.
    Replaces enrich_job_postings._build_title_rules()."""
    out: Dict[str, List[Tuple[re.Pattern, str]]] = {}
    for domain, d in _data()["domains"].items():
        out[domain] = [
            (re.compile(r["pattern"], re.IGNORECASE), r["slug"])
            for r in d.get("title_rules", [])
        ]
    return out


@lru_cache(maxsize=1)
def cross_domain_fallbacks() -> List[Tuple[re.Pattern, str]]:
    """Ordered [(compiled_pattern, slug)] applied after per-domain rules miss."""
    return [
        (re.compile(r["pattern"], re.IGNORECASE), r["slug"])
        for r in _data().get("cross_domain_fallbacks", [])
    ]


@lru_cache(maxsize=1)
def data_title_patterns() -> List[Tuple[str, str]]:
    """data_ml unambiguous title pre-check: ordered [(pattern_str, slug)].
    Replaces llm_client.DATA_TITLE_PATTERNS (slug 'auto_subcat' resolved by caller)."""
    return [
        (r["pattern"], r["slug"])
        for r in _data()["domains"].get("data_ml", {}).get("title_precheck", [])
    ]


def llm_subcategories(domain: str) -> List[dict]:
    """Ordered [{slug, hint}] for the LLM prompt of `domain`."""
    return _data()["domains"].get(domain, {}).get("llm_subcategories", [])


def llm_subcategory_block(domain: str) -> str:
    """Formatted 'SUBCATEGORIES' lines for a domain prompt.
    Bespoke domains carry hints (`- "slug": hint`); generic domains list bare slugs."""
    lines = []
    for sc in llm_subcategories(domain):
        slug, hint = sc.get("slug"), (sc.get("hint") or "").strip()
        lines.append(f'- "{slug}": {hint}' if hint else f'- "{slug}"')
    return "\n".join(lines)


def roles_by_domain() -> Dict[str, List[dict]]:
    """{domain: [{slug, label}]} for frontend codegen and UI label lookups."""
    out: Dict[str, List[dict]] = {}
    for domain, d in _data()["domains"].items():
        out[domain] = [{"slug": s, "label": meta["label"]} for s, meta in d.get("roles", {}).items()]
    return out


def role_label(slug: str) -> Optional[str]:
    """Human label for a role_category slug, searched across all domains."""
    for d in _data()["domains"].values():
        meta = d.get("roles", {}).get(slug)
        if meta:
            return meta["label"]
    return None
