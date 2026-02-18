#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
PY="${REPO}/.venv/bin/python"
ts="$(date +%Y%m%d_%H%M%S)"

cp "$FILE" "${FILE}.bak.safe_${ts}"
echo "🧾 Backup: ${FILE}.bak.safe_${ts}"

"$PY" - <<'PY'
from pathlib import Path
import re

p = Path.home() / "github" / "job-market-analytics" / "python" / "score_resume.py"
s = p.read_text(encoding="utf-8")

# ------------------------------------------------------------
# 1) Inject plausibility/confidence block right before CALL SITE
#    (only call-site line; no need to find closing paren)
# ------------------------------------------------------------
m_call = re.search(r"(?m)^\s+write_scores\(", s)
if not m_call:
    raise SystemExit("❌ Could not find write_scores( call site.")

# Don't inject if already present nearby
window = s[max(0, m_call.start()-2000):m_call.start()]
if "compute_plausibility_penalty(" not in window:
    inject = """
                # Fetch resume skill names (for plausibility heuristics)
                cur.execute(\"\"\"
                  SELECT s.skill_name
                  FROM resume_skills rs
                  JOIN skills s ON s.skill_id = rs.skill_id
                  WHERE rs.resume_id=%s
                \"\"\", (resume_id,))
                resume_skill_names = [r[\"skill_name\"] for r in cur.fetchall()]

                # Plausibility + flags
                pl_pen, pl_flags = compute_plausibility_penalty(resume_text, resume_skill_names, experience_level)
                for f in pl_flags:
                    write_run_flag(cur, resume_id, run_id, "plausibility", f, None)

                # Confidence + flags
                conf_score, conf_flags = compute_confidence_score(
                    resume_text=resume_text,
                    skills_found=len(resume_skill_ids),
                    market_jobs=len(market_job_ids),
                    matched_jobs=len(matches),
                    sal_min=smin, sal_max=smax
                )
                for f in conf_flags:
                    write_run_flag(cur, resume_id, run_id, "confidence", f, None)

""".lstrip("\n")
    s = s[:m_call.start()] + inject + s[m_call.start():]

# ------------------------------------------------------------
# 2) Patch write_scores() signature to accept new params w/ defaults
# ------------------------------------------------------------
m_def = re.search(r"(?m)^def\s+write_scores\((.*?)\):\s*$", s)
if not m_def:
    raise SystemExit("❌ Could not find def write_scores(...) definition line.")

sig = m_def.group(0)
if "plausibility_penalty" not in sig:
    # Append optional args at end with defaults (keeps call-sites valid)
    sig_new = sig.replace("):", ", plausibility_penalty: int = 0, confidence_score: int = 100, confidence_flags=None):")
    s = s[:m_def.start()] + sig_new + s[m_def.end():]

# ------------------------------------------------------------
# 3) Patch INSERT INTO resume_scores to include new columns + values
#    We modify the SQL inside write_scores() block.
# ------------------------------------------------------------
# Find the write_scores() function body block (roughly) by slicing from def line
# to next "def " at column 0.
start = s.find(sig_new if 'sig_new' in locals() else sig)
if start == -1:
    start = m_def.start()
m_next = re.search(r"(?m)^def\s+", s[m_def.end():])
end = m_def.end() + (m_next.start() if m_next else len(s))

block = s[start:end]

# Update column list
if "plausibility_penalty" not in block:
    block = block.replace(
        "matched_jobs_count, top_jobs_considered",
        "matched_jobs_count, top_jobs_considered,\n        plausibility_penalty, confidence_score, confidence_flags"
    )

# Update VALUES tuple placeholders
# Old: VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
# New: add 3 placeholders at end
block = re.sub(
    r"VALUES\s*\(\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*,\s*%s\s*\)",
    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
    block
)

# Update execute params tuple in Python call
# Old: (resume_id, run_id, market_fit, percentile, sal_min, sal_max, honesty_match, matched_jobs_count, top_jobs_considered)
# New: add 3 args
if "confidence_flags" not in block:
    block = block.replace(
        "(resume_id, run_id, market_fit, percentile, sal_min, sal_max, honesty_match, matched_jobs_count, top_jobs_considered))",
        "(resume_id, run_id, market_fit, percentile, sal_min, sal_max, honesty_match, matched_jobs_count, top_jobs_considered, plausibility_penalty, confidence_score, confidence_flags))"
    )

# Ensure confidence_flags is json-safe
if "confidence_flags = []" not in block:
    # Add a tiny guard right before cur.execute(""" INSERT ...
    block = block.replace(
        "    cur.execute(\"\"\"",
        "    if confidence_flags is None:\n        confidence_flags = []\n    cur.execute(\"\"\""
    )

# Splice modified block back
s = s[:start] + block + s[end:]

p.write_text(s, encoding="utf-8")
print("✅ Patched: injected plausibility/confidence + extended write_scores() + INSERT columns.")
PY

"$PY" -m py_compile "$FILE"
echo "✅ Compile OK"
