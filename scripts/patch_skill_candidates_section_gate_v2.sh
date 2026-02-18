#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.sectiongate_v2_$ts"

python - <<'PY'
from pathlib import Path
import re

path = Path("python/enrich_job_postings.py")
lines = path.read_text(encoding="utf-8").splitlines(True)

def find_func_block(lines, func_name):
    # returns (start_idx, end_idx, indent)
    pat = re.compile(rf"^def\s+{re.escape(func_name)}\s*\(")
    for i, ln in enumerate(lines):
        if pat.search(ln):
            indent = len(ln) - len(ln.lstrip(" "))
            # find end: first non-blank line with indentation <= indent and not a decorator/continuation
            j = i + 1
            while j < len(lines):
                lj = lines[j]
                if lj.strip() == "":
                    j += 1
                    continue
                cur_indent = len(lj) - len(lj.lstrip(" "))
                if cur_indent <= indent and not lj.lstrip().startswith(("#", "@")):
                    break
                j += 1
            return i, j, indent
    return None

blk = find_func_block(lines, "record_skill_candidates")
if not blk:
    raise SystemExit("❌ Could not find def record_skill_candidates(...).")

start, end, indent = blk
block = lines[start:end]
block_text = "".join(block)

if "SECTION_GATING_PATCH_v2" in block_text:
    print("ℹ️ Already patched (v2). No changes.")
    raise SystemExit(0)

# Helper: insert after docstring (if exists) else after def line
def insert_after_docstring(block, base_indent):
    # block[0] is def line
    i = 1
    # skip blank lines
    while i < len(block) and block[i].strip() == "":
        i += 1
    if i < len(block) and re.match(r'^\s*("""|\'\'\')', block[i]):
        quote = '"""' if '"""' in block[i] else "'''"
        i += 1
        while i < len(block) and quote not in block[i]:
            i += 1
        if i < len(block):  # include closing line
            i += 1
    return i  # insertion index within block

ins_at = insert_after_docstring(block, indent)
pad = " " * (indent + 4)

gating_block = [
    f"{pad}# --- SECTION_GATING_PATCH_v2 ---\n",
    f"{pad}# Only capture candidates inside real skill/qualification sections.\n",
    f"{pad}START_SECTION_RE = re.compile(\n",
    f"{pad}    r\"\\b(qualifications|requirements|what you (?:will )?have|what you'll have|what you bring|\"\n",
    f"{pad}    r\"skills|technical skills|preferred qualifications|preferred|nice to have|must have)\\b\",\n",
    f"{pad}    re.IGNORECASE\n",
    f"{pad})\n",
    f"{pad}STOP_SECTION_RE = re.compile(\n",
    f"{pad}    r\"\\b(benefits|compensation|pay|salary|equal opportunity|eeo|affirmative action|\"\n",
    f"{pad}    r\"disability|veteran|race|religion|gender|sexual orientation|national origin|\"\n",
    f"{pad}    r\"about (?:us|the company)|who we are|our values|privacy|legal|disclaimer)\\b\",\n",
    f"{pad}    re.IGNORECASE\n",
    f"{pad})\n",
    f"{pad}ACRONYM_ALLOW = set([\n",
    f"{pad}    \"bi\",\"kpi\",\"etl\",\"elt\",\"api\",\"erp\",\"hris\",\"wfm\",\"ga4\",\"seo\",\"ppc\",\"cro\",\n",
    f"{pad}    \"ml\",\"ai\",\"nlp\",\"aws\",\"gcp\",\"qa\",\"ui\",\"ux\",\"git\",\"pca\",\"glm\",\"dax\",\"json\"\n",
    f"{pad}])\n",
    f"{pad}ACRONYM_BLOCK = set([\"tsa\",\"fbi\",\"eeo\",\"ada\",\"doi\"])  # common noise acronyms\n",
    "\n",
]

block = block[:ins_at] + gating_block + block[ins_at:]

block_text = "".join(block)

# Insert capture_enabled=False just before the first for-loop over lines
m_for = re.search(r"(?m)^\s*for\s+(\w+)\s+in\s+lines:\s*$", block_text)
if not m_for:
    raise SystemExit("❌ Could not find `for <var> in lines:` inside record_skill_candidates().")

loop_var = m_for.group(1)

# add capture_enabled = False right before loop line
block_text = re.sub(
    r"(?m)^(\s*)for\s+" + re.escape(loop_var) + r"\s+in\s+lines:\s*$",
    r"\1capture_enabled = False\n\n\1for " + loop_var + " in lines:",
    block_text,
    count=1
)

# Add gating logic at top of loop body (after loop line)
# We’ll find the loop line again and inject immediately after it.
loop_line_pat = rf"(?m)^(\s*)for\s+{re.escape(loop_var)}\s+in\s+lines:\s*$"
m_loopline = re.search(loop_line_pat, block_text)
if not m_loopline:
    raise SystemExit("❌ Could not re-find loop line to inject gating.")

loopline_end = m_loopline.end()
loop_indent = m_loopline.group(1)
body_pad = loop_indent + " " * 4

gating_inside = (
    f"\n{body_pad}# section gating\n"
    f"{body_pad}if STOP_SECTION_RE.search({loop_var}):\n"
    f"{body_pad}    capture_enabled = False\n"
    f"{body_pad}    continue\n"
    f"{body_pad}if START_SECTION_RE.search({loop_var}):\n"
    f"{body_pad}    capture_enabled = True\n"
    f"{body_pad}    continue\n"
    f"{body_pad}if not capture_enabled:\n"
    f"{body_pad}    continue\n"
)

block_text = block_text[:loopline_end] + gating_inside + block_text[loopline_end:]

# Insert extra filters:
# Preferred anchor: after "if norm in ignore: continue" (any whitespace/indentation)
anchor = re.search(r"(?ms)^\s*if\s+norm\s+in\s+ignore\s*:\s*\n\s*continue\s*$", block_text)
extra_filters = (
    "\n"
    f"{body_pad}# extra filters: kill generic nouns + boilerplate fragments\n"
    f"{body_pad}if re.fullmatch(r\"[a-z]{{1,2}}\", norm):\n"
    f"{body_pad}    continue\n"
    f"{body_pad}if re.fullmatch(r\"(yes|no|and|or|the|a|an|our|your|their|you|we)\", norm):\n"
    f"{body_pad}    continue\n"
    f"{body_pad}if re.fullmatch(r\"(education|learning|knowledge|clients|training|retirement|benefits|salary|compensation)\", norm):\n"
    f"{body_pad}    continue\n"
    f"{body_pad}if norm.endswith(\" skills\"):\n"
    f"{body_pad}    continue\n"
    "\n"
    f"{body_pad}# acronym rules (avoid TSA/FBI/etc)\n"
    f"{body_pad}if re.fullmatch(r\"[a-z]{{2,6}}\\d{{0,2}}\", norm):\n"
    f"{body_pad}    if norm in ACRONYM_BLOCK:\n"
    f"{body_pad}        continue\n"
    f"{body_pad}    if norm not in ACRONYM_ALLOW and not _looks_like_tool_or_hard_skill(norm):\n"
    f"{body_pad}        continue\n"
)

if anchor:
    block_text = block_text[:anchor.end()] + extra_filters + block_text[anchor.end():]
else:
    # Fallback anchor: right after "for raw, norm in _extract_candidate_phrases_from_line"
    anchor2 = re.search(r"(?m)^\s*for\s+raw\s*,\s*norm\s+in\s+_extract_candidate_phrases_from_line\(.*\):\s*$", block_text)
    if not anchor2:
        raise SystemExit("❌ Could not find a safe insertion point for extra filters (no ignore check, no raw/norm loop).")
    block_text = block_text[:anchor2.end()] + extra_filters + block_text[anchor2.end():]

# Write back: replace the original block in file
new_lines = lines[:start] + block_text.splitlines(True) + lines[end:]
path.write_text("".join(new_lines), encoding="utf-8")
print("✅ Patched record_skill_candidates(): section gating + acronym rules + generic cleanup (v2).")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK. Backup: $FILE.bak.sectiongate_v2_$ts"
