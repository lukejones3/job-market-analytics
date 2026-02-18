#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.marketstats_def_${ts}"
echo "🧾 Backup: ${FILE}.bak.marketstats_def_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

if "def write_market_skill_stats(" in s:
    print("ℹ️ write_market_skill_stats already exists; no change.")
    raise SystemExit(0)

anchor = re.search(r"(?m)^def job_match_scores\(", s)
if not anchor:
    raise SystemExit("❌ Could not find anchor: def job_match_scores(")

inject = r'''
def write_market_skill_stats(cur, resume_id: str, run_id: str, job_ids: list):
    """
    Cache per-run market skill stats into resume_market_skill_stats.
    Counts DISTINCT jobs that mention each skill as required/preferred (nice-to-have counts as preferred).
    """
    if not job_ids:
        return

    total_jobs = int(len(job_ids))

    cur.execute("""
      SELECT
        js.skill_id,
        COUNT(DISTINCT js.job_id) FILTER (WHERE js.skill_priority = 'required') AS req_jobs,
        COUNT(DISTINCT js.job_id) FILTER (WHERE js.skill_priority IN ('preferred','nice-to-have')) AS pref_jobs
      FROM job_skills js
      WHERE js.job_id = ANY(%s)
      GROUP BY js.skill_id
    """, (job_ids,))

    rows = cur.fetchall() or []
    for r in rows:
        sid = r["skill_id"]
        req_jobs = int(r["req_jobs"] or 0)
        pref_jobs = int(r["pref_jobs"] or 0)

        req_freq = (req_jobs / total_jobs) if total_jobs else 0.0
        pref_freq = (pref_jobs / total_jobs) if total_jobs else 0.0

        # Demand: required dominates preferred
        demand = (0.75 * req_freq) + (0.25 * pref_freq)

        # Rarity proxy: higher when combined frequency is low (avoid div by zero)
        combined = req_freq + pref_freq
        rarity = 1.0 / (combined + 0.02)

        roi = demand * rarity

        cur.execute("""
          INSERT INTO resume_market_skill_stats (
            resume_id, run_id, skill_id,
            req_jobs, pref_jobs, total_jobs,
            req_freq, pref_freq,
            demand_score, rarity_score, roi_score
          ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (resume_id, run_id, skill_id) DO UPDATE SET
            req_jobs=EXCLUDED.req_jobs,
            pref_jobs=EXCLUDED.pref_jobs,
            total_jobs=EXCLUDED.total_jobs,
            req_freq=EXCLUDED.req_freq,
            pref_freq=EXCLUDED.pref_freq,
            demand_score=EXCLUDED.demand_score,
            rarity_score=EXCLUDED.rarity_score,
            roi_score=EXCLUDED.roi_score
        """, (
            resume_id, run_id, sid,
            req_jobs, pref_jobs, total_jobs,
            float(req_freq), float(pref_freq),
            float(demand), float(rarity), float(roi)
        ))
'''.lstrip("\n") + "\n\n"

s = s[:anchor.start()] + inject + s[anchor.start():]
p.write_text(s, encoding="utf-8")
print("✅ Injected write_market_skill_stats() before job_match_scores().")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
