#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
TS="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.strip_chrome_v2_${TS}"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# 1) Ensure helper exists (leave your inserted one alone if already present)
if "def strip_linkedin_chrome(" not in s:
    raise SystemExit("❌ strip_linkedin_chrome() not found. (It should already be there from your last run.)")
else:
    print("ℹ️ strip_linkedin_chrome() already present.")

# 2) Replace ANY desc assignment that pulls description_text
# Accept variants like:
#   desc = job["description_text"] or ""
#   desc = (job["description_text"] or "")
#   desc = job.get("description_text") or ""
#   desc = (job.get("description_text") or "")
pat = re.compile(
    r'(?m)^(?P<ind>\s*)desc\s*=\s*(?P<rhs>.*?(?:job\[\s*["\']description_text["\']\s*\]|job\.get\(\s*["\']description_text["\']\s*\)).*)$'
)

m = pat.search(s)
if not m:
    # Show nearby hints for debugging
    hits = [i for i,ln in enumerate(s.splitlines(), 1) if "description_text" in ln and "desc" in ln]
    raise SystemExit(f"❌ Could not find a desc=... line using description_text. Candidate lines: {hits[:12]}")

ind = m.group("ind")
rhs = m.group("rhs").strip()

# Normalize: ensure it ultimately becomes a string
replacement = f'{ind}desc = strip_linkedin_chrome(({rhs}) or "")'

s = s[:m.start()] + replacement + s[m.end():]
p.write_text(s, encoding="utf-8")
print("✅ Patched desc assignment to strip LinkedIn chrome.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
echo "Next: python -u python/enrich_job_postings.py --rescan-skills"
