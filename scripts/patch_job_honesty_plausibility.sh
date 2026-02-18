#!/usr/bin/env bash
set -euo pipefail
FILE="sql/job_honesty.sql"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.plaus_${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("sql/job_honesty.sql")
s = p.read_text(encoding="utf-8")

# 1) Ensure calc CTE has plausibility_penalty
if "AS plausibility_penalty" not in s:
    # Insert into calc SELECT list right after eeo_penalty block (best anchor)
    anchor = re.search(r"(?s)(--\s*-+\s*EEO dominance penalty\s*-+.*?\)\s*::int\s+AS\s+eeo_penalty,\s*)", s)
    if not anchor:
        raise SystemExit("❌ Could not find eeo_penalty anchor in sql/job_honesty.sql")

    plaus_block = r"""
      -- ---------------- Skill-to-level plausibility penalty ----------------
      (
        WITH senior_stack AS (
          SELECT unnest(ARRAY[
            'airflow','kubernetes','terraform','spark','pyspark','kafka',
            'databricks','snowflake','dbt','docker','mlops',
            'aws','gcp','azure','redshift','bigquery'
          ]) AS sk
        ),
        counts AS (
          SELECT
            COUNT(*) FILTER (WHERE js.skill_priority='required') AS senior_req,
            COUNT(*) AS senior_any
          FROM job_skills js
          JOIN skills sk ON sk.skill_id = js.skill_id
          JOIN senior_stack ss ON lower(sk.skill_name) = ss.sk
          WHERE js.job_id = s.job_id
        )
        SELECT
          CASE
            WHEN s.experience_level = 'entry'
              THEN LEAST(25, COALESCE(senior_req,0)*6 + COALESCE(senior_any,0)*2)
            WHEN s.experience_level IN ('associate','mid')
              THEN LEAST(18, COALESCE(senior_req,0)*4 + COALESCE(senior_any,0)*1)
            ELSE 0
          END
        FROM counts
      )::int AS plausibility_penalty,
"""
    s = s[:anchor.end()] + plaus_block + s[anchor.end():]

# 2) Ensure honesty_score subtracts plausibility_penalty
# Add "- c.plausibility_penalty" if missing
if re.search(r"honesty_score.*-\s*c\.plausibility_penalty", s, flags=re.S):
    pass
else:
    # Insert into the GREATEST(0, 100 - ...) expression in final CTE
    # Anchor after eeo_penalty subtraction
    pat = r"(-\s*LEAST\(c\.eeo_penalty,\s*10\)\s*)"
    m = re.search(pat, s)
    if not m:
        raise SystemExit("❌ Could not find honesty_score eeo subtraction anchor.")
    s = s[:m.end()] + "\n        - c.plausibility_penalty\n" + s[m.end():]

# 3) Ensure flags include plausibility mismatch when penalty meaningful
if "senior_stack_mismatch" not in s:
    # Insert near the end of flags building, after eeo flag is fine
    flag_anchor = re.search(r"(\|\|\s*CASE\s+WHEN\s+c\.eeo_penalty\s*>\s*0\s+THEN\s+to_jsonb\('eeo_dominates_posting'\).*?\)\s*)", s, flags=re.S)
    if not flag_anchor:
        raise SystemExit("❌ Could not find eeo_dominates_posting flag anchor.")
    add_flag = "\n        || CASE WHEN c.plausibility_penalty >= 10 THEN to_jsonb('senior_stack_mismatch') ELSE '[]'::jsonb END\n"
    s = s[:flag_anchor.end()] + add_flag + s[flag_anchor.end():]

p.write_text(s, encoding="utf-8")
print("✅ Patched sql/job_honesty.sql plausibility (compute + subtract + flag).")
PY

psql -v ON_ERROR_STOP=1 job_analytics -f "$FILE"
psql job_analytics -c "SELECT * FROM refresh_job_honesty(NULL, 6);"
echo "✅ Done. Backup: ${FILE}.bak.plaus_${ts}"
