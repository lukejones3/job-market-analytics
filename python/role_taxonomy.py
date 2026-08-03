"""Single source of truth loader for the role taxonomy."""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Dict, List, Optional, Tuple

_DEFAULT_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                             "config", "role_taxonomy.json")


def _path() -> str:
    return os.getenv("ROLE_TAXONOMY_PATH", _DEFAULT_PATH)


@lru_cache(maxsize=1)
def _data() -> dict:
    with open(_path(), encoding="utf-8") as f:
        return json.load(f)


def reload() -> None:
    _data.cache_clear()
    build_title_rules.cache_clear()
    cross_domain_fallbacks.cache_clear()
    data_title_patterns.cache_clear()
    target_title_patterns.cache_clear()


def domains() -> List[str]:
    return list(_data()["domains"].keys())


def is_known_domain(domain: Optional[str]) -> bool:
    return bool(domain) and domain in _data()["domains"]


def domain_label(domain: str) -> str:
    return _data()["domains"].get(domain, {}).get("label", domain)


@lru_cache(maxsize=1)
def build_title_rules() -> Dict[str, List[Tuple[re.Pattern, str]]]:
    return {domain: [(re.compile(rule["pattern"], re.I), rule["slug"])
            for rule in data.get("title_rules", [])]
            for domain, data in _data()["domains"].items()}


@lru_cache(maxsize=1)
def cross_domain_fallbacks() -> List[Tuple[re.Pattern, str]]:
    return [(re.compile(rule["pattern"], re.I), rule["slug"])
            for rule in _data().get("cross_domain_fallbacks", [])]


@lru_cache(maxsize=1)
def data_title_patterns() -> List[Tuple[str, str]]:
    return [(rule["pattern"], rule["slug"])
            for rule in _data()["domains"].get("data_ml", {}).get("title_precheck", [])]


def llm_subcategories(domain: str) -> List[dict]:
    return _data()["domains"].get(domain, {}).get("llm_subcategories", [])


def llm_subcategory_block(domain: str) -> str:
    lines = []
    for subcategory in llm_subcategories(domain):
        slug, hint = subcategory.get("slug"), (subcategory.get("hint") or "").strip()
        lines.append(f'- "{slug}": {hint}' if hint else f'- "{slug}"')
    return "\n".join(lines)


def roles_by_domain() -> Dict[str, List[dict]]:
    return {domain: [{"slug": slug, "label": meta["label"]}
            for slug, meta in data.get("roles", {}).items()]
            for domain, data in _data()["domains"].items()}


def role_label(slug: str) -> Optional[str]:
    for data in _data()["domains"].values():
        meta = data.get("roles", {}).get(slug)
        if meta:
            return meta["label"]
    return None


# Broad queries maximize recall; the taxonomy-backed matcher below provides the
# precise acceptance decision consistently during harvesting and validation.
SEARCH_TERMS = ("data", "analytics", "machine learning", "software engineer",
    "developer", "product", "design", "marketing", "sales", "finance",
    "accounting", "recruiter", "human resources", "operations", "project manager")


@lru_cache(maxsize=1)
def target_title_patterns() -> Tuple[re.Pattern, ...]:
    patterns = []
    for rules in build_title_rules().values():
        patterns.extend(pattern for pattern, _slug in rules)
    patterns.extend(pattern for pattern, _slug in cross_domain_fallbacks())
    return tuple(patterns)


_NON_KNOWLEDGE_WORK = re.compile(
    r"\b(?:intern|internship|warehouse|driver|cashier|nurse|physician|mechanic|"
    r"security guard|food service|retail associate)\b", re.I)


def is_target_role(title: str) -> bool:
    return bool(title and not _NON_KNOWLEDGE_WORK.search(title)
                and any(pattern.search(title) for pattern in target_title_patterns()))
