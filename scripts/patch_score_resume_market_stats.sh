#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
bak="${FILE}.bak.marketstats_${ts}"
cp "$FILE" "$bak"
echo "🧾 Backup: $bak"

python3 - <<'PY'
from pathlib import Path

p = Path.home() / "github/job-market-analytics/python/score_resume.py"
s = p.read_text(encoding="utf-8")

# 1) Insert helper function once
marker = "\n\ndef score_resume("
if "def write_market_skill_stats(" not in s:
    inject = r'''

def write_market_skill_stats(cur, resume_id: str, run_id: str, market_job_ids: list):
    """
    Cache market skill stats for the run.
    req_freq/pref_freq are fractions of market jobs mentioning the skill in that priority bucket.
    demand_score = req_freq*1.0 + pref_freq*0.5
    rarity_score = 1 / (req_freq + pref_freq + 0.01)  (simple proxy)
    roi_score = demand_score * rarity_score
    """
    if not market_job_ids:
        return

    total_jobs = len(market_job_ids)

    cur.execute("""
      SELECT
        js.skill_id,
        COUNT(DISTINCT js.job_id) FILTER (WHERE js.skill_priority='required')  AS req_jobs,
        COUNT(DISTINCT js.job_id) FILTER (WHERE js.skill_priority='preferred') AS pref_jobs
      FROM job_skills js
      WHERE js.job_id = ANY(%s)
      GROUP BY js.skill_id
    """, (market_job_ids,))

    rows = cur.fetchall()

    # Clear existing cache for this run
    cur.execute("DELETE FROM resume_market_skill_stats WHERE resume_id=%s AND run_id=%s", (resume_id, run_id))

    for r in rows:
        sid = r["skill_id"]
        req_jobs = int(r["req_jobs"] or 0)
        pref_jobs = int(r["pref_jobs"] or 0)
        req_freq = (req_jobs / float(total_jobs)) if total_jobs else 0.0
        pref_freq = (pref_jobs / float(total_jobs)) if total_jobs else 0.0

        demand = (req_freq * 1.0) + (pref_freq * 0.5)
        rarity = 1.0 / (req_freq + pref_freq + 0.01)
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
            req_freq, pref_freq,
            demand, rarity, roi
        ))
'''.strip("\n") + "\n\n"
    i = s.find(marker)
    if i == -1:
        raise SystemExit("❌ Could not find score_resume() to anchor injection.")
    s = s[:i] + "\n\n" + inject + s[i:]

# 2) Call it after weights are computed (right after: weights = compute_skill_weights(...))
needle = "                weights = compute_skill_weights(cur, market_job_ids)\n"
if needle not in s:
    raise SystemExit("❌ Could not find weights assignment line to hook market stats.")

if "write_market_skill_stats(" not in s[s.find(needle):s.find(needle)+600]:
    s = s.replace(
        needle,
        needle + "\n                # cache market skill stats for this run\n                write_market_skill_stats(cur, resume_id, run_id, market_job_ids)\n"
    )

p.write_text(s, encoding="utf-8")
print("✅ Patched: market skill stats cache -> resume_market_skill_stats.")
PY

python3 -m py_compile "$FILE"
echo "✅ Compile OK"
