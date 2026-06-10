#!/usr/bin/env python3
"""
Bootstrap: build config/skill_taxonomy.json — the single source of truth for skills.

Consolidates everything currently hardcoded or DB-resident:
  - DB skills table        (authoritative: skill_id, slug, vertical, also_in, category,
                            skill_group, difficulty_relevant)   [decision A: JSON = full DB]
  - DB skill_aliases       (runtime alias index)
  - vertical_taxonomy.VERTICALS[*].skills  (aliases, category, weight, also_in seed)
  - enrich_job_postings.FALLBACK_ALIASES   (hardcoded alias dict — folded in)
  - enrich_job_postings.SKILL_DENYLIST     (hardcoded noise list — folded in)

Inputs (DB dumps, pull once via psql row_to_json):
  /tmp/skills_dump.json          [{skill_id, skill_name, skill_slug, vertical, also_in,
                                    category, skill_group, difficulty_relevant}]
  /tmp/skill_aliases_dump.json   [{skill_id, alias_text}]

Output:
  config/skill_taxonomy.json

Also REPORTS the extraction delta: aliases that exist in VERTICALS but were NOT in the
legacy SQL-extraction active set (FALLBACK + DB). Folding them in is the single behavior
change of this migration — surfaced here so it can be approved before the cron re-extracts.
"""
import ast
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENRICH = os.path.join(REPO, "python", "enrich_job_postings.py")
VTAX = os.path.join(REPO, "python", "vertical_taxonomy.py")
SKILLS_DUMP = os.environ.get("SKILLS_DUMP", "/tmp/skills_dump.json")
ALIASES_DUMP = os.environ.get("ALIASES_DUMP", "/tmp/skill_aliases_dump.json")
OUT = os.path.join(REPO, "config", "skill_taxonomy.json")


def _canon_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip())


def _canon_norm(s: str) -> str:
    return re.sub(r"\s+", " ", _canon_key(s).lower()).strip()


def _isolated(path, names):
    src = open(path, encoding="utf-8").read()
    wanted = []
    for node in ast.parse(src).body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted.append(node)
        elif isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in names for t in node.targets
        ):
            wanted.append(node)
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) \
                and node.target.id in names:
            wanted.append(node)
    ns = {"re": re, "Dict": dict, "List": list}
    # provide typing names used in annotations of extracted assigns
    import typing
    ns.update({k: getattr(typing, k) for k in ("Dict", "List")})
    exec(compile(ast.Module(body=wanted, type_ignores=[]), path, "exec"), ns)
    return ns


def main():
    if not (os.path.exists(SKILLS_DUMP) and os.path.exists(ALIASES_DUMP)):
        sys.exit(f"missing DB dumps: {SKILLS_DUMP} / {ALIASES_DUMP}")
    db_skills = json.load(open(SKILLS_DUMP, encoding="utf-8"))
    db_aliases = json.load(open(ALIASES_DUMP, encoding="utf-8"))

    enrich_ns = _isolated(ENRICH, {"FALLBACK_ALIASES", "SKILL_DENYLIST"})
    FALLBACK = enrich_ns["FALLBACK_ALIASES"]
    DENYLIST = enrich_ns["SKILL_DENYLIST"]

    vtax_ns = _isolated(VTAX, {"VERTICALS"})
    VERTICALS = vtax_ns["VERTICALS"]
    # canon_key -> VERTICALS skill meta
    vskills = {}
    for vdata in VERTICALS.values():
        for name, meta in vdata.get("skills", {}).items():
            vskills[_canon_key(name)] = meta

    # DB aliases grouped by skill_id
    db_alias_by_id = {}
    for a in db_aliases:
        db_alias_by_id.setdefault(a["skill_id"], []).append(a["alias_text"])

    def dedup(seq):
        out, seen = [], set()
        for x in seq:
            x = re.sub(r"[ \t]+", " ", (x or "").strip())
            n = _canon_norm(x)
            if x and n not in seen:
                seen.add(n)
                out.append(x)
        return out

    skills_out = []
    delta_patterns = 0
    delta_skills = 0
    unmapped_vertical = []

    for s in db_skills:
        sid = s["skill_id"]
        name = s["skill_name"]
        canon = _canon_key(name)
        vmeta = vskills.get(canon, {})

        # legacy extraction-active aliases = name + FALLBACK[canon] + DB[skill_id] (deduped)
        legacy_active = dedup([name] + list(FALLBACK.get(canon, [])) + db_alias_by_id.get(sid, []))
        # full union also folds VERTICALS aliases (latently unused by SQL extraction today)
        vertical_aliases = list(vmeta.get("aliases", []))
        full_union = dedup(legacy_active + vertical_aliases)

        extra = [a for a in full_union if _canon_norm(a) not in {_canon_norm(x) for x in legacy_active}]
        if extra:
            delta_skills += 1
            delta_patterns += len(extra)

        skills_out.append({
            "skill_id": sid,
            "skill_name": name,
            "skill_slug": s.get("skill_slug"),
            "vertical": s.get("vertical"),
            "also_in": s.get("also_in") or [],
            "category": s.get("category"),
            "skill_group": s.get("skill_group"),
            "difficulty_relevant": s.get("difficulty_relevant"),
            "weight": vmeta.get("weight", 1),
            "aliases": full_union,
        })

    # VERTICALS skills with no matching DB skill_id (dead for extraction today)
    db_canons = {_canon_key(s["skill_name"]) for s in db_skills}
    for canon in vskills:
        if canon not in db_canons:
            unmapped_vertical.append(canon)

    out = {
        "_note": "Canonical skills taxonomy — single source of truth. Generated once by "
                 "scripts/build_skill_taxonomy_json.py from the live DB + VERTICALS + FALLBACK_ALIASES; "
                 "hand-maintained thereafter. discover_skills.py may still add skills to the DB at "
                 "runtime (dynamic); this JSON is the seed/floor. 'aliases' folds DB + FALLBACK + "
                 "VERTICALS seed aliases. 'denylist' = generic terms never extracted.",
        "denylist": sorted(DENYLIST),
        "skills": skills_out,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"wrote {OUT}: {len(skills_out)} skills, {len(out['denylist'])} denylist terms")
    print(f"EXTRACTION DELTA (folding VERTICALS seed aliases): "
          f"+{delta_patterns} alias patterns across {delta_skills} skills")
    if unmapped_vertical:
        print(f"VERTICALS skills with no DB skill_id ({len(unmapped_vertical)}), excluded: "
              f"{', '.join(sorted(unmapped_vertical)[:15])}{' ...' if len(unmapped_vertical) > 15 else ''}")


if __name__ == "__main__":
    main()
