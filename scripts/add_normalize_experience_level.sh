#!/usr/bin/env bash
set -euo pipefail

FILE="python/score_resume.py"
TS="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.normexp_${TS}"
echo "🧾 Backup: ${FILE}.bak.normexp_${TS}"

python3 << 'PY'
from pathlib import Path

p = Path("python/score_resume.py")
s = p.read_text()

if "def normalize_experience_level(" in s:
    print("ℹ️ normalize_experience_level already exists.")
    raise SystemExit(0)

insert_point = s.find("def percentile_from_distribution")
if insert_point == -1:
    raise SystemExit("❌ Could not find safe anchor point.")

helper = """

def normalize_experience_level(raw, role_name):
    text = f"{raw or ''} {role_name or ''}".lower()

    if any(k in text for k in ["senior", "sr.", "lead", "principal"]):
        return "senior"
    if any(k in text for k in ["mid", "ii", "iii"]):
        return "mid"
    if any(k in text for k in ["associate", "analyst i"]):
        return "associate"
    if any(k in text for k in ["junior", "jr.", "entry"]):
        return "entry"

    return "any"
"""

s = s[:insert_point] + helper + s[insert_point:]
p.write_text(s)

print("✅ normalize_experience_level added cleanly.")
PY

python3 -m py_compile "$FILE"
echo "✅ Compile OK"
