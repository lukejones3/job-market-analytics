#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
PY="${REPO}/.venv/bin/python"
ts="$(date +%Y%m%d_%H%M%S)"

cp "$FILE" "${FILE}.bak.insertcols_${ts}"
echo "🧾 Backup: ${FILE}.bak.insertcols_${ts}"

"$PY" - <<'PY'
from pathlib import Path
import re

p = Path.home() / "github" / "job-market-analytics" / "python" / "score_resume.py"
s = p.read_text(encoding="utf-8")

# 0) Ensure Json import exists (needed for jsonb)
# Common patterns in your file: "from psycopg2.extras import RealDictCursor" or similar.
if re.search(r"from\s+psycopg2\.extras\s+import\s+.*\bJson\b", s) is None:
    m = re.search(r"(?m)^from\s+psycopg2\.extras\s+import\s+(.+)$", s)
    if m:
        line = m.group(0)
        if "Json" not in line:
            s = s.replace(line, line + ", Json", 1)
    else:
        # fallback: add a safe import after "import psycopg2"
        m2 = re.search(r"(?m)^import\s+psycopg2\s*$", s)
        if m2:
            insert_at = m2.end()
            s = s[:insert_at] + "\nfrom psycopg2.extras import Json\n" + s[insert_at:]
        else:
            # last resort at top
            s = "from psycopg2.extras import Json\n" + s

# 1) Locate write_scores() block
needle = "def write_scores("
start = s.find(needle)
if start == -1:
    raise SystemExit("❌ Could not find def write_scores(")

next_def = s.find("\ndef ", start + 1)
end = next_def if next_def != -1 else len(s)
block = s[start:end]

# 2) Expand INSERT column list to include new columns
# Find the INSERT column list area and patch it
if "plausibility_penalty" not in block or "confidence_score" not in block or "confidence_flags" not in block:
    # Patch the column list right after top_jobs_considered
    block = block.replace(
        "matched_jobs_count, top_jobs_considered",
        "matched_jobs_count, top_jobs_considered,\n        plausibility_penalty, confidence_score, confidence_flags"
    )

# 3) Ensure placeholders are 12
block = block.replace(
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)

# 4) Ensure execute params include Json(confidence_flags)
# Replace the execute tuple tail
block = block.replace(
    ", plausibility_penalty, confidence_score, confidence_flags))",
    ", plausibility_penalty, confidence_score, Json(confidence_flags)))"
)

# 5) Keep your guard (already there) but ensure it stays before execute
# (No change needed if it exists.)

s = s[:start] + block + s[end:]
p.write_text(s, encoding="utf-8")
print("✅ Patched write_scores(): INSERT columns + Json(confidence_flags).")
PY

"$PY" -m py_compile "$FILE"
echo "✅ Compile OK"
