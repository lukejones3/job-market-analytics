#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"

echo "🛠️  1) DB: add baseline/lift columns to resume_market_skill_stats"
psql -v ON_ERROR_STOP=1 job_analytics <<'SQL'
BEGIN;

ALTER TABLE resume_market_skill_stats
  ADD COLUMN IF NOT EXISTS baseline_req_freq numeric NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS baseline_pref_freq numeric NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS lift_req numeric NOT NULL DEFAULT 1,
  ADD COLUMN IF NOT EXISTS lift_pref numeric NOT NULL DEFAULT 1;

COMMIT;
SQL

FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.roilift_${ts}"
echo "🧾 Backup: ${FILE}.bak.roilift_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# ---- Inject helper: safe_div + clamp ----
helpers = r'''
def _safe_div(a: float, b: float, default: float = 0.0) -> float:
    try:
        if b is None or b == 0:
            return default
        return float(a) / float(b)
    except Exception:
        return default

def _clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else (hi if x > hi else x)
'''.strip() + "\n\n"

if "_safe_div(" not in s:
    # Put near other small helpers: after percentile_from_distribution OR before compute_salary_estimate
    anchor = s.find("def percentile_from_distribution")
    if anchor == -1:
        anchor = s.find("def compute_salary_estimate")
    if anchor == -1:
        raise SystemExit("❌ Could not find a stable anchor for helper injection.")
    s = s[:anchor] + helpers + s[anchor:]

# ---- Replace write_market_skill_stats definition with baseline/lift aware version ----
m = re.search(r"(?s)def write_market_skill_stats\s*\(.*?\):\n(    .*\n)+?(?=\ndef |\Z)", s)
if not m:
    raise SystemExit("❌ Could not find write_market_skill_stats() to patch.")

new_def = r'''
def write_market_skill_stats(cur, resume_id: str, run_id: str, job_ids: list, baseline_job_ids: list = None):
    """
    Cache per-skill market stats for a run:
      - req/pref freq in target market (job_ids)
      - baseline req/pref freq in broader market (baseline_job_ids)
      - lift = target_freq / baseline_freq (clamped)
      - ROI = demand * rarity * lift
    """
    if not job_ids:
        return

    total_jobs = len(job_ids)

    # Baseline: if not provided, fall back to the same market (lift=1)
    if not baseline_job_ids:
        baseline_job_ids = job_ids
    baseline_total = len(baseline_job_ids) or 1

    # Target market counts
    cur.execute("""
      SELECT skill_id,
             COUNT(*) FILTER (WHERE skill_priority='required') AS req_jobs,
             COUNT(*) FILTER (WHERE skill_priority IN ('preferred','nice-to-have')) AS pref_jobs
      FROM job_skills
      WHERE job_id = ANY(%s)
      GROUP BY skill_id
    """, (job_ids,))
    tgt = {r["skill_id"]: (int(r["req_jobs"]), int(r["pref_jobs"])) for r in cur.fetchall()}

    # Baseline counts
    cur.execute("""
      SELECT skill_id,
             COUNT(*) FILTER (WHERE skill_priority='required') AS req_jobs,
             COUNT(*) FILTER (WHERE skill_priority IN ('preferred','nice-to-have')) AS pref_jobs
      FROM job_skills
      WHERE job_id = ANY(%s)
      GROUP BY skill_id
    """, (baseline_job_ids,))
    base = {r["skill_id"]: (int(r["req_jobs"]), int(r["pref_jobs"])) for r in cur.fetchall()}

    # Union of skills
    all_sids = set(tgt.keys()) | set(base.keys())

    for sid in all_sids:
        req_jobs, pref_jobs = tgt.get(sid, (0,0))
        b_req, b_pref = base.get(sid, (0,0))

        req_freq  = _safe_div(req_jobs,  total_jobs,  0.0)
        pref_freq = _safe_div(pref_jobs, total_jobs,  0.0)

        b_req_freq  = _safe_div(b_req,  baseline_total, 0.0)
        b_pref_freq = _safe_div(b_pref, baseline_total, 0.0)

        # demand: required heavy, preferred light
        demand = (0.80 * req_freq) + (0.20 * pref_freq)

        # rarity: punish "everywhere" skills; floor to avoid blow-ups
        # (1 - freq) accentuates rare skills; add epsilon so it never hits 0
        rarity = 0.15 + (1.0 - _clamp(req_freq + 0.5*pref_freq, 0.0, 1.0))

        # lift: "how disproportionately required here vs baseline"
        lift_req  = _clamp(_safe_div(req_freq,  b_req_freq,  1.0), 0.5, 3.0)
        lift_pref = _clamp(_safe_div(pref_freq, b_pref_freq, 1.0), 0.5, 3.0)
        lift = (0.75 * lift_req) + (0.25 * lift_pref)

        roi = demand * rarity * lift

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
            req_freq, pref_freq,
            float(demand), float(rarity), float(roi),
            b_req_freq, b_pref_freq,
            float(lift_req), float(lift_pref)
        ))
'''.strip() + "\n\n"

s = s[:m.start()] + new_def + s[m.end():]

# ---- Ensure score_resume passes baseline_job_ids ----
# We will inject:
#   baseline_job_ids = fetch_market_jobs(cur, None, location_id, workplace_type, experience_level, months_back, max(top_jobs, 2000))
# and call write_market_skill_stats(..., baseline_job_ids)

needle = "                weights = compute_skill_weights(cur, market_job_ids)\n"
pos = s.find(needle)
if pos == -1:
    raise SystemExit("❌ Could not find weights assignment line to hook baseline stats.")

hook_window = s[pos:pos+800]
if "baseline_job_ids" not in hook_window:
    inject = (
        needle +
        "\n                # baseline market (broader) for lift; keep location/workplace/level, drop role\n"
        "                baseline_job_ids = fetch_market_jobs(cur, None, location_id, workplace_type, experience_level, months_back, max(top_jobs, 2000))\n"
        "\n                # cache market skill stats for this run (with baseline for lift)\n"
        "                write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)\n"
    )
    s = s.replace(needle, inject)
else:
    # Ensure call uses baseline arg
    s = s.replace(
        "write_market_skill_stats(cur, resume_id, run_id, market_job_ids)\n",
        "write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)\n"
    )

p.write_text(s, encoding="utf-8")
print("✅ Patched ROI: baseline + lift + improved rarity/ROI, and call site passes baseline_job_ids.")
PY

python -m py_compile python/score_resume.py
echo "✅ Compile OK"
