"""
Fidelity test for the role_category taxonomy extraction (config/role_taxonomy.json).

Proves the JSON-backed loader reproduces the legacy hardcoded classifier EXACTLY:

  1. Behavior fixture — for 12k real job titles, the wired
     _classify_by_title_heuristic() (now sourcing rules from the JSON) returns the
     identical role_category the legacy hardcoded rules produced. The fixture's
     `legacy_category` was captured from the pre-refactor code by
     scripts/build_role_taxonomy_json.py.

  2. LLM vocabulary — every subcategory slug the loader feeds the LLM prompt is a
     known role for that domain, and the data-title pre-check still has all 20 rules.

Run:  python python/test_role_taxonomy.py   (exits non-zero on any mismatch)
CI-friendly: no DB, no network.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import role_taxonomy  # noqa: E402
import enrich_job_postings as enrich  # noqa: E402

FIXTURE = os.path.join(HERE, "_role_taxonomy_fixture.json")


def test_behavior_fixture():
    cases = json.load(open(FIXTURE, encoding="utf-8"))
    mismatches = []
    for c in cases:
        title, domain, expected = c["title"], c["domain"], c["legacy_category"]
        got = enrich._classify_by_title_heuristic((title or "").lower(), domain)
        if got != expected:
            mismatches.append((title, domain, expected, got))
    total = len(cases)
    print(f"[behavior] {total - len(mismatches)}/{total} titles match legacy classification")
    if mismatches:
        print(f"  {len(mismatches)} MISMATCHES (showing up to 20):")
        for title, domain, exp, got in mismatches[:20]:
            print(f"    {domain:12} {exp!r:24} != {got!r:24} | {title[:60]}")
    assert not mismatches, f"{len(mismatches)} classification mismatches vs legacy"


def test_llm_vocab_known():
    bad = []
    for domain in role_taxonomy.domains():
        known = {r["slug"] for r in role_taxonomy.roles_by_domain()[domain]}
        for sc in role_taxonomy.llm_subcategories(domain):
            if sc["slug"] not in known:
                bad.append((domain, sc["slug"]))
    print(f"[llm-vocab] checked {len(role_taxonomy.domains())} domains; {len(bad)} unknown subcat slugs")
    assert not bad, f"LLM subcategory slugs not in role registry: {bad}"


def test_data_title_precheck_intact():
    pats = role_taxonomy.data_title_patterns()
    print(f"[precheck] data_ml title pre-check has {len(pats)} rules")
    assert len(pats) == 20, f"expected 20 data-title pre-check rules, got {len(pats)}"
    assert any(slug == "auto_subcat" for _, slug in pats), "auto_subcat rule missing"


if __name__ == "__main__":
    test_behavior_fixture()
    test_llm_vocab_known()
    test_data_title_precheck_intact()
    print("\nALL ROLE-TAXONOMY FIDELITY TESTS PASSED")
