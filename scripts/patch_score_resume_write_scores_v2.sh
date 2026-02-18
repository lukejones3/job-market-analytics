#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
PY="${REPO}/.venv/bin/python"
ts="$(date +%Y%m%d_%H%M%S)"

cp "$FILE" "${FILE}.bak.write_scores_v2_${ts}"
echo "🧾 Backup: ${FILE}.bak.write_scores_v2_${ts}"

"$PY" - <<'PY'
from pathlib import Path

p = Path.home() / "github" / "job-market-analytics" / "python" / "score_resume.py"
s = p.read_text(encoding="utf-8")

needle = "def write_scores("
start = s.find(needle)
if start == -1:
    raise SystemExit("❌ Could not find def write_scores(")

# Find end of the def signature: first subsequent line that endswith "):"
sig_end = None
pos = start
while True:
    nl = s.find("\n", pos)
    if nl == -1:
        break
    line = s[pos:nl]
    if line.rstrip().endswith("):"):
        sig_end = nl
        break
    pos = nl + 1

if sig_end is None:
    raise SystemExit("❌ Could not find end of write_scores(...) signature (line ending with '):').")

sig_block = s[start:sig_end]
if "plausibility_penalty" not in sig_block:
    # Append new optional args to the LAST signature line (the one that ends with '):')
    # We'll insert before the final '):'
    last_line_start = s.rfind("\n", start, sig_end) + 1
    last_line = s[last_line_start:sig_end]
    if not last_line.rstrip().endswith("):"):
        raise SystemExit("❌ Unexpected: last signature line does not end with '):'.")

    new_last_line = last_line.rstrip()[:-2] + ", plausibility_penalty: int = 0, confidence_score: int = 100, confidence_flags=None):"
    s = s[:last_line_start] + new_last_line + "\n" + s[sig_end+1:]

# Recompute positions for function body slicing
start = s.find(needle)
if start == -1:
    raise SystemExit("❌ write_scores disappeared after patch??")

# function body end = next top-level def after write_scores
after = s.find("\n", start)
if after == -1:
    raise SystemExit("❌ Unexpected EOF after write_scores def.")
next_def = s.find("\ndef ", after)
end = next_def if next_def != -1 else len(s)

block = s[start:end]

# Ensure confidence_flags JSON-safe inside function body
if "if confidence_flags is None:" not in block:
    block = block.replace(
        '    cur.execute("""',
        '    if confidence_flags is None:\n        confidence_flags = []\n    cur.execute("""',
        1
    )

# Patch INSERT columns
if "plausibility_penalty" not in block:
    block = block.replace(
        "matched_jobs_count, top_jobs_considered",
        "matched_jobs_count, top_jobs_considered,\n        plausibility_penalty, confidence_score, confidence_flags"
    )

# Patch VALUES placeholder count (9 -> 12)
block = block.replace(
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
)

# Patch python execute params tuple
old_params = "(resume_id, run_id, market_fit, percentile, sal_min, sal_max, honesty_match, matched_jobs_count, top_jobs_considered))"
new_params = "(resume_id, run_id, market_fit, percentile, sal_min, sal_max, honesty_match, matched_jobs_count, top_jobs_considered, plausibility_penalty, confidence_score, confidence_flags))"
if old_params in block and new_params not in block:
    block = block.replace(old_params, new_params)
elif new_params in block:
    pass
else:
    # If formatting differs, fail loudly so we don't silently break it
    raise SystemExit("❌ Could not find the expected execute params tuple inside write_scores(). Paste lines 488-498 if you changed formatting.")

# Splice back
s = s[:start] + block + s[end:]

p.write_text(s, encoding="utf-8")
print("✅ Patched write_scores(): signature + INSERT includes plausibility/confidence fields.")
PY

"$PY" -m py_compile "$FILE"
echo "✅ Compile OK"
