#!/usr/bin/env python3
"""
Bootstrap: extract domain-classification data from vertical_taxonomy.VERTICALS
(the last thing left in that file) into config/domain_taxonomy.json.

Per domain: label (display_name), description, and the ordered title `patterns`
used by classify_domain to assign `domain`. The dead `overlaps` are dropped.

After this, vertical_taxonomy.py is retired. Run once from repo root:
    .venv/bin/python scripts/build_domain_taxonomy_json.py
"""
import ast
import json
import os

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VTAX = os.path.join(REPO, "python", "vertical_taxonomy.py")
OUT = os.path.join(REPO, "config", "domain_taxonomy.json")


def main():
    src = open(VTAX, encoding="utf-8").read()
    ns = {"re": __import__("re")}
    for node in ast.parse(src).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id == "VERTICALS" for t in node.targets
        ):
            exec(compile(ast.Module([node], []), VTAX, "exec"), ns)
    VERTICALS = ns["VERTICALS"]

    domains = {}
    for dkey, d in VERTICALS.items():
        domains[dkey] = {
            "label": d.get("display_name", dkey),
            "description": d.get("description", ""),
            "patterns": list(d.get("patterns", [])),
        }

    out = {
        "_note": "Canonical DOMAIN taxonomy — single source of truth for the `domain` "
                 "column. patterns: ordered title regex fragments (OR-joined, case-insensitive) "
                 "compiled by classify_domain. label/description drive UI + prompts. "
                 "Generated once by scripts/build_domain_taxonomy_json.py; hand-maintained after.",
        "domains": domains,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.write("\n")
    print(f"wrote {OUT}: {len(domains)} domains, "
          f"{sum(len(d['patterns']) for d in domains.values())} total patterns")


if __name__ == "__main__":
    main()
