#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.recall_v4b_${ts}"
echo "🧾 Backup: ${FILE}.bak.recall_v4b_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

m = re.search(r"(?s)(^def\s+resume_to_skills\s*\(.*?\):\n)(.*?)(?=^def\s|\Z)", s, re.M)
if not m:
    raise SystemExit("❌ Could not find resume_to_skills()")

head, body = m.group(1), m.group(2)

# Find (B) and (C) blocks by regex, not exact text
mB = re.search(r"(?m)^\s*#\s*\(B\).*?$", body)
mC = re.search(r"(?m)^\s*#\s*\(C\).*?$", body)

if not mB or not mC or mC.start() <= mB.start():
    raise SystemExit("❌ Could not locate (B)/(C) headers inside resume_to_skills() (regex).")

before = body[:mB.start()]
after  = body[mC.start():]

# If already present, bail
if "variant_set" in body[mB.start():mC.start()] and "token_variants" in body[mB.start():mC.start()] and "alias_map" in body[mB.start():mC.start()]:
    print("ℹ️ Extraction recall v2 already present; no changes.")
    raise SystemExit(0)

new_b = r'''# (B) skills section parsing (great for resumes)
    # Improve recall by matching punctuation/format variants against skill_aliases.
    section_lines = extract_skill_sections(resume_text)

    # Collect tokens (raw) from section
    section_tokens = []
    for ln in section_lines:
        for tok in split_skill_list_line(ln):
            if tok:
                section_tokens.append(tok)

    # Build variants for exact alias matching (deterministic, low false-positive risk)
    variant_set = set()
    token_variants = {}  # raw_tok -> [variants]
    for tok in section_tokens:
        tok_norm = normalize_for_matching(tok)
        depunct = re.sub(r"[._\-/]+", " ", tok_norm)
        depunct = re.sub(r"\s+", " ", depunct).strip()

        vars_ = []
        if tok_norm:
            vars_.append(tok_norm)
        if depunct and depunct != tok_norm:
            vars_.append(depunct)

        # unique while preserving order
        seen = set()
        vars2 = []
        for v in vars_:
            if v not in seen:
                seen.add(v)
                vars2.append(v)

        token_variants[tok] = vars2
        for v in vars2:
            variant_set.add(v)

    alias_map = {}
    if variant_set:
        cur.execute("""
          SELECT lower(alias_text) AS a, skill_id
          FROM skill_aliases
          WHERE lower(alias_text) = ANY(%s)
        """, (list(variant_set),))
        for r in cur.fetchall():
            alias_map[r["a"]] = r["skill_id"]

    # Apply mapping
    for tok in section_tokens:
        sid = None
        for v in token_variants.get(tok, []):
            sid = alias_map.get(v)
            if sid:
                break
        if sid:
            conf = 0.88
            evidence = tok
            prev = found.get(sid, (0, "", ""))
            if conf > prev[0]:
                found[sid] = (conf, evidence, "section")

'''.rstrip() + "\n\n"

body2 = before + new_b + after

s2 = s[:m.start()] + head + body2 + s[m.end():]
p.write_text(s2, encoding="utf-8")
print("✅ Patched resume_to_skills(): (B) block variant_set/token_variants/alias_map recall boost.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
