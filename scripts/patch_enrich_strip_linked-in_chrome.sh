#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
TS="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.strip_chrome_${TS}"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# 1) Insert helper if missing (right after normalize_for_matching is a safe spot)
if "def strip_linkedin_chrome(" not in s:
    anchor = "def normalize_for_matching(s: str) -> str:\n"
    i = s.find(anchor)
    if i == -1:
        raise SystemExit("❌ Could not find normalize_for_matching() to anchor insertion")

    # insert after normalize_for_matching() block (find next blank line after it)
    j = s.find("\n\n", i)
    j = s.find("\n\n", j + 2)  # hop once more to get past the function body
    if j == -1:
        raise SystemExit("❌ Could not find insertion point after normalize_for_matching()")

    helper = """
def strip_linkedin_chrome(desc: str) -> str:
    \"\"\"
    LinkedIn copy/paste includes lots of UI chrome like 'Try Premium', 'help of AI', etc.
    We keep the actual job content by trimming to the first real section header.
    \"\"\"
    if not desc:
        return desc

    d = desc.replace("\\u00a0", " ")

    # Prefer the actual job section start
    m = re.search(r"(?im)^\\s*about the job\\s*$", d)
    if m:
        return d[m.start():]

    # Fallbacks seen in various sources
    m = re.search(r"(?im)^\\s*(job description|about the role|about us)\\s*$", d)
    if m:
        return d[m.start():]

    return d
""".lstrip("\n")

    s = s[:j] + "\n" + helper + s[j:]
    print("✅ Inserted strip_linkedin_chrome() helper.")
else:
    print("ℹ️ strip_linkedin_chrome() already present; skipping helper insert.")

# 2) Patch the main loop where desc is assigned
# Replace:
#   desc = job["description_text"] or ""
# with:
#   desc = strip_linkedin_chrome(job["description_text"] or "")
pat = re.compile(r'(?m)^\s*desc\s*=\s*job\["description_text"\]\s*or\s*""\s*$')
m = pat.search(s)
if not m:
    raise SystemExit('❌ Could not find: desc = job["description_text"] or ""')

line = m.group(0)
indent = re.match(r"^(\s*)", line).group(1)
replacement = f'{indent}desc = strip_linkedin_chrome(job["description_text"] or "")'
s = s[:m.start()] + replacement + s[m.end():]
print("✅ Patched desc assignment to strip LinkedIn chrome.")

p.write_text(s, encoding="utf-8")
print("✅ Wrote patched file.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
echo "Next: python -u python/enrich_job_postings.py --rescan-skills"
