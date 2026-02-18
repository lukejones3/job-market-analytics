#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.mktstats_inject_${ts}"
echo "🧾 Backup: ${FILE}.bak.mktstats_inject_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# ---- 0) ensure import re exists ----
if re.search(r"(?m)^\s*import re\s*$", s) is None:
    mimp = re.search(r"(?m)^(import .+|from .+ import .+)\n", s)
    if not mimp:
        raise SystemExit("❌ Could not find import block to insert import re.")
    s = s[:mimp.end()] + "import re\n" + s[mimp.end():]
    print("✅ inserted import re")

# ---- 1) Inject defs right before def job_match_scores (stable anchor) ----
anchor = s.find("def job_match_scores")
if anchor == -1:
    raise SystemExit("❌ Could not find anchor: def job_match_scores")

if "def write_market_skill_stats(" not in s:
    defs = r'''
def write_market_skill_stats(cur, resume_id: str, run_id: str, market_job_ids, baseline_job_ids=None):
    """
    Market skill stats + baseline lift (optional).
    - req_freq/pref_freq from market_job_ids
    - baseline_req_freq/baseline_pref_freq from baseline_job_ids (if provided)
    - lift = market - baseline
    - roi = demand * rarity, where demand weights required > preferred and rarity penalizes ubiquity
    """
    if not market_job_ids:
        return

    total_jobs = len(market_job_ids)

    cur.execute("""
      SELECT
        js.skill_id,
        COUNT(*) FILTER (WHERE js.skill_priority='required') AS req_jobs,
        COUNT(*) FILTER (WHERE js.skill_priority='preferred') AS pref_jobs
      FROM job_skills js
      WHERE js.job_id = ANY(%s)
      GROUP BY js.skill_id
    """, (market_job_ids,))
    rows = cur.fetchall()

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

        demand = (1.0 * req_freq) + (0.35 * pref_freq)

        # rarity: penalize ubiquitous skills, boost scarce skills
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


def write_market_skill_stats_matched(cur, resume_id: str, run_id: str, matched_job_ids, baseline_job_ids=None):
    """
    Same as write_market_skill_stats, but writes into resume_market_skill_stats_matched
    for the TOP MATCHED jobs set.
    """
    if not matched_job_ids:
        return

    total_jobs = len(matched_job_ids)

    cur.execute("""
      SELECT
        js.skill_id,
        COUNT(*) FILTER (WHERE js.skill_priority='required') AS req_jobs,
        COUNT(*) FILTER (WHERE js.skill_priority='preferred') AS pref_jobs
      FROM job_skills js
      WHERE js.job_id = ANY(%s)
      GROUP BY js.skill_id
    """, (matched_job_ids,))
    rows = cur.fetchall()

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

        demand = (1.0 * req_freq) + (0.35 * pref_freq)
        rarity = 1.0 / ((req_freq + pref_freq + eps) ** 0.5)
        roi = demand * rarity

        b_req_freq, b_pref_freq = base.get(sid, (0.0, 0.0))
        lift_req = req_freq - b_req_freq
        lift_pref = pref_freq - b_pref_freq

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
            float(req_freq), float(pref_freq),
            float(demand), float(rarity), float(roi),
            float(b_req_freq), float(b_pref_freq),
            float(lift_req), float(lift_pref),
        ))
'''.lstrip("\n")

    s = s[:anchor] + defs + "\n" + s[anchor:]
    print("✅ Injected write_market_skill_stats + write_market_skill_stats_matched")
else:
    print("ℹ️ write_market_skill_stats already exists; not injecting defs")

# ---- 2) Patch score_resume() callsite: baseline + calls ----
# We inject right after: weights = compute_skill_weights(cur, market_job_ids)
m_w = re.search(r"(?m)^\s*weights\s*=\s*compute_skill_weights\(cur,\s*market_job_ids\)\s*$", s)
if not m_w:
    raise SystemExit("❌ Could not find weights = compute_skill_weights(cur, market_job_ids)")

# Determine indentation of that line
line_start = s.rfind("\n", 0, m_w.start()) + 1
indent = re.match(r"\s*", s[line_start:m_w.start()]).group(0)

inject = (
    f"\n{indent}# baseline market for lift (drop role, keep location/workplace/level)\n"
    f"{indent}baseline_job_ids = fetch_market_jobs(cur, None, location_id, workplace_type, experience_level, months_back, max(top_jobs, 2000))\n"
    f"\n{indent}# cache market skill stats (market + baseline lift)\n"
    f"{indent}write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)\n"
)

# only inject once
window = s[m_w.end():m_w.end()+800]
if "baseline_job_ids" not in window and "write_market_skill_stats(" not in window:
    s = s[:m_w.end()] + inject + s[m_w.end():]
    print("✅ Injected baseline_job_ids + write_market_skill_stats call after weights")
else:
    print("ℹ️ baseline/call already present near weights; not reinjecting")

# Inject matched-market call right after top_match_job_ids line
needle = "top_match_job_ids = [j for j,_ in matches[:50]]"
pos = s.find(needle)
if pos == -1:
    raise SystemExit("❌ Could not find top_match_job_ids line")

# find end of that line
eol = s.find("\n", pos)
after = s[eol:eol+500]
call2 = "write_market_skill_stats_matched"
if call2 not in after:
    s = s[:eol+1] + f"{indent}# cache matched-market skill stats (top matched jobs)\n{indent}write_market_skill_stats_matched(cur, resume_id, run_id, top_match_job_ids, baseline_job_ids)\n" + s[eol+1:]
    print("✅ Injected write_market_skill_stats_matched call")
else:
    print("ℹ️ matched call already present; not reinjecting")

p.write_text(s, encoding="utf-8")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
