#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
bak="${FILE}.bak.explevel_${ts}"
cp "$FILE" "$bak"
echo "🧾 Backup: $bak"

python3 - <<'PY'
from pathlib import Path
import re

p = Path.home() / "github/job-market-analytics/python/score_resume.py"
s = p.read_text(encoding="utf-8")

# Inject helper once
if "def normalize_experience_level(" not in s:
    marker = "\n\ndef fetch_market_jobs("
    i = s.find(marker)
    if i == -1:
        raise SystemExit("❌ Could not find fetch_market_jobs() to anchor injection.")

    inject = r'''
def normalize_experience_level(raw: str, role_name: str) -> str:
    x = (raw or "").strip().lower()
    rn = (role_name or "").strip().lower()

    if x in {"entry","associate","mid","senior"}:
        return x

    # infer from role title
    if any(k in rn for k in ["intern", "internship", "new grad", "university"]):
        return "entry"
    if any(k in rn for k in ["junior", "jr", "associate", "analyst i", "analyst 1"]):
        return "associate"
    if any(k in rn for k in ["senior", "sr", "lead", "principal", "staff"]):
        return "senior"
    if any(k in rn for k in ["manager"]):
        return "mid"

    return "unknown"
'''.strip("\n") + "\n\n"

    s = s[:i] + inject + s[i:]

# Patch fetch_market_jobs query: select role_name so we can infer exp when filtering
# Find the fetch_market_jobs SQL that selects FROM job_postings jp ...
# We’ll minimally add roles join + use normalize in python filtering if needed.
# Safer approach: patch the WHERE clause logic in python by filtering after query.

# Add role_name to fetch_market_jobs select if missing:
if "FROM job_postings jp" not in s:
    raise SystemExit("❌ Could not find job_postings query in fetch_market_jobs().")

# Ensure fetch_market_jobs pulls role_name
if "LEFT JOIN roles r" not in s[s.find("def fetch_market_jobs"):s.find("def fetch_market_jobs")+1200]:
    # Insert join in the fetch_market_jobs query block
    s = s.replace(
        "      FROM job_postings jp\n",
        "      FROM job_postings jp\n      LEFT JOIN roles r ON r.role_id = jp.role_id\n"
    )
if "r.role_name" not in s[s.find("def fetch_market_jobs"):s.find("def fetch_market_jobs")+1200]:
    s = s.replace(
        "      SELECT\n        jp.job_id\n",
        "      SELECT\n        jp.job_id,\n        r.role_name\n"
    )

# Now patch the return to filter by normalized experience level when args.level != 'any'
# Locate the end of fetch_market_jobs where it returns job_ids.
m = re.search(r"def fetch_market_jobs\([\s\S]*?\n\s*return \[r\[\"job_id\"\] for r in cur\.fetchall\(\)\]\n", s)
if not m:
    raise SystemExit("❌ Could not locate fetch_market_jobs return line for patching.")

block = s[m.start():m.end()]
if "normalize_experience_level" not in block:
    block_new = block.replace(
        '    return [r["job_id"] for r in cur.fetchall()]\n',
        '''    rows = cur.fetchall()
    # If experience_level filter is specific, enforce via normalized title inference too
    if experience_level and experience_level != "any":
        keep = []
        for r in rows:
            raw = r.get("experience_level") if isinstance(r, dict) else None
            rn = r.get("role_name") if isinstance(r, dict) else None
            if normalize_experience_level(raw, rn) == experience_level:
                keep.append(r["job_id"])
        return keep
    return [r["job_id"] for r in rows]\n'''
    )
    s = s[:m.start()] + block_new + s[m.end():]

p.write_text(s, encoding="utf-8")
print("✅ Patched: experience_level normalization for fetch_market_jobs().")
PY

python3 -m py_compile "$FILE"
echo "✅ Compile OK"
