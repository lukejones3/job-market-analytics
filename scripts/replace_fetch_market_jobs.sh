#!/usr/bin/env bash
set -euo pipefail

FILE="python/score_resume.py"
TS="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.fetchreplace_${TS}"
echo "🧾 Backup: ${FILE}.bak.fetchreplace_${TS}"

python3 << 'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text()

# Locate entire fetch_market_jobs function block
m = re.search(
    r"def fetch_market_jobs\([\s\S]*?\n\s*return \[r\[\"job_id\"\] for r in cur\.fetchall\(\)\]\n",
    s
)

if not m:
    raise SystemExit("❌ Could not find fetch_market_jobs() block.")

new_block = """
def fetch_market_jobs(cur, role_id, location_id,
                      workplace_type, experience_level,
                      months_back, top_jobs):

    params = []

    query = '''
      SELECT
        jp.job_id,
        jp.experience_level,
        r.role_name
      FROM job_postings jp
      LEFT JOIN roles r ON r.role_id = jp.role_id
      WHERE 1=1
    '''

    if role_id:
        query += " AND jp.role_id = %s"
        params.append(role_id)

    if location_id:
        query += " AND jp.location_id = %s"
        params.append(location_id)

    if workplace_type and workplace_type != "any":
        query += " AND COALESCE(jp.workplace_type,'any') = %s"
        params.append(workplace_type)

    if months_back:
        query += " AND COALESCE(jp.posted_date, jp.date_found, now()) >= now() - (%s || ' months')::interval"
        params.append(months_back)

    query += " ORDER BY COALESCE(jp.posted_date, jp.date_found, now()) DESC LIMIT %s"
    params.append(top_jobs)

    cur.execute(query, tuple(params))
    rows = cur.fetchall()

    if experience_level and experience_level != "any":
        filtered = []
        for r in rows:
            raw = r.get("experience_level")
            role_name = r.get("role_name")
            if normalize_experience_level(raw, role_name) == experience_level:
                filtered.append(r["job_id"])
        return filtered

    return [r["job_id"] for r in rows]
"""

s = s[:m.start()] + new_block + s[m.end():]
p.write_text(s)

print("✅ fetch_market_jobs fully replaced cleanly.")
PY

python3 -m py_compile "$FILE"
echo "✅ Compile OK"
