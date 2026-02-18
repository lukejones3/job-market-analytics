#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.sectiongate_$ts"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# --- find record_skill_candidates() ---
m = re.search(r"(?ms)^def\s+record_skill_candidates\s*\(.*?\):\n(.*?)(?=^\S)", s)
if not m:
    raise SystemExit("❌ Could not find record_skill_candidates() in enrich_job_postings.py")

func_block = m.group(0)

# If already section-gated, don't double patch
if "SECTION_GATING_PATCH_v1" in func_block:
    print("ℹ️ record_skill_candidates already patched. No changes.")
    raise SystemExit(0)

# We will replace the *inside* of record_skill_candidates by injecting gating logic,
# while keeping your existing extraction helpers (like _extract_candidate_phrases_from_line, etc.)
# We look for the main "for line in lines:" loop and wrap extraction under capture_enabled.

# 1) Ensure we have lines list and ignore set inside function (your earlier patch has these)
if "lines =" not in func_block or "ignore" not in func_block:
    raise SystemExit("❌ record_skill_candidates() structure not recognized (missing lines/ignore). Paste the function if needed.")

# 2) Inject gating regex + capture flag near top of function (after ignore set is safest)
insert_anchor = None
# Prefer inserting after "ignore = " line
m_ignore = re.search(r"(?m)^\s*ignore\s*=\s*set\(\[.*?\]\)\s*$", func_block)
if m_ignore:
    insert_anchor = m_ignore.end()
else:
    # fallback: after "ignore = set(" (multiline)
    m_ignore2 = re.search(r"(?ms)^\s*ignore\s*=\s*set\(\[\s*.*?\s*\]\)\s*$", func_block)
    if m_ignore2:
        insert_anchor = m_ignore2.end()

if insert_anchor is None:
    raise SystemExit("❌ Could not locate ignore=set([...]) inside record_skill_candidates().")

gating_block = """

    # --- SECTION_GATING_PATCH_v1 ---
    # Only capture candidate skills inside real skill/qualification sections.
    # This eliminates benefits/EEO/company boilerplate noise.

    START_SECTION_RE = re.compile(
        r"\\b(qualifications|requirements|what you (?:will )?have|what you'll have|what you bring|"
        r"skills|technical skills|preferred qualifications|preferred|nice to have|must have)\\b",
        re.IGNORECASE
    )

    STOP_SECTION_RE = re.compile(
        r"\\b(benefits|compensation|pay|salary|equal opportunity|eeo|affirmative action|"
        r"disability|veteran|race|religion|gender|sexual orientation|national origin|"
        r"about (?:us|the company)|who we are|our values|privacy|legal|disclaimer)\\b",
        re.IGNORECASE
    )

    # Acronym handling
    ACRONYM_ALLOW = set([
        "bi","kpi","etl","elt","api","erp","hris","wfm","ga4","seo","ppc","cro",
        "ml","ai","nlp","aws","gcp","qa","ui","ux","git","pca","glm"
    ])
    ACRONYM_BLOCK = set(["tsa","fbi","eeo","ada","doi"])

"""

func_block2 = func_block[:insert_anchor] + gating_block + func_block[insert_anchor:]


# 3) Patch the loop: find "for line in lines:" inside record_skill_candidates and gate extraction
# We replace the first occurrence within the function block.
loop_pat = r"(?m)^\s*for\s+ln\s+in\s+lines:\s*$"
m_loop = re.search(loop_pat, func_block2)
if not m_loop:
    # maybe variable name is "line"
    loop_pat = r"(?m)^\s*for\s+line\s+in\s+lines:\s*$"
    m_loop = re.search(loop_pat, func_block2)
if not m_loop:
    raise SystemExit("❌ Could not find the for-loop over lines in record_skill_candidates().")

# Determine loop variable name
loop_line = m_loop.group(0)
var = "ln" if "for ln in lines" in loop_line else "line"

# We now inject capture_enabled logic at the top of the loop
loop_inject = f"""{loop_line}
        # toggle capture based on section headers
        if STOP_SECTION_RE.search({var}):
            capture_enabled = False
            continue
        if START_SECTION_RE.search({var}):
            capture_enabled = True
            # keep scanning next lines; header itself may contain junk
            continue

        if not capture_enabled:
            continue
"""

# Need to ensure capture_enabled is defined before loop.
# Insert "capture_enabled = False" before the loop inside function.
# We'll inject it right before the loop line in func_block2.
func_block3 = func_block2[:m_loop.start()] + "    capture_enabled = False\n\n" + func_block2[m_loop.start():]
func_block3 = func_block3.replace(loop_line, loop_inject, 1)


# 4) Tighten candidate acceptance: after normalized_text is computed, add acronym allow/block checks + generic word rejects.
# We'll look for "norm in ignore" check and insert after it.
anchor_pat = r"(?m)^\s*if\s+norm\s+in\s+ignore:\s*\n\s*continue\s*$"
m_anchor = re.search(anchor_pat, func_block3)
if not m_anchor:
    raise SystemExit("❌ Could not find 'if norm in ignore: continue' in record_skill_candidates().")

extra_filters = r"""

            # extra filters: kill generic nouns + boilerplate fragments
            if re.fullmatch(r"[a-z]{1,2}", norm):
                continue
            if re.fullmatch(r"(yes|no|and|or|the|a|an|our|your|their|you|we)", norm):
                continue
            if re.fullmatch(r"(education|learning|knowledge|clients|training|retirement|benefits|salary|compensation)", norm):
                continue
            if norm.endswith(" skills"):
                continue

            # acronym rules
            if re.fullmatch(r"[a-z]{2,6}\d{0,2}", norm):
                if norm in ACRONYM_BLOCK:
                    continue
                # allow only known acronyms; otherwise skip to avoid TSA/FBI/etc noise
                if norm not in ACRONYM_ALLOW and not _looks_like_tool_or_hard_skill(norm):
                    continue
"""

func_block4 = func_block3[:m_anchor.end()] + extra_filters + func_block3[m_anchor.end():]

# 5) Replace original function block in file
s2 = s[:m.start()] + func_block4 + s[m.end():]
p.write_text(s2, encoding="utf-8")
print("✅ Patched record_skill_candidates() with section gating + acronym allow/block + generic cleanup.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK. Backup: $FILE.bak.sectiongate_$ts"
