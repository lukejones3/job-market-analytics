#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.fixindent_${ts}"
echo "🧾 Backup: ${FILE}.bak.fixindent_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# ----- Work only inside score_resume() to avoid collateral edits -----
m0 = re.search(r"(?ms)^def score_resume\([^\n]*\):\n(?P<body>.*?)(?=^def main\(\):\n)", s)
if not m0:
    raise SystemExit("❌ Could not locate score_resume() block (def score_resume ... def main).")
body = m0.group("body")

# Detect the canonical indent used inside score_resume (should be 16 spaces here)
# We'll anchor on the weights assignment line you showed.
m_w = re.search(r"(?m)^(?P<indent>\s*)weights\s*=\s*compute_skill_weights\(cur,\s*market_job_ids\)\s*$", body)
if not m_w:
    raise SystemExit("❌ Could not find weights = compute_skill_weights(cur, market_job_ids) inside score_resume().")
indent = m_w.group("indent")

# 1) Ensure baseline_job_ids exists + ensure write_market_skill_stats uses it
baseline_block = (
    f"\n{indent}# baseline market for lift (drop role, keep location/workplace/level)\n"
    f"{indent}baseline_job_ids = fetch_market_jobs(cur, None, location_id, workplace_type, experience_level, months_back, max(top_jobs, 2000))\n"
    f"\n{indent}# cache market skill stats for this run (market + baseline lift)\n"
    f"{indent}write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)\n"
)

# Remove any old non-baseline call (we'll re-add the correct one)
body2 = re.sub(
    rf"(?m)^{re.escape(indent)}# cache market skill stats for this run\s*\n"
    rf"{re.escape(indent)}write_market_skill_stats\(cur,\s*resume_id,\s*run_id,\s*market_job_ids\)\s*\n",
    "",
    body
)

# If baseline_job_ids is not defined shortly after weights line, inject it right after weights assignment
post_weights_window = body2[m_w.end():m_w.end()+1200]
if "baseline_job_ids" not in post_weights_window:
    body2 = body2[:m_w.end()] + baseline_block + body2[m_w.end():]

# 2) Fix the matched-market call indentation + ensure it exists exactly once after top_match_job_ids line
# First: remove any stray UNINDENTED lines (your current bug)
body2 = re.sub(r"(?m)^\s*# cache matched-market skill stats \(top matched jobs\)\s*\n", "", body2)
body2 = re.sub(r"(?m)^write_market_skill_stats_matched\([^\n]*\)\s*\n", "", body2)

# Now inject the properly-indented matched call after top_match_job_ids assignment
needle = "top_match_job_ids = [j for j,_ in matches[:50]]"
m_tm = re.search(rf"(?m)^{re.escape(indent)}{re.escape(needle)}\s*$", body2)
if not m_tm:
    raise SystemExit("❌ Could not find indented top_match_job_ids line inside score_resume().")

inject_matched = (
    f"\n{indent}# cache matched-market skill stats (top matched jobs)\n"
    f"{indent}write_market_skill_stats_matched(cur, resume_id, run_id, top_match_job_ids, baseline_job_ids)\n"
)

# Only inject if not already present nearby
window = body2[m_tm.end():m_tm.end()+600]
if "write_market_skill_stats_matched" not in window:
    body2 = body2[:m_tm.end()] + inject_matched + body2[m_tm.end():]

# Splice updated body back into full file
s2 = s[:m0.start("body")] + body2 + s[m0.end("body"):]
p.write_text(s2, encoding="utf-8")
print("✅ Patched: baseline_job_ids + corrected write_market_skill_stats call + fixed matched call indentation.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
