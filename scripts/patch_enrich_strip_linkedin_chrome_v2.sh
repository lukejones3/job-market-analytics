#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# ----------------------------
# 1) Insert helper if missing
# ----------------------------
if "def strip_linkedin_chrome(" not in s:
    helper = r'''
def strip_linkedin_chrome(text: str) -> str:
    """
    Remove common LinkedIn/Chrome copy-paste UI noise from descriptions.
    Keeps job content, strips repeated UI blocks and "help of ai" marketing line.
    """
    if not text:
        return text

    drop_substrings = [
        "Get personalized tips to stand out to hirers",
        "Try Premium",
        "People you can reach out to",
        "Company alumni",
        "Tailor my resume",
        "Help me update my profile",
        "Create cover letter",
        "Is this information helpful?",
        "Responses managed off LinkedIn",
        "Promoted by hirer",
        "Show more options",
        "Show match details",
        "Save",
        "Share",
    ]

    out_lines = []
    for ln in text.splitlines():
        raw = ln.rstrip("\n")
        low = normalize_for_matching(raw)

        if not low:
            out_lines.append(raw)
            continue

        # Kill the main offender explicitly
        if "help of ai" in low or "with the help of ai" in low:
            continue

        # Drop exact UI-ish singletons
        if low in {"apply", "save", "share", "show more options"}:
            continue

        # Drop lines containing common UI blurbs
        if any(sub.lower() in low for sub in (x.lower() for x in drop_substrings)):
            continue

        out_lines.append(raw)

    # De-dupe excessive blank runs
    cleaned = []
    blank_run = 0
    for ln in out_lines:
        if ln.strip() == "":
            blank_run += 1
            if blank_run <= 2:
                cleaned.append(ln)
        else:
            blank_run = 0
            cleaned.append(ln)

    return "\n".join(cleaned)
'''.lstrip("\n")

    # Insert helper after normalize_for_matching() definition (safe anchor)
    anchor = "def normalize_for_matching"
    idx = s.find(anchor)
    if idx == -1:
        raise SystemExit("❌ Could not find anchor: def normalize_for_matching")

    # Find end of normalize_for_matching() block by next double newline after it
    m = re.search(r"def normalize_for_matching\(.*?\):.*?\n(?:.*\n)*?\n", s[idx:], re.DOTALL)
    if not m:
        raise SystemExit("❌ Could not locate end of normalize_for_matching() block")

    insert_at = idx + m.end()
    s = s[:insert_at] + "\n" + helper + "\n" + s[insert_at:]
    print("✅ Inserted strip_linkedin_chrome() helper.")
else:
    print("ℹ️ strip_linkedin_chrome() already present; skipping helper insert.")

# ----------------------------
# 2) Ensure main loop calls it
# ----------------------------
# We’ll insert right after: desc = job["description_text"] or ""
# Be flexible to handle slight variations.
pat = re.compile(r'(?m)^(?P<ind>\s*)desc\s*=\s*job\[[\'"]description_text[\'"]\]\s*or\s*[\'"]{0,1}[\'"]{0,1}\s*$')
mm = pat.search(s)

if not mm:
    # fallback: desc = job.get("description_text") or ""
    pat2 = re.compile(r'(?m)^(?P<ind>\s*)desc\s*=\s*job\.get\([\'"]description_text[\'"]\)\s*or\s*[\'"]{0,1}[\'"]{0,1}\s*$')
    mm = pat2.search(s)

if not mm:
    raise SystemExit('❌ Could not find desc assignment line (description_text) in main loop.')

ind = mm.group("ind")
call_line = f"{ind}desc = strip_linkedin_chrome(desc)\n"

# Don’t double-insert
after_pos = mm.end()
window = s[after_pos: after_pos + 200]
if "strip_linkedin_chrome(desc)" in window:
    print("ℹ️ strip_linkedin_chrome(desc) call already present; skipping call insert.")
else:
    s = s[:after_pos] + "\n" + call_line + s[after_pos:]
    print("✅ Inserted desc = strip_linkedin_chrome(desc) call after desc assignment.")

p.write_text(s, encoding="utf-8")
print("✅ Wrote patched python/enrich_job_postings.py")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
