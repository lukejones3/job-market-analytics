#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
python - <<'PY'
from pathlib import Path
import re
from datetime import datetime

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = p.with_suffix(p.suffix + f".bak.ai_section_{ts}")
bak.write_text(s, encoding="utf-8")
print(f"🧾 Backup: {bak}")

# ------------------------------------------------------------
# (A) Insert section-extraction helpers (once), above extract_skill_priorities
# ------------------------------------------------------------
marker = "JOB_SKILL_SECTION_HEADERS"
if marker not in s:
    insert_before = re.search(r"(?m)^def\s+extract_skill_priorities\s*\(", s)
    if not insert_before:
        raise SystemExit("❌ Could not find def extract_skill_priorities(")

    helpers = r'''
# -----------------------------
# Job skill-section extraction (used to avoid AI false positives like "help of AI")
# -----------------------------
JOB_SKILL_SECTION_HEADERS = {
    "skills",
    "technical skills",
    "required skills",
    "core skills",
    "qualifications",
    "required qualifications",
    "preferred qualifications",
    "requirements",
    "what you will have",
    "what you'll have",
    "what you bring",
    "what you'll bring",
    "what we are looking for",
    "what we're looking for",
}

# Common non-skill headers that should end a skill block
JOB_NONSKILL_SECTION_HEADERS = {
    "responsibilities",
    "what you will do",
    "what you'll do",
    "job description",
    "about the job",
    "about the role",
    "about you",
    "about us",
    "benefits",
    "compensation",
    "pay range",
    "equal opportunity",
    "eeo",
    "privacy",
    "how to apply",
    "location",
}

def _is_headerish_line(raw_line: str) -> bool:
    # ALL CAPS-ish line often indicates a new section
    ln = raw_line.strip()
    if not ln:
        return False
    if re.fullmatch(r"[A-Z0-9 \-\/&(),.]{6,}", ln) and sum(c.isalpha() for c in ln) >= 4:
        return True
    return False

def extract_job_skill_section_lines(desc: str) -> list[str]:
    """
    Extract only the lines that appear under skill/qualification/requirement headers.
    Used to restrict ambiguous short aliases like 'ai' to skills sections only.
    """
    lines_raw = desc.splitlines()
    lines = [clean_text(x) for x in lines_raw]
    out = []
    in_block = False

    for raw, ln in zip(lines_raw, lines):
        low = normalize_for_matching(ln)
        if not low:
            # allow blank lines, but if we're in_block, a long blank gap ends it
            if in_block:
                # single blank line is common within lists; don't immediately end
                continue
            else:
                continue

        # start block
        if any(low == h or low.startswith(h + ":") for h in JOB_SKILL_SECTION_HEADERS):
            in_block = True
            continue

        # end block
        if in_block:
            if any(low == h or low.startswith(h + ":") for h in JOB_NONSKILL_SECTION_HEADERS):
                in_block = False
                continue
            if _is_headerish_line(raw):
                in_block = False
                continue

            out.append(ln)

    return out
'''.lstrip("\n")

    s = s[:insert_before.start()] + helpers + "\n" + s[insert_before.start():]
    print("✅ Inserted job skill-section helpers.")
else:
    print("ℹ️ Job skill-section helpers already present; skipping insert.")

# ------------------------------------------------------------
# (B) Patch extract_skill_priorities to restrict alias 'ai' to skill sections only
# ------------------------------------------------------------
func_pat = re.compile(
    r"(?s)(^def\s+extract_skill_priorities\s*\(.*?\):\n)(.*?)(^\s*return\s+out\s*$)",
    re.M
)
m = func_pat.search(s)
if not m:
    raise SystemExit("❌ Could not locate extract_skill_priorities() body for patching")

head = m.group(1)
body = m.group(2)
tail = m.group(3)

# If already patched, bail
if "extract_job_skill_section_lines" in body and "ai_section_only" in body:
    print("ℹ️ extract_skill_priorities already patched for AI section-only logic; leaving unchanged.")
else:
    # We replace the body with a clean implementation (keeps existing helpers like infer_section_priority)
    new_body = r'''
    """
    Returns: {skill_id: skill_priority}
    Default: required
    Strongest wins: required > preferred > nice-to-have

    ai_section_only: the short alias "ai" is only counted if it appears inside a skill/qual/req section block.
    """
    priority_rank = {"required": 3, "preferred": 2, "nice-to-have": 1}

    # full text lines (for normal skills)
    lines = [clean_text(x) for x in desc.splitlines()]
    lines = [x for x in lines if x]

    # skill-section-only lines (to prevent "help of AI" etc.)
    section_lines = extract_job_skill_section_lines(desc)
    section_set = set(section_lines)

    current_section = None
    out: Dict[str, str] = {}

    for line in lines:
        new_section = infer_section_priority(line)
        if new_section:
            current_section = new_section

        line_priority = detect_priority_from_line(line)
        t = normalize_for_matching(line)

        for pat, skill_id, _alias in compiled_patterns:
            # Only restrict the ambiguous short alias "ai"
            if _alias == "ai":
                if line not in section_set:
                    continue

            if pat.search(t):
                p = line_priority or current_section or "required"
                if skill_id not in out:
                    out[skill_id] = p
                else:
                    if priority_rank.get(p, 0) > priority_rank.get(out[skill_id], 0):
                        out[skill_id] = p
'''.lstrip("\n")

    s = s[:m.start()] + head + new_body + "    " + tail.strip() + "\n"
    print("✅ Patched extract_skill_priorities(): AI alias only counted inside skill sections.")

p.write_text(s, encoding="utf-8")
print("✅ Wrote patched file.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
