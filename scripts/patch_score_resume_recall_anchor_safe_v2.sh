#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.recall_anchor_v2_${ts}"
echo "🧾 Backup: ${FILE}.bak.recall_anchor_v2_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# Grab resume_to_skills() block
m = re.search(r"(?s)(^def\s+resume_to_skills\s*\(.*?\):\n)(.*?)(?=^def\s|\Z)", s, re.M)
if not m:
    raise SystemExit("❌ Could not find resume_to_skills()")

head, body = m.group(1), m.group(2)

# If already present, stop
if all(k in body for k in ["section_tokens", "token_variants", "variant_set", "alias_map"]):
    print("ℹ️ Recall upgrade already present. No changes.")
    raise SystemExit(0)

# Start anchor: section_lines = extract_skill_sections(...)
m_start = re.search(r"(?m)^(?P<indent>[ \t]*)section_lines\s*=\s*extract_skill_sections\([^\)]*\)\s*$", body)
if not m_start:
    raise SystemExit("❌ Could not find anchor line: section_lines = extract_skill_sections(...)")

indent = m_start.group("indent")

# End anchor: toolish = set()
m_end = re.search(r"(?m)^" + re.escape(indent) + r"toolish\s*=\s*set\(\)\s*$", body)
if not m_end:
    raise SystemExit("❌ Could not find anchor line: toolish = set()")

before = body[:m_start.start()]
after  = body[m_end.start():]

tmpl = """
__IND__section_lines = extract_skill_sections(resume_text)

__IND__# Collect raw tokens from the skills section
__IND__section_tokens = []
__IND__for ln in section_lines:
__IND__    for tok in split_skill_list_line(ln):
__IND__        if tok:
__IND__            section_tokens.append(tok)

__IND__# Build deterministic variants for higher recall (punctuation-insensitive)
__IND__variant_set = set()
__IND__token_variants = {}  # raw_tok -> [variants]
__IND__for tok in section_tokens:
__IND__    tok_norm = normalize_for_matching(tok)
__IND__    depunct = re.sub(r"[._\\-/]+", " ", tok_norm)
__IND__    depunct = re.sub(r"\\s+", " ", depunct).strip()

__IND__    vars_ = []
__IND__    if tok_norm:
__IND__        vars_.append(tok_norm)
__IND__    if depunct and depunct != tok_norm:
__IND__        vars_.append(depunct)

__IND__    # unique while preserving order
__IND__    seen = set()
__IND__    vars2 = []
__IND__    for v in vars_:
__IND__        if v not in seen:
__IND__            seen.add(v)
__IND__            vars2.append(v)

__IND__    token_variants[tok] = vars2
__IND__    for v in vars2:
__IND__        variant_set.add(v)

__IND__# Bulk map variants -> skill_id
__IND__alias_map = {}
__IND__if variant_set:
__IND__    cur.execute(\"\"\"
__IND__      SELECT lower(alias_text) AS a, skill_id
__IND__      FROM skill_aliases
__IND__      WHERE lower(alias_text) = ANY(%s)
__IND__    \"\"\", (list(variant_set),))
__IND__    for r in cur.fetchall():
__IND__        alias_map[r["a"]] = r["skill_id"]

__IND__# Apply mapping to found[]
__IND__for tok in section_tokens:
__IND__    sid = None
__IND__    for v in token_variants.get(tok, []):
__IND__        sid = alias_map.get(v)
__IND__        if sid:
__IND__            break
__IND__    if sid:
__IND__        conf = 0.88
__IND__        evidence = tok
__IND__        prev = found.get(sid, (0, "", ""))
__IND__        if conf > prev[0]:
__IND__            found[sid] = (conf, evidence, "section")

""".lstrip("\n")

new_b = tmpl.replace("__IND__", indent)
body2 = before + new_b + after

s2 = s[:m.start()] + head + body2 + s[m.end():]
p.write_text(s2, encoding="utf-8")
print("✅ Patched resume_to_skills(): anchor-based section recall upgrade applied.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
