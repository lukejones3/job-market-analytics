#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"

echo "🛠️  2) DB: create resume_market_skill_stats_matched"
psql -v ON_ERROR_STOP=1 job_analytics <<'SQL'
BEGIN;

CREATE TABLE IF NOT EXISTS resume_market_skill_stats_matched (
  resume_id      text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  run_id         text NOT NULL REFERENCES resume_runs(run_id) ON DELETE CASCADE,
  skill_id       text NOT NULL REFERENCES skills(skill_id),
  req_jobs       int  NOT NULL DEFAULT 0,
  pref_jobs      int  NOT NULL DEFAULT 0,
  total_jobs     int  NOT NULL DEFAULT 0,
  req_freq       numeric NOT NULL DEFAULT 0,
  pref_freq      numeric NOT NULL DEFAULT 0,
  demand_score   numeric NOT NULL DEFAULT 0,
  rarity_score   numeric NOT NULL DEFAULT 0,
  roi_score      numeric NOT NULL DEFAULT 0,
  baseline_req_freq numeric NOT NULL DEFAULT 0,
  baseline_pref_freq numeric NOT NULL DEFAULT 0,
  lift_req numeric NOT NULL DEFAULT 1,
  lift_pref numeric NOT NULL DEFAULT 1,
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, run_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_rmsm_run_roi ON resume_market_skill_stats_matched(run_id, roi_score DESC);

COMMIT;
SQL

FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.matchedstats_${ts}"
echo "🧾 Backup: ${FILE}.bak.matchedstats_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# We reuse write_market_skill_stats logic but write into matched table.
if "def write_market_skill_stats_matched" not in s:
    inject = r'''
def write_market_skill_stats_matched(cur, resume_id: str, run_id: str, matched_job_ids: list, baseline_job_ids: list = None):
    if not matched_job_ids:
        return

    # Same logic as write_market_skill_stats, but store into resume_market_skill_stats_matched
    total_jobs = len(matched_job_ids)

    if not baseline_job_ids:
        baseline_job_ids = matched_job_ids
    baseline_total = len(baseline_job_ids) or 1

    cur.execute("""
      SELECT skill_id,
             COUNT(*) FILTER (WHERE skill_priority='required') AS req_jobs,
             COUNT(*) FILTER (WHERE skill_priority IN ('preferred','nice-to-have')) AS pref_jobs
      FROM job_skills
      WHERE job_id = ANY(%s)
      GROUP BY skill_id
    """, (matched_job_ids,))
    tgt = {r["skill_id"]: (int(r["req_jobs"]), int(r["pref_jobs"])) for r in cur.fetchall()}

    cur.execute("""
      SELECT skill_id,
             COUNT(*) FILTER (WHERE skill_priority='required') AS req_jobs,
             COUNT(*) FILTER (WHERE skill_priority IN ('preferred','nice-to-have')) AS pref_jobs
      FROM job_skills
      WHERE job_id = ANY(%s)
      GROUP BY skill_id
    """, (baseline_job_ids,))
    base = {r["skill_id"]: (int(r["req_jobs"]), int(r["pref_jobs"])) for r in cur.fetchall()}

    all_sids = set(tgt.keys()) | set(base.keys())

    for sid in all_sids:
        req_jobs, pref_jobs = tgt.get(sid, (0,0))
        b_req, b_pref = base.get(sid, (0,0))

        req_freq  = _safe_div(req_jobs,  total_jobs,  0.0)
        pref_freq = _safe_div(pref_jobs, total_jobs,  0.0)

        b_req_freq  = _safe_div(b_req,  baseline_total, 0.0)
        b_pref_freq = _safe_div(b_pref, baseline_total, 0.0)

        demand = (0.80 * req_freq) + (0.20 * pref_freq)
        rarity = 0.15 + (1.0 - _clamp(req_freq + 0.5*pref_freq, 0.0, 1.0))

        lift_req  = _clamp(_safe_div(req_freq,  b_req_freq,  1.0), 0.5, 3.0)
        lift_pref = _clamp(_safe_div(pref_freq, b_pref_freq, 1.0), 0.5, 3.0)

        lift = (0.75 * lift_req) + (0.25 * lift_pref)
        roi = demand * rarity * lift

        cur.execute("""
          INSERT INTO resume_market_skill_stats_matched (
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
            req_freq, pref_freq,
            float(demand), float(rarity), float(roi),
            b_req_freq, b_pref_freq,
            float(lift_req), float(lift_pref)
        ))
'''.strip() + "\n\n"

    # Put it near the other market-stats function
    anchor = s.find("def job_match_scores")
    if anchor == -1:
        raise SystemExit("❌ Could not find anchor (def job_match_scores) for matched stats injection.")
    s = s[:anchor] + inject + s[anchor:]

# Call it after matches are computed (after top_match_job_ids is defined)
needle = "                top_match_job_ids = [j for j,_ in matches[:50]]\n"
pos = s.find(needle)
if pos == -1:
    raise SystemExit("❌ Could not find top_match_job_ids line to hook matched stats.")

hook = (
    needle +
    "\n                # cache matched-market skill stats (what your resume actually matches)\n"
    "                write_market_skill_stats_matched(cur, resume_id, run_id, top_match_job_ids, baseline_job_ids)\n"
)
if "write_market_skill_stats_matched(" not in s[pos:pos+600]:
    s = s.replace(needle, hook)

p.write_text(s, encoding="utf-8")
print("✅ Patched matched-market stats: def + call after top_match_job_ids.")
PY

python -m py_compile python/score_resume.py
echo "✅ Compile OK"
