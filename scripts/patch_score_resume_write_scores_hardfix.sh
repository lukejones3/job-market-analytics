#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
PY="${REPO}/.venv/bin/python"
ts="$(date +%Y%m%d_%H%M%S)"

cp "$FILE" "${FILE}.bak.write_scores_hardfix_${ts}"
echo "🧾 Backup: ${FILE}.bak.write_scores_hardfix_${ts}"

"$PY" - <<'PY'
from pathlib import Path
import re

p = Path.home() / "github" / "job-market-analytics" / "python" / "score_resume.py"
s = p.read_text(encoding="utf-8")

# Ensure Json import exists
if re.search(r"from\s+psycopg2\.extras\s+import\s+.*\bJson\b", s) is None:
    m = re.search(r"(?m)^from\s+psycopg2\.extras\s+import\s+(.+)$", s)
    if m:
        line = m.group(0)
        if "Json" not in line:
            s = s.replace(line, line + ", Json", 1)
    else:
        # fallback: add near top
        s = "from psycopg2.extras import Json\n" + s

# Replace the entire write_scores() function block
pat = re.compile(r"(?s)^def write_scores\([^\n]*\n(?:.*?\n)*?(?=^def write_gaps\()", re.M)
m = pat.search(s)
if not m:
    raise SystemExit("❌ Could not locate write_scores() block to replace (expected def write_gaps after it).")

replacement = r'''
def write_scores(cur, resume_id: str, run_id: str, market_fit: int, percentile: Optional[float],
                 sal_min: Optional[float], sal_max: Optional[float], honesty_match: Optional[int],
                 matched_jobs_count: int, top_jobs_considered: int,
                 plausibility_penalty: int = 0,
                 confidence_score: int = 100,
                 confidence_flags=None):
    """
    Writes one row into resume_scores for (resume_id, run_id).
    Must match resume_scores schema exactly.
    """
    if confidence_flags is None:
        confidence_flags = []

    cur.execute("""
      INSERT INTO resume_scores (
        resume_id, run_id,
        market_fit_score, market_percentile,
        salary_est_min, salary_est_max,
        honesty_match_score,
        matched_jobs_count, top_jobs_considered,
        plausibility_penalty, confidence_score, confidence_flags
      )
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
      ON CONFLICT (resume_id, run_id) DO UPDATE SET
        market_fit_score      = EXCLUDED.market_fit_score,
        market_percentile     = EXCLUDED.market_percentile,
        salary_est_min        = EXCLUDED.salary_est_min,
        salary_est_max        = EXCLUDED.salary_est_max,
        honesty_match_score   = EXCLUDED.honesty_match_score,
        matched_jobs_count    = EXCLUDED.matched_jobs_count,
        top_jobs_considered   = EXCLUDED.top_jobs_considered,
        plausibility_penalty  = EXCLUDED.plausibility_penalty,
        confidence_score      = EXCLUDED.confidence_score,
        confidence_flags      = EXCLUDED.confidence_flags,
        created_at            = now()
    """, (
        resume_id, run_id,
        market_fit, percentile,
        sal_min, sal_max,
        honesty_match,
        matched_jobs_count, top_jobs_considered,
        plausibility_penalty, confidence_score, Json(confidence_flags)
    ))
'''.lstrip()

s = s[:m.start()] + replacement + s[m.end():]
p.write_text(s, encoding="utf-8")
print("✅ Hard-replaced write_scores() with correct 12-column INSERT.")
PY

"$PY" -m py_compile "$FILE"
echo "✅ Compile OK"
