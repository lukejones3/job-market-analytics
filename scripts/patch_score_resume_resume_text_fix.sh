#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
cd "$REPO"

FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.resumetextfix_${ts}"
echo "🧾 Backup: ${FILE}.bak.resumetextfix_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# 1) Add helper if missing
if "def fetch_resume_text(" not in s:
    insert_after = re.search(r"(?m)^def fetch_resume_skill_ids\(", s)
    if not insert_after:
        raise SystemExit("❌ Could not find anchor def fetch_resume_skill_ids(...) to insert fetch_resume_text().")

    helper = r'''

def fetch_resume_text(cur, resume_id: str) -> str:
    cur.execute("""
      SELECT resume_text
      FROM resumes
      WHERE resume_id=%s
    """, (resume_id,))
    r = cur.fetchone()
    return (r["resume_text"] if r and r["resume_text"] is not None else "")
'''.lstrip("\n")

    s = s[:insert_after.start()] + helper + "\n\n" + s[insert_after.start():]

# 2) Inject resume_text load inside score_resume() before plausibility is computed
m = re.search(r"(?m)^\s*def score_resume\(", s)
if not m:
    raise SystemExit("❌ Could not find def score_resume(...).")

# Find a safe place inside the try cursor block after upsert_resume_target(...)
needle = "upsert_resume_target(cur, resume_id, role_id, location_id, workplace_type, experience_level)"
pos = s.find(needle)
if pos == -1:
    raise SystemExit("❌ Could not find upsert_resume_target(...) call inside score_resume().")

# Insert right AFTER that line (end of line)
line_end = s.find("\n", pos)
if line_end == -1:
    raise SystemExit("❌ Unexpected: no newline after upsert_resume_target line.")

inject = "\n\n                # load resume_text (needed for plausibility/confidence)\n                resume_text = fetch_resume_text(cur, resume_id)\n"
window = s[pos:pos+400]
if "resume_text = fetch_resume_text" not in window:
    s = s[:line_end] + inject + s[line_end:]

p.write_text(s, encoding="utf-8")
print("✅ Patched: score_resume() now loads resume_text from DB.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
