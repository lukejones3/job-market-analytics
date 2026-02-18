#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.last5_$ts"

python - <<'PY'
from pathlib import Path
import re

path = Path("python/enrich_job_postings.py")
s = path.read_text(encoding="utf-8")

# Grab the record_skill_candidates function block
m = re.search(r"(?ms)^def\s+record_skill_candidates\s*\(.*?\):\n(.*?)(?=^def\s|\Z)", s)
if not m:
    raise SystemExit("❌ record_skill_candidates() not found.")

block = s[m.start():m.end()]

if "LAST5_PATCH_v1" in block:
    print("ℹ️ last5 patch already applied; no changes.")
    raise SystemExit(0)

# 1) Add ACRONYM_ALLOW/BLOCK just after in_excluded_section = False
anchor1 = "in_excluded_section = False"
if anchor1 not in block:
    raise SystemExit("❌ Could not find 'in_excluded_section = False' anchor.")

insert_acronyms = r'''
    # --- LAST5_PATCH_v1 ---
    # Keep only useful acronyms (otherwise TSA/FBI/etc noise floods candidates)
    ACRONYM_ALLOW = {
        "bi","kpi","etl","elt","api","erp","hris","wfm","ga4","seo","ppc","cro",
        "ml","ai","nlp","aws","gcp","git","pca","glm","dax","json","sql","ui","ux"
    }
    ACRONYM_BLOCK = {"tsa","fbi","eeo","ada","doi","eod","eom"}
'''
block = block.replace(anchor1, anchor1 + insert_acronyms, 1)

# 2) Insert extra filters after the ignore check (line 55-56 area in your snippet)
anchor2 = "if norm in ignore:\n                  continue"
# But indentation must match your file; so we search more flexibly.
anchor2_re = re.search(r"(?m)^\s*if\s+norm\s+in\s+ignore\s*:\s*\n\s*continue\s*$", block)
if not anchor2_re:
    raise SystemExit("❌ Could not find 'if norm in ignore: continue' inside record_skill_candidates().")

# Determine body indentation for that section
anchor_line = anchor2_re.group(0).splitlines()[0]
indent = re.match(r"^(\s*)", anchor_line).group(1)

extra_filters = (
    "\n"
    f"{indent}# extra filters: strip generic / legal / geo junk\n"
    f"{indent}if re.fullmatch(r\"[a-z]{{1,2}}\", norm):  # il/ca/etc\n"
    f"{indent}    continue\n"
    f"{indent}if re.fullmatch(r\"(yes|no|and|or|the|a|an|our|your|their|you|we)\", norm):\n"
    f"{indent}    continue\n"
    f"{indent}if re.fullmatch(r\"(education|learning|knowledge|clients|training|retirement|benefits|salary|compensation)\", norm):\n"
    f"{indent}    continue\n"
    f"{indent}if re.fullmatch(r\"(gender|religion|race|creed|pregnancy|ancestry|marital status|national origin|sexual orientation|genetic information)\", norm):\n"
    f"{indent}    continue\n"
    f"{indent}if re.fullmatch(r\"(usa|us|u\\.s\\.|china|japan|india|canada|uk)\", norm):\n"
    f"{indent}    continue\n"
    f"{indent}if norm.startswith(\"age over\"):\n"
    f"{indent}    continue\n"
    f"{indent}if norm.endswith(\" skills\"):\n"
    f"{indent}    continue\n"
    "\n"
    f"{indent}# acronym rules (avoid TSA/FBI/etc)\n"
    f"{indent}if re.fullmatch(r\"[a-z]{{2,6}}\\d{{0,2}}\", norm):\n"
    f"{indent}    if norm in ACRONYM_BLOCK:\n"
    f"{indent}        continue\n"
    f"{indent}    if norm not in ACRONYM_ALLOW:\n"
    f"{indent}        continue\n"
)

# Insert right after the ignore-check block
block = block[:anchor2_re.end()] + extra_filters + block[anchor2_re.end():]

# Write back
s2 = s[:m.start()] + block + s[m.end():]
path.write_text(s2, encoding="utf-8")
print("✅ Applied last5 patch to record_skill_candidates().")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK. Backup: $FILE.bak.last5_$ts"
