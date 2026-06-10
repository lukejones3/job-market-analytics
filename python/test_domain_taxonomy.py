"""
Fidelity / integrity test for the domain taxonomy (config/domain_taxonomy.json).

1. Pattern golden — the OR-joined compiled title regex per domain must match the
   frozen golden (_domain_pattern_golden.json). The golden was captured at migration
   time and was verified byte-identical to the legacy vertical_taxonomy.VERTICALS
   patterns (and 12,000/12,000 real titles produced identical domain matches).
   Guards against drift in the JSON or loader. Pure: no DB, no network.

2. Structure — 8 domains, each with a label and non-empty patterns.

Run:  python python/test_domain_taxonomy.py
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import domain_taxonomy as dt  # noqa: E402

GOLDEN = os.path.join(HERE, "_domain_pattern_golden.json")
EXPECTED_DOMAINS = {"data_ml", "engineering", "finance", "marketing", "product", "sales", "design", "ops"}


def test_pattern_golden():
    golden = json.load(open(GOLDEN, encoding="utf-8"))
    got = {k: p.pattern for k, p in dt.compiled_patterns().items()}
    mismatches = [k for k in set(golden) | set(got) if golden.get(k) != got.get(k)]
    print(f"[golden] {len(got)} domains; {len(mismatches)} pattern mismatches")
    if mismatches:
        for k in mismatches[:5]:
            print(f"  {k}: golden vs got differ")
    assert not mismatches, "domain title patterns drifted from golden"


def test_structure():
    domains = dt.domains()
    print(f"[structure] domains={domains}")
    assert set(domains) == EXPECTED_DOMAINS, f"unexpected domain set: {domains}"
    for d in domains:
        assert dt.label(d), f"{d} missing label"
        assert dt.patterns(d), f"{d} has no patterns"


if __name__ == "__main__":
    test_pattern_golden()
    test_structure()
    print("\nALL DOMAIN-TAXONOMY FIDELITY TESTS PASSED")
