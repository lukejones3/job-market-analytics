#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.roi_recall_v3_${ts}"
echo "🧾 Backup: ${FILE}.bak.roi_recall_v3_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# ---------------------------
# 1) ROI v2 patch (baseline rarity + cap)
# ---------------------------
def patch_roi_in_fn(src: str, fn_name: str) -> str:
    m = re.search(rf"(?s)(^def\s+{re.escape(fn_name)}\s*\(.*?\):\n)(.*?)(?=^def\s|\Z)", src, re.M)
    if not m:
        raise SystemExit(f"❌ Could not find function: {fn_name}()")

    head = m.group(1)
    body = m.group(2)

    # We replace the demand/rarity/roi block and move b_req/b_pref before rarity.
    # Expected old sequence inside loop:
    #   demand = ...
    #   rarity = ...
    #   roi = ...
    #   b_req_freq, b_pref_freq = base.get(...)
    #   lift_req = ...
    #   lift_pref = ...
    #
    # We'll replace it with:
    #   b_req_freq, b_pref_freq = base.get(...)
    #   lift_req/lift_pref ...
    #   base_freq = (b_req+b_pref) if baseline_job_ids else (req+pref)
    #   demand = ...
    #   rarity = ...
    #   rarity = min(rarity, 3.0)
    #   roi = demand * rarity

    pat = re.compile(
        r"(?s)"
        r"(\n\s*)demand\s*=\s*\(1\.0\s*\*\s*req_freq\)\s*\+\s*\(0\.35\s*\*\s*pref_freq\)\s*\n"
        r"\s*#?\s*rarity:.*?\n"
        r"\s*rarity\s*=\s*1\.0\s*/\s*\(\(\s*req_freq\s*\+\s*pref_freq\s*\+\s*eps\s*\)\s*\*\*\s*0\.5\s*\)\s*\n"
        r"\s*roi\s*=\s*demand\s*\*\s*rarity\s*\n"
        r"\s*b_req_freq,\s*b_pref_freq\s*=\s*base\.get\(sid,\s*\(0\.0,\s*0\.0\)\)\s*\n"
        r"\s*lift_req\s*=\s*req_freq\s*-\s*b_req_freq\s*\n"
        r"\s*lift_pref\s*=\s*pref_freq\s*-\s*b_pref_freq\s*\n"
    )

    def repl(mo):
        indent = mo.group(1)
        return (
            f"{indent}b_req_freq, b_pref_freq = base.get(sid, (0.0, 0.0))\n"
            f"{indent}lift_req = req_freq - b_req_freq\n"
            f"{indent}lift_pref = pref_freq - b_pref_freq\n\n"
            f"{indent}# demand: required dominates preferred\n"
            f"{indent}demand = (1.0 * req_freq) + (0.35 * pref_freq)\n"
            f"{indent}# rarity: use BASELINE frequency when available; cap to avoid over-rewarding ultra-rare skills\n"
            f"{indent}base_freq = (b_req_freq + b_pref_freq) if baseline_job_ids else (req_freq + pref_freq)\n"
            f"{indent}rarity = 1.0 / ((base_freq + eps) ** 0.5)\n"
            f"{indent}rarity = min(rarity, 3.0)\n"
            f"{indent}roi = demand * rarity\n"
        )

    new_body, n = pat.subn(repl, body, count=1)
    if n == 0:
        raise SystemExit(f"❌ ROI patch: could not find expected block in {fn_name}()")

    return src[:m.start()] + head + new_body + src[m.end():]

s = patch_roi_in_fn(s, "write_market_skill_stats")
s = patch_roi_in_fn(s, "write_market_skill_stats_matched")
print("✅ ROI v2 patched in both market-stats functions.")

# ---------------------------
# 2) Extraction recall v2 patch (skills section tokens -> alias variants)
# ---------------------------
m = re.search(r"(?s)(^def\s+resume_to_skills\s*\(.*?\):\n)(.*?)(?=^def\s|\Z)", s, re.M)
if not m:
    raise SystemExit("❌ Could not find resume_to_skills()")

head = m.group(1)
body = m.group(2)

# Replace the (B) section mapping block that currently does per-token exact lookup.
# We'll build a variant set (original + depunct) and do one ANY() lookup.
# Then map each token to a skill_id if any variant matches.

# Find the start of section parsing and the end right before (C) Optional heuristic toolish tokens
b_start = body.find("# (B) skills section parsing")
c_start = body.find("# (C) Optional heuristic toolish tokens")
if b_start == -1 or c_start == -1 or c_start <= b_start:
    raise SystemExit("❌ Could not locate (B)/(C) markers inside resume_to_skills()")

before = body[:b_start]
b_block = body[b_start:c_start]
after = body[c_start:]

# New (B) block
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
    # Example: "Power-BI" -> "power bi", "PowerBI" stays "powerbi" (if present as alias)
    variant_set = set()
    token_variants = {}  # raw_tok -> [variants]
    for tok in section_tokens:
        tok_norm = normalize_for_matching(tok)
        # depunct separators that often differ between resumes vs alias table
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

# Ensure we don't double-patch
if "token_variants" in b_block and "alias_map" in b_block and "variant_set" in b_block:
    print("ℹ️ Extraction recall v2 already present; leaving (B) block unchanged.")
    new_body = body
else:
    new_body = before + new_b + after
    print("✅ Extraction recall v2 patched in resume_to_skills().")

s = s[:m.start()] + head + new_body + s[m.end():]

p.write_text(s, encoding="utf-8")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
