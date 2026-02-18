#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.mktstats_siglift_${ts}"
echo "🧾 Backup: ${FILE}.bak.mktstats_siglift_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# Replace the whole write_market_skill_stats(...) function (hard replace)
pat = re.compile(r"(?s)^def write_market_skill_stats\([^\n]*\):\n.*?(?=^def |\Z)", re.M)

m = pat.search(s)
if not m:
    raise SystemExit("❌ Could not find def write_market_skill_stats(...) to replace.")

new_fn = r'''
def write_market_skill_stats(cur, resume_id: str, run_id: str, market_job_ids, baseline_job_ids=None):
    """
    Writes market skill stats for a job set (market_job_ids) plus optional baseline lift stats.
    - req_freq/pref_freq computed from market_job_ids
    - baseline_req_freq/baseline_pref_freq computed from baseline_job_ids (if provided)
    - lift_req/lift_pref are simple differences (market - baseline)
    - roi_score uses demand * rarity where rarity = 1 / sqrt(req_freq+pref_freq+eps)
    """
    if not market_job_ids:
        return

    # Market counts
    cur.execute("""
      SELECT
        js.skill_id,
        COUNT(*) FILTER (WHERE js.skill_priority='required') AS req_jobs,
        COUNT(*) FILTER (WHERE js.skill_priority='preferred') AS pref_jobs,
        COUNT(DISTINCT js.job_id) AS jobs_with_skill
      FROM job_skills js
      WHERE js.job_id = ANY(%s)
      GROUP BY js.skill_id
    """, (market_job_ids,))
    rows = cur.fetchall()
    total_jobs = len(market_job_ids)

    # Baseline freqs (optional)
    base = {}
    if baseline_job_ids:
        b_total = len(baseline_job_ids)
        cur.execute("""
          SELECT
            js.skill_id,
            COUNT(*) FILTER (WHERE js.skill_priority='required') AS req_jobs,
            COUNT(*) FILTER (WHERE js.skill_priority='preferred') AS pref_jobs
          FROM job_skills js
          WHERE js.job_id = ANY(%s)
          GROUP BY js.skill_id
        """, (baseline_job_ids,))
        for r in cur.fetchall():
            sid = r["skill_id"]
            b_req = int(r["req_jobs"] or 0)
            b_pref = int(r["pref_jobs"] or 0)
            base[sid] = (
                (b_req / b_total) if b_total else 0.0,
                (b_pref / b_total) if b_total else 0.0,
            )

    eps = 1e-9
    for r in rows:
        sid = r["skill_id"]
        req_jobs = int(r["req_jobs"] or 0)
        pref_jobs = int(r["pref_jobs"] or 0)

        req_freq = (req_jobs / total_jobs) if total_jobs else 0.0
        pref_freq = (pref_jobs / total_jobs) if total_jobs else 0.0

        # demand is just weighted freq (required counts more)
        demand = (1.0 * req_freq) + (0.35 * pref_freq)

        # rarity: penalize ubiquitous skills; boost scarce ones
        rarity = 1.0 / ((req_freq + pref_freq + eps) ** 0.5)
        roi = demand * rarity

        b_req_freq, b_pref_freq = base.get(sid, (0.0, 0.0))
        lift_req = req_freq - b_req_freq
        lift_pref = pref_freq - b_pref_freq

        cur.execute("""
          INSERT INTO resume_market_skill_stats (
            resume_id, run_id, skill_id,
            req_jobs, pref_jobs, total_jobs,
            req_freq, pref_freq,
            demand_score, rarity_score, roi_score,
            baseline_req_freq, baseline_pref_freq,
            lift_req, lift_pref
          ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (resume_id, run_id, skill_id) DO UPDATE SET
            req_jobs=EXCLUDED.req_jobs,
            pref_jobs=EXCLUDED.pref_jobs,
            total_jobs=EXCLUDED.total_jobs,
            req_freq=EXCLUDED.req_freq,
            pref_freq=EXCLUDED.pref_freq,
            demand_score=EXCLUDED.demand_score,
            rarity_score=EXCLUDED.rarity_score,
            roi_score=EXCLUDED.roi_score,
            baseline_req_freq=EXCLUDED.baseline_req_freq,
            baseline_pref_freq=EXCLUDED.baseline_pref_freq,
            lift_req=EXCLUDED.lift_req,
            lift_pref=EXCLUDED.lift_pref
        """, (
            resume_id, run_id, sid,
            req_jobs, pref_jobs, total_jobs,
            float(req_freq), float(pref_freq),
            float(demand), float(rarity), float(roi),
            float(b_req_freq), float(b_pref_freq),
            float(lift_req), float(lift_pref),
        ))
'''.lstrip("\n")

s2 = s[:m.start()] + new_fn + "\n" + s[m.end():]
p.write_text(s2, encoding="utf-8")
print("✅ Replaced write_market_skill_stats() with baseline/lift-capable version.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
