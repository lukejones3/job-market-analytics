#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.recall_anchor_${ts}"
echo "🧾 Backup: ${FILE}.bak.recall_anchor_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# Locate resume_to_skills() block
m = re.search(r"(?s)(^def\s+resume_to_skills\s*\(.*?\):\n)(.*?)(?=^def\s|\Z)", s, re.M)
if not m:
    raise SystemExit("❌ Could not find resume_to_skills()")

head, body = m.group(1), m.group(2)

# If already patched, stop
if "token_variants" in body and "variant_set" in body and "alias_map" in body and "section_tokens" in body:
    print("ℹ️ recall already present (token_variants/variant_set/alias_map found). No changes.")
    raise SystemExit(0)

# Anchor lines (indent-aware)
m_start = re.search(r"(?m)^(?P<indent>\s*)section_lines\s*=\s*extract_skill_sections\(resume_text\)\s*$", body)
if not m_start:
    raise SystemExit("❌ Could not find anchor: section_lines = extract_skill_sections(resume_text)")

indent = m_start.group("indent")

m_end = re.search(r"(?m)^" + re.escape(indent) + r"toolish\s*=\s*set\(\)\s*$", body)
if not m_end:
    raise SystemExit("❌ Could not find anchor: toolish = set()")

# Replace everything from section_lines assignment up to (but not including) toolish = set()
before = body[:m_start.start()]
after  = body[m_end.start():]

new_b = f"""{indent}section_lines = extract_skill_sections(resume_text)

{indent}# Collect raw tokens from the skills section
{indent}section_tokens = []
{indent}for ln in section_lines:
{indent}    for tok in split_skill_list_line(ln):
{indent}        if tok:
{indent}            section_tokens.append(tok)

{indent}# Build deterministic variants for higher recall (punctuation-insensitive)
{indent}variant_set = set()
{indent}token_variants = {{}}  # raw_tok -> [variants]
{indent}for tok in section_tokens:
{indent}    tok_norm = normalize_for_matching(tok)
{indent}    depunct = re.sub(r"[._\\-/]+", " ", tok_norm)
{indent}    depunct = re.sub(r"\\s+", " ", depunct).strip()

{indent}    vars_ = []
{indent}    if tok_norm:
{indent}        vars_.append(tok_norm)
{indent}    if depunct and depunct != tok_norm:
{indent}        vars_.append(depunct)

{indent}    # unique while preserving order
{indent}    seen = set()
{indent}    vars2 = []
{indent}    for v in vars_:
{indent}        if v not in seen:
{indent}            seen.add(v)
{indent}            vars2.append(v)

{indent}    token_variants[tok] = vars2
{indent}    for v in vars2:
{indent}        variant_set.add(v)

{indent}# Bulk map variants -> skill_id
{indent}alias_map = {{}}
{indent}if variant_set:
{indent}    cur.execute(\"\"\"
{indent}      SELECT lower(alias_text) AS a, skill_id
{indent}      FROM skill_aliases
{indent}      WHERE lower(alias_text) = ANY(%s)
{indent}    \"\"\", (list(variant_set),))
{indent}    for r in cur.fetchall():
{indent}        alias_map[r["a"]] = r["skill_id"]

{indent}# Apply mapping to found[]
{indent}for tok in section_tokens:
{indent}    sid = None
{indent}    for v in token_variants.get(tok, []):
{indent}        sid = alias_map.get(v)
{indent}        if sid:
{indent}            break
{indent}    if sid:
{indent}        conf = 0.88
{indent}        evidence = tok
{indent}        prev = found.get(sid, (0, "", ""))
{indent}        if conf > prev[0]:
{indent}            found[sid] = (conf, evidence, "section")

"""

body2 = before + new_b + after
s2 = s[:m.start()] + head + body2 + s[m.end():]

p.write_text(s2, encoding="utf-8")
print("✅ Patched resume_to_skills(): anchor-based recall upgrade (skills section variants + bulk alias map).")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
