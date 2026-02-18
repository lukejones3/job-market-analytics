#!/usr/bin/env bash
set -euo pipefail
cd ~/github/job-market-analytics
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.confblock_${ts}"
echo "🧾 Backup: ${FILE}.bak.confblock_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# We replace the whole injected confidence block (broken) with a known-good one.
pat = re.compile(
    r"(?ms)"
    r"^\s*# source breakdown of extracted skills \(alias/section/heuristic\).*?"
    r"^\s*\)\s*$"   # end of compute_confidence_score call (currently broken, but we’ll match until a lone ')')
)

# If the above doesn't match (because your broken call isn't ending cleanly),
# match from the comment through the final line of the call block (the line containing 'sal_min=' etc).
pat2 = re.compile(
    r"(?ms)"
    r"^\s*# source breakdown of extracted skills \(alias/section/heuristic\).*?"
    r"^\s*\)\s*$"
)

# We’ll do a more robust slice using the known line anchors in your snippet:
start = re.search(r"(?m)^\s*# source breakdown of extracted skills \(alias/section/heuristic\)\s*$", s)
if not start:
    raise SystemExit("❌ Could not find the confidence block start comment.")

# Find end at the line right after the compute_confidence_score call closes.
# We'll anchor on: 'conf_score, conf_flags = compute_confidence_score(' then find the next line that is only whitespace + ')'
call = re.search(r"(?m)^\s*conf_score,\s*conf_flags\s*=\s*compute_confidence_score\(\s*$", s[start.start():])
if not call:
    # your current file has it unindented; match without leading spaces
    call = re.search(r"(?m)^conf_score,\s*conf_flags\s*=\s*compute_confidence_score\(\s*$", s[start.start():])
    if not call:
        raise SystemExit("❌ Could not find compute_confidence_score( line after the comment.")

call_abs = start.start() + call.start()

# Find the closing paren line for the call (a line that is just indentation + ')')
close = re.search(r"(?m)^\s*\)\s*$", s[call_abs:])
if not close:
    raise SystemExit("❌ Could not find closing ')' line for compute_confidence_score call.")

end_abs = call_abs + close.end()

# Indentation level in score_resume() at this point is 16 spaces in your file snippet.
ind = " " * 16

replacement = f"""{ind}# source breakdown of extracted skills (alias/section/heuristic)
{ind}cur.execute(\"\"\"
{ind}  SELECT source, COUNT(*) AS n
{ind}  FROM resume_skills
{ind}  WHERE resume_id=%s
{ind}  GROUP BY source
{ind}\"\"\", (resume_id,))
{ind}skill_source_counts = {{r["source"]: int(r["n"]) for r in cur.fetchall()}}

{ind}conf_score, conf_flags = compute_confidence_score(
{ind}    resume_text=resume_text,
{ind}    skills_found=len(resume_skill_ids),
{ind}    skill_source_counts=skill_source_counts,
{ind}    market_jobs=len(market_job_ids),
{ind}    matched_jobs=len(matches),
{ind}    sal_min=sal_min, sal_max=sal_max
{ind})
"""

s2 = s[:start.start()] + replacement + s[end_abs:]
p.write_text(s2, encoding="utf-8")
print("✅ Replaced broken confidence block with a clean, correctly-indented version.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
