#!/usr/bin/env bash
set -e

FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.candfilters_$ts"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# 1) Add stronger stopwords + blocklists (insert/replace inside existing _CANDIDATE_STOPWORDS / helpers)
# We'll patch by inserting new constants if not present.

if "_CANDIDATE_HARD_BLACKLIST" not in s:
    insert = r'''
_CANDIDATE_HARD_BLACKLIST = {
    # EEO / protected-class boilerplate
    "race","color","religion","sex","gender","gender identity","gender expression",
    "sexual orientation","national origin","ancestry","age","disability","veteran",
    "pregnancy","marital status","genetic information","citizenship","protected",
    # generic HR/legal/benefits noise
    "equal opportunity","eeo","accommodation","reasonable accommodation",
    "background check","drug free","drug-free","benefits","benefit","insurance",
    "pto","paid time off","401k","bonus","wellness","healthcare","vision","dental",
    "pay range","compensation","salary range",
    # common headings you saw
    "required qualifications","preferred qualifications","physical requirements","abilities",
}

_EXCLUDED_SECTION_HEADERS_RE = re.compile(
    r"(?i)\b("
    r"equal opportunity|eeo|diversity|inclusion|accommodation|reasonable accommodation|"
    r"background check|drug[- ]?free|benefits|what you(’|')ll get|perks|"
    r"privacy|terms|disclaimer|authorization|consent|employment eligibility|"
    r"vaccin|covid|work authorization"
    r")\b"
)

_END_SECTION_RE = re.compile(r"(?i)\b(about the job|responsibilities|what you will do|qualifications|requirements|preferred|skills)\b")
'''
    # place right after _SOFT_SKILL_HINTS definition (exists in your inserted block)
    s = re.sub(r"(_SOFT_SKILL_HINTS\s*=\s*\{[\s\S]*?\}\n)", r"\1"+insert+"\n", s, count=1)

# 2) Make _is_garbage_candidate stricter (ignore blacklist + headings)
# Find function and inject checks near top.
m = re.search(r"def _is_garbage_candidate\(t: str\) -> bool:\n([\s\S]*?)\n\s*return False", s)
if not m:
    raise SystemExit("Could not find _is_garbage_candidate to patch.")

body = m.group(0)
if "_CANDIDATE_HARD_BLACKLIST" not in body:
    body2 = body.replace(
        "if not t:",
        "if not t:\n        return True\n    if t in _CANDIDATE_HARD_BLACKLIST:\n        return True\n    if re.search(r\"\\b(equal opportunity|eeo|accommodation|benefits|privacy|disclaimer)\\b\", t):\n        return True\n"
    )
    s = s.replace(body, body2)

# 3) Tighten record_skill_candidates: track excluded sections and skip lines inside them
# Patch record_skill_candidates function.
m2 = re.search(r"def record_skill_candidates\(cur, job_id: str, desc: str, compiled_patterns\):\n([\s\S]*?)\n\s*for ln in skillish_lines:", s)
if not m2:
    raise SystemExit("Could not find record_skill_candidates() block to patch.")

block = m2.group(0)
if "in_excluded_section" not in block:
    # Insert excluded-section tracking before building skillish_lines loop
    patch = r'''
    in_excluded_section = False

    # detect and skip entire EEO/benefits/legal sections
    def _toggle_excluded(line_norm: str) -> bool:
        # Start excluded if header matches and NOT immediately a skills header
        if _EXCLUDED_SECTION_HEADERS_RE.search(line_norm) and not re.search(r"(?i)\b(qualifications|requirements|skills)\b", line_norm):
            return True
        return False
'''
    block2 = block.replace("    # Focus on likely skill-heavy lines", patch + "\n    # Focus on likely skill-heavy lines")

    # Now modify the line collection loop to respect excluded sections
    # Replace: for ln in lines[:200]:
    block2 = re.sub(
        r"for ln in lines\[:200\]:\s*# cap to avoid insane scans",
        "for ln in lines[:300]:  # cap to avoid insane scans",
        block2
    )

    # Inject exclusion logic inside loop right after low = normalize_for_matching(ln)
    block2 = block2.replace(
        "        low = normalize_for_matching(ln)",
        "        low = normalize_for_matching(ln)\n"
        "        # enter excluded section\n"
        "        if _toggle_excluded(low):\n"
        "            in_excluded_section = True\n"
        "        # exit excluded section if we hit a major new header\n"
        "        if in_excluded_section and _END_SECTION_RE.search(low) and not _EXCLUDED_SECTION_HEADERS_RE.search(low):\n"
        "            in_excluded_section = False\n"
        "        if in_excluded_section:\n"
        "            continue\n"
    )

    s = s.replace(block, block2)

# 4) Also: only treat lines as skillish if they contain skill/tool-ish signals or bullets,
# but NOT generic HR headers.
# Add a small filter near where skillish_lines gets appended.
if "skillish_lines.append(ln)" in s and "tool|tech|stack" not in s:
    s = s.replace(
        'if any(k in low for k in ["requirements", "qualifications", "skills", "preferred", "must have", "nice to have"]):',
        'if any(k in low for k in ["requirements", "qualifications", "skills", "preferred", "must have", "nice to have", "tools", "tech", "stack", "technology"]):'
    )

p.write_text(s, encoding="utf-8")
print("✅ Patched candidate filters: excluded EEO/benefits/legal sections + blacklist.")
PY

python -m py_compile python/enrich_job_postings.py
echo "✅ Compiles. Backup: $FILE.bak.candfilters_$ts"
