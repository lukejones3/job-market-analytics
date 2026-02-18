#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.roi_recall_v4_${ts}"
echo "🧾 Backup: ${FILE}.bak.roi_recall_v4_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

def patch_roi_calc(fn_src: str, fn_name: str) -> str:
    # find def block
    m = re.search(rf"(?s)(^def\s+{re.escape(fn_name)}\s*\(.*?\):\n)(.*?)(?=^def\s|\Z)", fn_src, re.M)
    if not m:
        raise SystemExit(f"❌ Could not find function: {fn_name}()")
    head, body = m.group(1), m.group(2)

    # inside the for-loop, locate the req_freq/pref_freq lines
    m_freq = re.search(
        r"(?m)^(?P<ind>\s+)req_freq\s*=\s*\(req_jobs\s*/\s*total_jobs\)\s*if\s*total_jobs\s*else\s*0\.0\s*$\n"
        r"(?P=ind)pref_freq\s*=\s*\(pref_jobs\s*/\s*total_jobs\)\s*if\s*total_jobs\s*else\s*0\.0\s*$\n",
        body
    )
    if not m_freq:
        raise SystemExit(f"❌ Could not find req_freq/pref_freq lines in {fn_name}()")

    ind = m_freq.group("ind")
    start_calc = m_freq.end()

    # find the INSERT cur.execute in this loop (first one in the function)
    m_exec = re.search(r"(?m)^\s*cur\.execute\(\s*\"\"\"\s*$", body[start_calc:])
    if not m_exec:
        raise SystemExit(f"❌ Could not find cur.execute(\"\"\" after freq lines in {fn_name}()")
    exec_i = start_calc + m_exec.start()

    # replace everything between freq block and cur.execute(""" with our canonical block
    new_calc = (
        f"\n{ind}# baseline/lift\n"
        f"{ind}b_req_freq, b_pref_freq = base.get(sid, (0.0, 0.0))\n"
        f"{ind}lift_req = req_freq - b_req_freq\n"
        f"{ind}lift_pref = pref_freq - b_pref_freq\n\n"
        f"{ind}# demand: required dominates preferred\n"
        f"{ind}demand = (1.0 * req_freq) + (0.35 * pref_freq)\n"
        f"{ind}# rarity: use BASELINE frequency when available; cap to avoid over-rewarding ultra-rare skills\n"
        f"{ind}base_freq = (b_req_freq + b_pref_freq) if baseline_job_ids else (req_freq + pref_freq)\n"
        f"{ind}rarity = 1.0 / ((base_freq + eps) ** 0.5)\n"
        f"{ind}rarity = min(rarity, 3.0)\n"
        f"{ind}roi = demand * rarity\n\n"
    )

    body2 = body[:start_calc] + new_calc + body[exec_i:]
    return fn_src[:m.start()] + head + body2 + fn_src[m.end():]

# ROI v2 patch in both functions (robust, comment-insensitive)
s = patch_roi_calc(s, "write_market_skill_stats")
s = patch_roi_calc(s, "write_market_skill_stats_matched")
print("✅ ROI v2 patched (baseline-based rarity + cap) in both market stats functions.")

# Extraction recall v2 patch: replace (B) block between markers in resume_to_skills()
m = re.search(r"(?s)(^def\s+resume_to_skills\s*\(.*?\):\n)(.*?)(?=^def\s|\Z)", s, re.M)
if not m:
    raise SystemExit("❌ Could not find resume_to_skills()")
head, body = m.group(1), m.group(2)

b0 = body.find("# (B) skills section parsing")
c0 = body.find("# (C) Optional heuristic toolish tokens")
if b0 == -1 or c0 == -1 or c0 <= b0:
    raise SystemExit("❌ Could not locate (B)/(C) markers inside resume_to_skills().")

before = body[:b0]
after = body[c0:]

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
    # Example: "Power-BI" -> "power bi"; "A/B Testing" -> "a b testing"
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

if "token_variants" in body[b0:c0] and "variant_set" in body[b0:c0] and "alias_map" in body[b0:c0]:
    print("ℹ️ Extraction recall v2 already present; left (B) unchanged.")
    body2 = body
else:
    body2 = before + new_b + after
    print("✅ Extraction recall v2 patched (B) block.")

s = s[:m.start()] + head + body2 + s[m.end():]
p.write_text(s, encoding="utf-8")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
