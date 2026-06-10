#!/usr/bin/env python3
"""
One-time bootstrap: extract the role-classification vocabulary that is currently
hardcoded across three modules and emit a single canonical JSON.

Sources consolidated (all byte-faithful to today):
  1. enrich_job_postings.py  _build_title_rules()      -> per-domain ordered title regex -> slug
                             _classify_by_title_heuristic cross-domain fallbacks
  2. llm_client.py           DATA_TITLE_PATTERNS         -> data_ml LLM title pre-check
                             _PROMPT_DATA_ML/ENG/SALES   -> bespoke LLM subcategory hints
  3. vertical_taxonomy.py    VERTICALS                   -> domain label/description + generic subcats
  4. lander/lib/verticals.ts *_ROLES                     -> human display labels per slug

Outputs:
  config/role_taxonomy.json            canonical single source of truth
  python/_role_taxonomy_fixture.json   {title, domain, legacy_category} over real DB titles,
                                        used by test_role_taxonomy.py to prove the loader
                                        reproduces legacy classification exactly.

Run once from repo root:  .venv/bin/python scripts/build_role_taxonomy_json.py
After this, config/role_taxonomy.json is hand-maintained; this script is provenance.
"""
import ast
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENRICH = os.path.join(REPO, "python", "enrich_job_postings.py")
LLM = os.path.join(REPO, "python", "llm_client.py")
VTAX = os.path.join(REPO, "python", "vertical_taxonomy.py")
FRONTEND_VERTICALS = os.path.join(os.path.dirname(REPO), "lander", "lib", "verticals.ts")
OUT_JSON = os.path.join(REPO, "config", "role_taxonomy.json")
OUT_FIXTURE = os.path.join(REPO, "python", "_role_taxonomy_fixture.json")


def _isolated(path, names):
    """Exec only the named top-level Assign/FunctionDef nodes from `path`,
    in a fresh namespace with `re` available. Returns the namespace."""
    src = open(path, encoding="utf-8").read()
    mod = ast.parse(src)
    wanted = []
    for node in mod.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            wanted.append(node)
        elif isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id in names for t in node.targets):
                wanted.append(node)
    ns = {"re": re, "_TITLE_RULES": {}}
    exec(compile(ast.Module(body=wanted, type_ignores=[]), path, "exec"), ns)
    return ns


def humanize(slug):
    special = {
        "fpa": "FP&A", "sre": "SRE", "qa": "QA / SDET", "bdr_sdr": "SDR / BDR",
        "devops": "DevOps", "ux_research": "UX Research", "ai_research": "AI Research",
        "technical_pm": "Technical PM", "people_ops": "People Ops",
        "marketing_ops": "Marketing Ops", "sales_ops": "Sales / RevOps",
        "data_analytics": "Data Analyst / BI", "ml_engineering": "ML Engineer",
        "data_engineering": "Data Engineer", "data_science": "Data Scientist",
        "analytics_engineering": "Analytics Engineer", "fullstack": "Full-stack",
    }
    if slug in special:
        return special[slug]
    return slug.replace("_", " ").title()


def parse_prompt_hints(prompt_text):
    """Parse a bespoke LLM prompt's SUBCATEGORIES block into ordered [(slug, hint)]."""
    lines = prompt_text.splitlines()
    start = end = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("SUBCATEGORIES"):
            start = i + 1
        elif start is not None and ln.strip().startswith("RULES"):
            end = i
            break
    block = lines[start:end]
    out = []
    cur_slug = None
    cur_parts = []
    head = re.compile(r'^-\s*"([a-z_]+)":\s*(.*)$')
    for ln in block:
        m = head.match(ln.strip())
        if m:
            if cur_slug is not None:
                out.append((cur_slug, re.sub(r"\s+", " ", " ".join(cur_parts)).strip()))
            cur_slug, cur_parts = m.group(1), [m.group(2)]
        elif cur_slug is not None and ln.strip():
            cur_parts.append(ln.strip())
    if cur_slug is not None:
        out.append((cur_slug, re.sub(r"\s+", " ", " ".join(cur_parts)).strip()))
    return out


def parse_frontend_labels(path):
    """slug -> label from lander/lib/verticals.ts ({ slug: "x", label: "y" })."""
    labels = {}
    if not os.path.exists(path):
        print(f"  WARN: frontend verticals not found at {path}", file=sys.stderr)
        return labels
    txt = open(path, encoding="utf-8").read()
    for m in re.finditer(r'\{\s*slug:\s*"([^"]+)",\s*label:\s*"([^"]+)"\s*\}', txt):
        labels[m.group(1)] = m.group(2)
    return labels


def main():
    enrich_ns = _isolated(ENRICH, {"_build_title_rules", "_classify_by_title_heuristic"})
    title_rules = enrich_ns["_build_title_rules"]()  # {domain: [(compiled, slug)]}

    llm_ns = _isolated(LLM, {
        "DATA_TITLE_PATTERNS", "_PROMPT_DATA_ML", "_PROMPT_ENGINEERING", "_PROMPT_SALES",
    })
    data_precheck = llm_ns["DATA_TITLE_PATTERNS"]  # [(pattern_str, slug)]
    bespoke_hints = {
        "data_ml": parse_prompt_hints(llm_ns["_PROMPT_DATA_ML"]),
        "engineering": parse_prompt_hints(llm_ns["_PROMPT_ENGINEERING"]),
        "sales": parse_prompt_hints(llm_ns["_PROMPT_SALES"]),
    }

    vtax_ns = _isolated(VTAX, {"VERTICALS"})
    VERTICALS = vtax_ns["VERTICALS"]

    frontend_labels = parse_frontend_labels(FRONTEND_VERTICALS)

    # Cross-domain fallbacks live in _classify_by_title_heuristic after the rule loop.
    # Mirror them explicitly (verified against source); the fixture test guards correctness.
    cross_domain_fallbacks = [
        {"slug": "ml_engineering",
         "pattern": r"\bai\s+engineer\b|\bml\s+engineer\b|\bmachine\s+learning\s+engineer\b"},
        {"slug": "data_engineering", "pattern": r"\bdata\s+engineer\b"},
        {"slug": "data_science", "pattern": r"\bdata\s+scientist\b"},
        {"slug": "data_analytics", "pattern": r"\bdata\s+analyst\b"},
    ]

    domains = {}
    for dkey, dmeta in VERTICALS.items():
        rules = title_rules.get(dkey, [])
        rule_list = [{"slug": slug, "pattern": pat.pattern} for pat, slug in rules]

        # LLM subcategory list: bespoke (data_ml/engineering/sales) else VERTICALS subcats (hintless).
        if dkey in bespoke_hints:
            llm_subcats = [{"slug": s, "hint": h} for s, h in bespoke_hints[dkey]]
        else:
            llm_subcats = [{"slug": s, "hint": ""} for s in dmeta.get("subcategories", [])]

        # Registry of every slug a consumer actually emits for this domain -> human label.
        # = title-rule slugs (heuristic) UNION llm_subcategory slugs (prompt). This excludes
        # phantom VERTICALS subcats (e.g. engineering 'infra'/'hardware') that nothing emits.
        slugs = []
        for s in [r["slug"] for r in rule_list] + [c["slug"] for c in llm_subcats]:
            if s not in slugs:
                slugs.append(s)
        roles = {s: {"label": frontend_labels.get(s, humanize(s))} for s in slugs}

        domain_obj = {
            "label": dmeta.get("display_name", dkey),
            "description": dmeta.get("description", ""),
            "roles": roles,
            "title_rules": rule_list,
            "llm_subcategories": llm_subcats,
        }
        if dkey == "data_ml":
            domain_obj["title_precheck"] = [
                {"slug": slug, "pattern": pat} for pat, slug in data_precheck
            ]
        domains[dkey] = domain_obj

    out = {
        "_note": "Canonical role_category taxonomy. Single source of truth. "
                 "Generated once by scripts/build_role_taxonomy_json.py; hand-maintained thereafter. "
                 "title_rules: ordered regex->slug for the no-LLM heuristic. "
                 "llm_subcategories: ordered slug+hint for the LLM prompt. "
                 "title_precheck (data_ml): unambiguous data-title LLM bypass. "
                 "roles: slug->display label (frontend + UI).",
        "domains": domains,
        "cross_domain_fallbacks": cross_domain_fallbacks,
    }

    os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUT_JSON}")

    # Behavior fixture: run legacy classifier over real DB titles (if reachable), else a static corpus.
    legacy_classify = enrich_ns["_classify_by_title_heuristic"]
    titles = load_corpus()
    fixture = []
    for title, domain in titles:
        rl = (title or "").lower()
        fixture.append({"title": title, "domain": domain,
                        "legacy_category": legacy_classify(rl, domain)})
    with open(OUT_FIXTURE, "w", encoding="utf-8") as f:
        json.dump(fixture, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUT_FIXTURE} ({len(fixture)} cases)")


CORPUS_TSV = os.path.join(REPO, "python", "_role_corpus.tsv")


def load_corpus():
    """(title, domain) pairs from python/_role_corpus.tsv (real DB titles), else static."""
    if os.path.exists(CORPUS_TSV):
        rows = []
        for ln in open(CORPUS_TSV, encoding="utf-8"):
            ln = ln.rstrip("\n")
            sep = "\t" if "\t" in ln else ("\\t" if "\\t" in ln else None)
            if not ln or sep is None:
                continue
            title, domain = ln.split(sep, 1)
            rows.append((title, domain))
        if rows:
            print(f"  corpus: {len(rows)} real titles from {CORPUS_TSV}")
            return rows
    print("  (no corpus TSV; using static corpus)", file=sys.stderr)
    return STATIC_CORPUS


STATIC_CORPUS = [
    ("Senior Data Engineer", "data_ml"), ("Staff ML Engineer", "data_ml"),
    ("Account Executive", "sales"), ("Sales Manager", "sales"),
    ("Backend Engineer", "engineering"), ("Engineering Manager", "engineering"),
    ("Product Manager", "product"), ("Technical Program Manager", "product"),
    ("Brand Designer", "design"), ("UX Researcher", "design"),
    ("Staff Accountant", "finance"), ("Financial Analyst", "finance"),
    ("Growth Marketer", "marketing"), ("Content Strategist", "marketing"),
    ("Recruiter", "ops"), ("Chief of Staff", "ops"),
]

if __name__ == "__main__":
    main()
