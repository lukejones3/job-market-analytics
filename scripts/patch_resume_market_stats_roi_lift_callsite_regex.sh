#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"

FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.roilift_callsite_regex_${ts}"
echo "🧾 Backup: ${FILE}.bak.roilift_callsite_regex_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# Find the score_resume() function block to avoid patching some other scope
m_sr = re.search(r"(?s)def score_resume\([^)]*\):\n.*?(?=\ndef |\Z)", s)
if not m_sr:
    raise SystemExit("❌ Could not locate def score_resume(...) block.")
sr = s[m_sr.start():m_sr.end()]

# Find the weights assignment line inside score_resume (allow whitespace/indent variations)
m_w = re.search(r"(?m)^(?P<indent>\s*)weights\s*=\s*compute_skill_weights\(\s*cur\s*,\s*market_job_ids\s*\)\s*$", sr)
if not m_w:
    # Show a hint: maybe the call signature got edited
    raise SystemExit("❌ Could not find weights = compute_skill_weights(cur, market_job_ids) inside score_resume().")

indent = m_w.group("indent")

# Build injection (keep same indent level)
inject = (
    f"{indent}\n"
    f"{indent}# baseline market (broader) for lift; keep location/workplace/level, drop role\n"
    f"{indent}baseline_job_ids = fetch_market_jobs(cur, None, location_id, workplace_type, experience_level, months_back, max(top_jobs, 2000))\n"
    f"{indent}\n"
    f"{indent}# cache market skill stats for this run (with baseline for lift)\n"
    f"{indent}write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)\n"
)

# If already present, just ensure the call uses baseline_job_ids
if "baseline_job_ids" in sr:
    sr2 = sr.replace(
        "write_market_skill_stats(cur, resume_id, run_id, market_job_ids)\n",
        "write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)\n"
    )
    if sr2 != sr:
        sr = sr2
        print("✅ Updated write_market_skill_stats call to include baseline_job_ids.")
    else:
        print("ℹ️ baseline_job_ids already present; nothing to inject.")
else:
    # Insert inject right AFTER the weights assignment line
    insert_pos = m_w.end()
    sr = sr[:insert_pos] + inject + sr[insert_pos:]
    print("✅ Injected baseline_job_ids + write_market_skill_stats(...) call after weights computation.")

# Splice back into full file
s2 = s[:m_sr.start()] + sr + s[m_sr.end():]
p.write_text(s2, encoding="utf-8")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
