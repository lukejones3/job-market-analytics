"""Policy-backed role admission with description evidence and audit reasons."""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
_SCOPE_PATH = ROOT / "config" / "role_scope.json"
_TAXONOMY_PATH = ROOT / "config" / "role_taxonomy.json"
_DISCOVERY_PATH = ROOT / "config" / "discovery_queries.json"


@dataclass(frozen=True)
class ScopeDecision:
    status: str
    rule_id: str
    confidence: float
    domain: Optional[str] = None
    category: Optional[str] = None
    positive_signals: tuple[str, ...] = ()
    negative_signals: tuple[str, ...] = ()

    @property
    def admitted(self) -> bool:
        return self.status in {"accepted_core", "accepted_evidence"}

    @property
    def candidate(self) -> bool:
        return self.status != "rejected"


def _load(path: Path) -> dict:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


@lru_cache(maxsize=1)
def _compiled() -> dict:
    scope = _load(Path(os.getenv("ROLE_SCOPE_PATH", _SCOPE_PATH)))
    taxonomy = _load(_TAXONOMY_PATH)
    return {
        "hard": [(re.compile(r["pattern"], re.I), r) for r in scope["hard_exclusions"]],
        "evidence": [(re.compile(r["pattern"], re.I), r) for r in scope["evidence_rules"]],
        "core": [(re.compile(r["pattern"], re.I), r) for r in scope["core_rules"]],
        "taxonomy": [
            (re.compile(r["pattern"], re.I), {**r, "domain": domain, "id": f"taxonomy:{domain}:{r['slug']}", "category": r["slug"]})
            for domain, data in taxonomy["domains"].items()
            for r in data.get("title_rules", [])
        ],
        "fallback": [
            (re.compile(r["pattern"], re.I), {**r, "domain": "data_ml", "id": f"fallback:{r['slug']}", "category": r["slug"]})
            for r in taxonomy.get("cross_domain_fallbacks", [])
        ],
    }


def reload() -> None:
    _compiled.cache_clear()
    discovery_terms.cache_clear()


def evaluate_role(title: str, description: Optional[str] = None) -> ScopeDecision:
    title = (title or "").strip()
    description_text = (description or "").lower()
    if not title:
        return ScopeDecision("rejected", "empty_title", 1.0)

    rules = _compiled()
    for pattern, rule in rules["hard"]:
        if pattern.search(title):
            return ScopeDecision("rejected", rule["id"], 1.0)

    # Ambiguous patterns take precedence over broad legacy taxonomy matches.
    quarantine_fallback = None
    for pattern, rule in rules["evidence"]:
        if not pattern.search(title):
            continue
        positives = tuple(s for s in rule.get("required_any", []) if s in description_text)
        negatives = tuple(s for s in rule.get("negative_any", []) if s in description_text)
        minimum = int(rule.get("minimum_positive", 1))
        if rule.get("quarantine_only"):
            quarantine_fallback = ScopeDecision(
                "quarantine", rule["id"], 0.25,
                rule.get("domain"), rule.get("category")
            )
            continue
        if negatives:
            return ScopeDecision("rejected", rule["id"], 0.95, rule.get("domain"),
                                 rule.get("category"), positives, negatives)
        if len(positives) >= minimum:
            confidence = min(0.98, 0.72 + 0.06 * len(positives))
            return ScopeDecision("accepted_evidence", rule["id"], confidence,
                                 rule.get("domain"), rule.get("category"), positives)
        return ScopeDecision("quarantine", rule["id"], 0.45, rule.get("domain"),
                             rule.get("category"), positives)

    for collection in (rules["core"], rules["taxonomy"], rules["fallback"]):
        for pattern, rule in collection:
            if pattern.search(title):
                return ScopeDecision("accepted_core", rule["id"], 0.99,
                                     rule.get("domain"), rule.get("category"))

    if quarantine_fallback:
        return quarantine_fallback
    return ScopeDecision("rejected", "no_scope_match", 0.99)


def is_title_candidate(title: str) -> bool:
    """Whether a listing merits detail retrieval; includes evidence-pending titles."""
    return evaluate_role(title).candidate


@lru_cache(maxsize=1)
def discovery_terms() -> tuple[str, ...]:
    data = _load(Path(os.getenv("DISCOVERY_QUERIES_PATH", _DISCOVERY_PATH)))
    return tuple(dict.fromkeys(term for terms in data["domains"].values() for term in terms))
