#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.marketstats_callfix_${ts}"
echo "🧾 Backup: ${FILE}.bak.marketstats_callfix_${ts}"

python3 - <<'PY'
from pathlib import Path
import re
p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

m = re.search(r"(?m)^\s*weights\s*=\s*compute_skill_weights\(cur,\s*market_job_ids\)\s*$", s)
if not m:
    raise SystemExit("❌ Could not find weights = compute_skill_weights(cur, market_job_ids)")

after = s[m.end():m.end()+500]
call = "write_market_skill_stats(cur, resume_id, run_id, market_job_ids)"
if call in after:
    print("ℹ️ call already present")
else:
    s = s[:m.end()] + "\n\n                # cache market skill stats for this run\n                " + call + "\n" + s[m.end():]
    print("✅ injected call")

p.write_text(s, encoding="utf-8")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
