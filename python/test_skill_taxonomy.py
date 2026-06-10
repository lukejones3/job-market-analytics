"""
Fidelity / integrity test for the skills taxonomy (config/skill_taxonomy.json).

1. Extraction golden — recompute the full set of (vertical, skill_id, alias_pattern)
   that the JSON-driven SQL extraction (extract_skills_sql) would build, and assert it
   matches the frozen golden (_skill_extraction_golden.json). Guards against accidental
   drift in the JSON or loader. Pure: no DB, no network.

2. Structure — every skill has a skill_id; denylist + skills counts are sane.

Note on the migration: vs the pre-migration code, this set is a strict SUPERSET
(+288 alias patterns, 0 removed) because folding the VERTICALS seed aliases activates
aliases that were authored but never wired into SQL extraction. That additive delta was
reviewed at migration time; the golden freezes the intended post-migration set.

Run:  python python/test_skill_taxonomy.py
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import skill_taxonomy as st  # noqa: E402
import extract_skills_sql as ex  # noqa: E402  (real _escape_pg_regex)

GOLDEN = os.path.join(HERE, "_skill_extraction_golden.json")


def _compute_rows():
    skills = st.skills()
    id_to_json = {s["skill_id"]: s for s in skills}
    name_to_id = {st._canon_key(s["skill_name"]): s["skill_id"] for s in skills}
    deny = st.denylist()
    deny_ids = {sid for c, sid in name_to_id.items() if st._canon_norm(c) in deny}
    verticals = sorted(
        {s.get("vertical") for s in skills if s.get("vertical")}
        | {v for s in skills for v in (s.get("also_in") or [])}
    )
    rows = set()
    for domain in verticals:
        for sname in sorted(st.relevant_skill_names_for_domain(domain)):
            canon = st._canon_key(sname)
            sid = name_to_id.get(canon)
            if not sid or sid in deny_ids or st._canon_norm(canon) == "ai":
                continue
            seen, aset = set(), []
            for a in [sname] + id_to_json.get(sid, {}).get("aliases", []):
                a = re.sub(r"[ \t]+", " ", (a or "").strip())
                n = st._canon_norm(a)
                if a and n not in seen:
                    seen.add(n)
                    aset.append(a)
            for al in aset:
                rows.add((domain, sid, ex._escape_pg_regex(al.lower())))
    return rows


def test_extraction_golden():
    got = _compute_rows()
    golden = {tuple(r) for r in json.load(open(GOLDEN, encoding="utf-8"))}
    missing = golden - got
    extra = got - golden
    print(f"[golden] computed {len(got)} rows; golden {len(golden)}; "
          f"missing {len(missing)} extra {len(extra)}")
    if missing:
        print("  MISSING:", list(missing)[:10])
    if extra:
        print("  EXTRA:", list(extra)[:10])
    assert not missing and not extra, "skill extraction pattern set drifted from golden"


def test_structure():
    skills = st.skills()
    no_id = [s["skill_name"] for s in skills if not s.get("skill_id")]
    print(f"[structure] {len(skills)} skills, {len(st.denylist())} denylist terms, "
          f"{len(no_id)} missing skill_id")
    assert not no_id, f"skills missing skill_id: {no_id[:10]}"
    assert len(skills) >= 290, "unexpectedly few skills"
    assert st.denylist(), "denylist empty"


if __name__ == "__main__":
    test_extraction_golden()
    test_structure()
    print("\nALL SKILL-TAXONOMY FIDELITY TESTS PASSED")
