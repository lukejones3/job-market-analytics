#!/usr/bin/env python3
"""
Codegen: regenerate the role-option constants in lander/lib/verticals.ts from
config/role_taxonomy.json (single source of truth — Option A delivery).

Only the `*_ROLES` constant block is regenerated, between the GENERATED markers.
Everything else in verticals.ts (types, ROLES_BY_VERTICAL, exported functions,
vertical display labels) is preserved untouched.

Run from the backend repo root after editing the taxonomy:
    .venv/bin/python scripts/gen_verticals_ts.py
Then commit lander/lib/verticals.ts in the lander repo.
"""
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "python"))
import role_taxonomy  # noqa: E402

TARGET = os.path.join(os.path.dirname(REPO), "lander", "lib", "verticals.ts")

# domain key -> TS const name (must match ROLES_BY_VERTICAL in verticals.ts)
CONST_NAME = {
    "data_ml": "DATA_ML_ROLES",
    "engineering": "ENGINEERING_ROLES",
    "finance": "FINANCE_ROLES",
    "marketing": "MARKETING_ROLES",
    "product": "PRODUCT_ROLES",
    "sales": "SALES_ROLES",
    "design": "DESIGN_ROLES",
    "ops": "OPS_ROLES",
}

BEGIN = "// === GENERATED ROLE OPTIONS — DO NOT EDIT BY HAND ==="
END = "// === END GENERATED ROLE OPTIONS ==="
START_ANCHOR = "export type VerticalRoleOption = { slug: string; label: string };\n"
END_ANCHOR = "const ROLES_BY_VERTICAL"


def ts_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def build_block() -> str:
    rbd = role_taxonomy.roles_by_domain()
    lines = [
        BEGIN,
        "// Source of truth: job-market-analytics/config/role_taxonomy.json",
        "// Regenerate: .venv/bin/python scripts/gen_verticals_ts.py (backend repo)",
        "",
    ]
    for domain, const in CONST_NAME.items():
        roles = rbd.get(domain, [])
        lines.append(f"const {const}: VerticalRoleOption[] = [")
        for i, r in enumerate(roles):
            comma = "," if i < len(roles) - 1 else ""
            lines.append(f'  {{ slug: "{ts_escape(r["slug"])}", label: "{ts_escape(r["label"])}" }}{comma}')
        lines.append("];")
        lines.append("")
    lines.append(END)
    return "\n".join(lines)


def main():
    if not os.path.exists(TARGET):
        sys.exit(f"target not found: {TARGET}")
    txt = open(TARGET, encoding="utf-8").read()

    si = txt.index(START_ANCHOR) + len(START_ANCHOR)
    ei = txt.index(END_ANCHOR)
    block = build_block()
    new = txt[:si] + "\n" + block + "\n\n" + txt[ei:]

    if new == txt:
        print("verticals.ts already up to date")
        return
    open(TARGET, "w", encoding="utf-8").write(new)
    print(f"regenerated role options in {TARGET}")
    for domain, const in CONST_NAME.items():
        print(f"  {const}: {len(role_taxonomy.roles_by_domain().get(domain, []))} roles")


if __name__ == "__main__":
    main()
