#!/usr/bin/env bash
set -euo pipefail

cd ~/github/job-market-analytics
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.fix_${ts}"
echo "🧾 Backup: ${FILE}.bak.fix_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# ------------------------------------------------------------
# A) Fix score_resume(): baseline_job_ids + correct calls + indentation
# ------------------------------------------------------------

# 1) Remove the broken UNINDENTED matched-stats lines (exactly as they appear now)
s = s.replace("\n# cache matched-market skill stats (top matched jobs)\nwrite_market_skill_stats_matched(cur, resume_id, run_id, top_match_job_ids, baseline_job_ids)\n",
              "\n")

# 2) Ensure baseline_job_ids exists after weights line, and ensure write_market_skill_stats uses baseline
weights_line = "                weights = compute_skill_weights(cur, market_job_ids)\n"
if weights_line not in s:
    raise SystemExit("❌ Could not find weights = compute_skill_weights(cur, market_job_ids) line.")

# Replace old 4-arg market stats call (if present) with baseline-aware approach
old_call = "                # cache market skill stats for this run\n                write_market_skill_stats(cur, resume_id, run_id, market_job_ids)\n"
baseline_block = (
    "\n"
    "                # baseline market for lift (drop role, keep location/workplace/level)\n"
    "                baseline_job_ids = fetch_market_jobs(\n"
    "                    cur, None, location_id, workplace_type, experience_level,\n"
    "                    months_back, max(top_jobs, 2000)\n"
    "                )\n\n"
    "                # cache market skill stats for this run (market + baseline lift)\n"
    "                write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)\n"
)

if old_call in s:
    # Replace the whole block (best case)
    s = s.replace(old_call, baseline_block)
else:
    # Otherwise inject after weights line if baseline not already nearby
    wpos = s.find(weights_line) + len(weights_line)
    window = s[wpos:wpos+1200]
    if "baseline_job_ids" not in window:
        s = s[:wpos] + baseline_block + s[wpos:]
    # Ensure the market stats call uses baseline if it exists but is still 4-arg
    s = re.sub(
        r"(?m)^\s*write_market_skill_stats\(cur,\s*resume_id,\s*run_id,\s*market_job_ids\)\s*$",
        "                write_market_skill_stats(cur, resume_id, run_id, market_job_ids, baseline_job_ids)",
        s
    )

# 3) Inject properly-indented matched-market stats call right after top_match_job_ids line
tm_line = "                top_match_job_ids = [j for j,_ in matches[:50]]\n"
if tm_line not in s:
    raise SystemExit("❌ Could not find top_match_job_ids assignment line.")

inject_matched = (
    "\n"
    "                # cache matched-market skill stats (top matched jobs)\n"
    "                write_market_skill_stats_matched(cur, resume_id, run_id, top_match_job_ids, baseline_job_ids)\n"
)

# Only inject if not already present right after that line
pos = s.find(tm_line) + len(tm_line)
after = s[pos:pos+800]
if "write_market_skill_stats_matched(" not in after:
    s = s[:pos] + inject_matched + s[pos:]

# 4) Pass plausibility/confidence into write_scores() call (keyword safe)
old_scores_call = (
    "                write_scores(cur, resume_id, run_id, market_fit, pct, sal_min, sal_max, honesty_match,\n"
    "                             matched_jobs_count=len(matches), top_jobs_considered=len(market_job_ids))\n"
)
new_scores_call = (
    "                write_scores(cur, resume_id, run_id, market_fit, pct, sal_min, sal_max, honesty_match,\n"
    "                             matched_jobs_count=len(matches), top_jobs_considered=len(market_job_ids),\n"
    "                             plausibility_penalty=pl_pen,\n"
    "                             confidence_score=conf_score,\n"
    "                             confidence_flags=conf_flags)\n"
)
if old_scores_call in s:
    s = s.replace(old_scores_call, new_scores_call)
else:
    # fallback: if user reformatted call slightly, we do a safer targeted insertion near the call
    s = re.sub(
        r"(write_scores\(cur,\s*resume_id,\s*run_id,\s*market_fit,\s*pct,\s*sal_min,\s*sal_max,\s*honesty_match,\s*\n\s*matched_jobs_count=len\(matches\),\s*top_jobs_considered=len\(market_job_ids\)\))",
        r"\1,\n                             plausibility_penalty=pl_pen,\n                             confidence_score=conf_score,\n                             confidence_flags=conf_flags",
        s,
        count=1
    )

# ------------------------------------------------------------
# B) Surgical extraction recall bump (safe)
#    - add a "sanitized padded" version so aliases match through punctuation
#    - keep the existing regex approach (high precision)
# ------------------------------------------------------------

# Find resume_to_skills body start anchor and inject a couple lines after t_norm
anchor = "    t_norm = normalize_for_matching(resume_text)\n"
if anchor not in s:
    raise SystemExit("❌ Could not find anchor: t_norm = normalize_for_matching(resume_text)")

if "t_sanitized" not in s[s.find(anchor):s.find(anchor)+400]:
    inject = (
        "    # extra normalized stream for higher recall (punctuation-insensitive)\n"
        "    t_sanitized = re.sub(r\"[^a-z0-9]+\", \" \", t_norm)\n"
        "    padded_sanitized = \" \" + re.sub(r\"\\s+\", \" \", t_sanitized).strip() + \" \"\n"
    )
    s = s.replace(anchor, anchor + inject)

# Now enhance the alias hit logic: if regex misses, also try sanitized token match for non-tiny aliases.
# Locate: for pat, sid, alias in alias_patterns:
loop_hdr = "    for pat, sid, alias in alias_patterns:\n"
if loop_hdr not in s:
    raise SystemExit("❌ Could not find alias_patterns loop header in resume_to_skills().")

# We patch just inside that loop: after m = pat.search(t_norm)
# Current code:
#     m = pat.search(t_norm)
#     if m:
# ...
pat_line = "        m = pat.search(t_norm)\n"
if pat_line not in s:
    raise SystemExit("❌ Could not find 'm = pat.search(t_norm)' line to patch recall.")

# Only patch once
if "padded_sanitized" not in s[s.find(pat_line):s.find(pat_line)+250]:
    replacement = (
        "        m = pat.search(t_norm)\n"
        "        # fallback: punctuation-insensitive token match (avoid tiny aliases)\n"
        "        if not m and alias and len(alias) >= 3:\n"
        "            tok = \" \" + alias + \" \"\n"
        "            if tok in padded_sanitized:\n"
        "                # synthesize a match-like span by searching in t_norm for evidence window\n"
        "                m = re.search(re.escape(alias), t_norm)\n"
    )
    s = s.replace(pat_line, replacement)

p.write_text(s, encoding="utf-8")
print("✅ Patched score_resume(): baseline + market/matched stats calls + write_scores args + extraction recall bump.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
