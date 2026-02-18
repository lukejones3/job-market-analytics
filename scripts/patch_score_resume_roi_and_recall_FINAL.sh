#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.roi_recall_FINAL_${ts}"
echo "🧾 Backup: ${FILE}.bak.roi_recall_FINAL_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# ------------------------------------------------------------
# 1) Patch resume_to_skills(): replace ONLY (B) block
#    From: "# (B) skills section parsing" up to right before "# (C) Optional heuristic"
# ------------------------------------------------------------
m_fn = re.search(r"(?s)def\s+resume_to_skills\([^)]*\):\n(.*?)\n\s*return\s+len\(found\)\n", s)
if not m_fn:
    raise SystemExit("❌ Could not locate resume_to_skills()")

fn_block = s[m_fn.start():m_fn.end()]

b_start = fn_block.find("# (B) skills section parsing")
c_start = fn_block.find("# (C) Optional heuristic")
if b_start == -1 or c_start == -1 or c_start < b_start:
    raise SystemExit("❌ Could not locate (B) and/or (C) markers inside resume_to_skills()")

# Detect indent inside function (we want the indent of section_lines line)
m_indent = re.search(r"(?m)^(\s*)section_lines\s*=\s*extract_skill_sections\(resume_text\)\s*$", fn_block)
if not m_indent:
    raise SystemExit("❌ Could not find 'section_lines = extract_skill_sections(resume_text)' inside resume_to_skills()")
IND = m_indent.group(1)

# If already upgraded, don't reapply
if "token_variants" in fn_block[b_start:c_start] and "variant_set" in fn_block[b_start:c_start] and "alias_map" in fn_block[b_start:c_start]:
    print("ℹ️ resume_to_skills(B) already has variant/bulk mapping; skipping (B) patch.")
else:
    new_b = f"""{IND}# (B) skills section parsing (great for resumes) — bulk variant-aware alias mapping
{IND}section_lines = extract_skill_sections(resume_text)

{IND}# Collect raw section tokens (preserve original token as evidence)
{IND}section_tokens = []
{IND}for ln in section_lines:
{IND}    for tok in split_skill_list_line(ln):
{IND}        tok = clean_text(tok)
{IND}        if tok:
{IND}            section_tokens.append(tok)

{IND}def _variants(tok_norm: str):
{IND}    # Produce a small, safe set of variants to improve recall without exploding false positives.
{IND}    v = []
{IND}    base = tok_norm.strip()
{IND}    if not base:
{IND}        return v
{IND}    v.append(base)
{IND}    # collapse spaces
{IND}    if " " in base:
{IND}        v.append(base.replace(" ", ""))
{IND}    # remove dots: node.js -> nodejs
{IND}    if "." in base:
{IND}        v.append(base.replace(".", ""))
{IND}    # replace separators with spaces then normalize
{IND}    sep_to_space = re.sub(r"[\\-/]", " ", base)
{IND}    sep_to_space = re.sub(r"\\s+", " ", sep_to_space).strip()
{IND}    if sep_to_space and sep_to_space != base:
{IND}        v.append(sep_to_space)
{IND}        if " " in sep_to_space:
{IND}            v.append(sep_to_space.replace(" ", ""))
{IND}    # strip most punctuation but keep + and # (c++, c#)
{IND}    stripped = re.sub(r"[^a-z0-9\\+\\# ]+", "", base)
{IND}    stripped = re.sub(r"\\s+", " ", stripped).strip()
{IND}    if stripped and stripped != base:
{IND}        v.append(stripped)
{IND}        if " " in stripped:
{IND}            v.append(stripped.replace(" ", ""))
{IND}    # de-dupe while preserving order
{IND}    out = []
{IND}    seen = set()
{IND}    for x in v:
{IND}        if x and x not in seen:
{IND}            seen.add(x)
{IND}            out.append(x)
{IND}    return out

{IND}hard_safe = {{ "r", "c", "c++", "c#", "go", "sql", "etl", "aws", "gcp", "bi", "ai", "ml", "api" }}

{IND}token_variants = {{}}
{IND}variant_set = set()
{IND}for tok in section_tokens:
{IND}    tn = normalize_for_matching(tok)
{IND}    vars_ = _variants(tn)
{IND}    # skip risky tiny variants unless hard-safe
{IND}    vars2 = []
{IND}    for vv in vars_:
{IND}        if len(vv) <= 2 and vv not in hard_safe:
{IND}            continue
{IND}        vars2.append(vv)
{IND}        variant_set.add(vv)
{IND}    token_variants[tok] = vars2

{IND}alias_map = {{}}
{IND}if variant_set:
{IND}    cur.execute(\"\"\"
{IND}      SELECT lower(alias_text) AS a, skill_id
{IND}      FROM skill_aliases
{IND}      WHERE lower(alias_text) = ANY(%s)
{IND}    \"\"\", (list(variant_set),))
{IND}    for r in cur.fetchall():
{IND}        alias_map[r["a"]] = r["skill_id"]

{IND}for tok in section_tokens:
{IND}    sid = None
{IND}    for vv in token_variants.get(tok, []):
{IND}        sid = alias_map.get(vv)
{IND}        if sid:
{IND}            break
{IND}    if sid:
{IND}        conf = 0.88
{IND}        evidence = tok
{IND}        prev = found.get(sid, (0, "", ""))
{IND}        if conf > prev[0]:
{IND}            found[sid] = (conf, evidence, "section")
"""

    fn_block2 = fn_block[:b_start] + new_b + "\n" + fn_block[c_start:]
    s = s[:m_fn.start()] + fn_block2 + s[m_fn.end():]
    print("✅ Patched resume_to_skills(): (B) is now bulk + variant-aware (higher recall, faster).")

# ------------------------------------------------------------
# 2) ROI v2 patch in BOTH write_market_skill_stats functions
#    - rarity based on BASELINE prevalence (when available), else market prevalence
#    - cap roi to avoid tiny-sample weirdness
# ------------------------------------------------------------
def patch_roi_in_function(src: str, fn_name: str) -> str:
    m = re.search(rf"(?s)def\s+{re.escape(fn_name)}\([^)]*\):\n(.*?)\n(?=def\s|\Z)", src)
    if not m:
        raise SystemExit(f"❌ Could not locate {fn_name}()")

    block = src[m.start():m.end()]

    # If already patched, skip
    if "rarity_base" in block and "roi_cap" in block:
        print(f"ℹ️ ROI v2 already present in {fn_name}(); skipping.")
        return src

    # Replace the 3-line demand/rarity/roi pattern if present.
    # We’ll be flexible: find demand line then next rarity+roi lines.
    # This function currently has:
    #   demand = (1.0 * req_freq) + (0.35 * pref_freq)
    #   rarity = 1.0 / ((req_freq + pref_freq + eps) ** 0.5)
    #   roi = demand * rarity
    pat = re.compile(
        r"(?m)^(?P<ind>\s*)demand\s*=\s*\(1\.0\s*\*\s*req_freq\)\s*\+\s*\(0\.35\s*\*\s*pref_freq\)\s*\n"
        r"(?P=ind)rarity\s*=\s*1\.0\s*/\s*\(\(req_freq\s*\+\s*pref_freq\s*\+\s*eps\)\s*\*\*\s*0\.5\)\s*\n"
        r"(?P=ind)roi\s*=\s*demand\s*\*\s*rarity\s*\n"
    )
    mm = pat.search(block)
    if not mm:
        raise SystemExit(f"❌ ROI patch: could not find expected demand/rarity/roi block in {fn_name}()")

    ind = mm.group("ind")
    repl = (
        f"{ind}demand = (1.0 * req_freq) + (0.35 * pref_freq)\n\n"
        f"{ind}# ROI v2: rarity based on baseline prevalence when available (more stable than market-only)\n"
        f"{ind}rarity_base = (b_req_freq + b_pref_freq) if baseline_job_ids else (req_freq + pref_freq)\n"
        f"{ind}rarity = 1.0 / ((rarity_base + eps) ** 0.5)\n"
        f"{ind}roi = demand * rarity\n"
        f"{ind}roi_cap = 0.999999999\n"
        f"{ind}if roi > roi_cap:\n"
        f"{ind}    roi = roi_cap\n"
    )

    block2 = block[:mm.start()] + repl + block[mm.end():]
    src2 = src[:m.start()] + block2 + src[m.end():]
    print(f"✅ ROI v2 patched in {fn_name}()")
    return src2

s = patch_roi_in_function(s, "write_market_skill_stats")
s = patch_roi_in_function(s, "write_market_skill_stats_matched")

p.write_text(s, encoding="utf-8")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
