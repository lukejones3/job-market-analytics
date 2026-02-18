#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"

python - <<'PY'
from pathlib import Path
import re
from datetime import datetime

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# ----------------------------
# Backup
# ----------------------------
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = p.with_suffix(p.suffix + f".bak.strip_linkedin_v5_{ts}")
bak.write_text(s, encoding="utf-8")
print(f"🧾 Backup: {bak}")

# ----------------------------
# 1) Insert helper above the skill-section extraction block (stable anchor)
# ----------------------------
anchor = "# Job skill-section extraction"
if "def strip_linkedin_chrome(" not in s:
    a = s.find(anchor)
    if a == -1:
        raise SystemExit(f"❌ Could not find anchor: {anchor}")

    helper = r'''
def strip_linkedin_chrome(text: str) -> str:
    """
    Remove common LinkedIn/Chrome copy UI chrome that pollutes skill extraction.
    Conservative: only removes well-known UI lines.
    """
    if not text:
        return text

    kill_exact = {
        "share",
        "show more options",
        "apply",
        "save",
        "show match details",
        "tailor my resume",
        "help me update my profile",
        "create cover letter",
        "is this information helpful?",
        "try premium for $0",
        "people you can reach out to",
        "show all",
        "beta",
    }

    out = []
    for raw in text.splitlines():
        ln = clean_text(raw)
        low = normalize_for_matching(ln)

        if not low:
            out.append(raw)
            continue

        if low in kill_exact:
            continue
        if low.startswith("matches your job preferences"):
            continue
        if "responses managed off linkedin" in low:
            continue
        if low.startswith("promoted by"):
            continue
        if re.search(r"\bpeople clicked apply\b", low):
            continue
        if low.startswith("save ") and " at " in low:
            continue

        out.append(raw)

    return "\n".join(out)
'''.lstrip("\n")

    s = s[:a] + helper + "\n\n" + s[a:]
    print("✅ Inserted strip_linkedin_chrome() helper (anchored above skill-section extraction).")
else:
    print("ℹ️ strip_linkedin_chrome() helper already present; leaving unchanged.")

# ----------------------------
# 2) Ensure extract_skill_priorities() strips chrome first
# Insert `desc = strip_linkedin_chrome(desc)` once per function.
# ----------------------------
m = re.search(r"(?ms)^def\s+extract_skill_priorities\s*\(.*?\)\s*->\s*Dict\[str,\s*str\]\s*:\s*\n", s)
if not m:
    raise SystemExit("❌ Could not locate def extract_skill_priorities(...).")

# Find the first executable line after the docstring (or right after def if no docstring)
fn_start = m.end()

# If already present near the top, skip
head_window = s[fn_start: fn_start + 600]
if "strip_linkedin_chrome(desc)" in head_window:
    print("ℹ️ strip_linkedin_chrome(desc) already applied inside extract_skill_priorities(); skipping.")
else:
    # If there's a docstring, insert right after it
    ds = re.search(r'(?ms)\A(\s*)("""|\'\'\').*?\2\s*\n', s[fn_start:])
    if ds:
        insert_at = fn_start + ds.end()
        indent = ds.group(1) or "    "
    else:
        # No docstring: insert on next line with 4 spaces
        insert_at = fn_start
        indent = "    "

    inject = f"{indent}desc = strip_linkedin_chrome(desc)\n"
    s = s[:insert_at] + inject + s[insert_at:]
    print("✅ Added `desc = strip_linkedin_chrome(desc)` at top of extract_skill_priorities().")

p.write_text(s, encoding="utf-8")
print("✅ Wrote patched file.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"

echo ""
echo "Run rescan:"
echo "  python -u python/enrich_job_postings.py --rescan-skills"
