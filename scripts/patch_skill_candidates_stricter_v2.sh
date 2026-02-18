#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "❌ Not in venv. Run: source .venv/bin/activate"
  exit 1
fi

PYBIN="$VIRTUAL_ENV/bin/python"
FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.candstrict_v2_$ts"

"$PYBIN" - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# Find the strict gate block (inserted earlier)
if "_CANDIDATE_STRICT_GATES" not in s or "def _looks_like_tool_or_hard_skill" not in s:
    raise SystemExit("❌ Strict gate block not found. (Did you run the first strict patch?)")

# 1) Expand stoplist set (safely by inserting extra items if missing)
extra_stop = [
    "and qualifications","other requirements","desired qualifications","minimum requirements",
    "required qualifications","preferred qualifications","soft skills","in writing",
    "written communication","written communication skills","organizational skills",
    "development opportunities","development opportunities. our broad",
    "their families. at caci","with external organizations","including job-related skills",
    "job-related skills","qualifications","requirements","opportunities",
    "attitude is everything","strong analytical","financial","retirement","passion",
    "level","develop","maintain","including","and","with"
]

# Patch: add these strings into the _CANDIDATE_STRICT_GATES set literal
m = re.search(r"_CANDIDATE_STRICT_GATES\s*=\s*\{(?s)(.*?)\n\}", s)
if not m:
    raise SystemExit("❌ Could not locate _CANDIDATE_STRICT_GATES literal.")

block_body = m.group(1)
for term in extra_stop:
    if f'"{term}"' not in block_body and f"'{term}'" not in block_body:
        # insert near top of set body
        block_body = block_body + f'\n    "{term}",'

s = s[:m.start(1)] + block_body + s[m.end(1):]

# 2) Strengthen reject regex to include headers/boilerplate signals
reject_pat = r"_CANDIDATE_STRICT_REJECT_RE\s*=\s*re\.compile\("
m2 = re.search(reject_pat, s)
if not m2:
    raise SystemExit("❌ Could not locate _CANDIDATE_STRICT_REJECT_RE.")

# If our new keywords aren't in there, extend it (simple string insert into the pattern text)
if "requirements" not in s[m2.start():m2.start()+800].lower():
    s = s.replace(
        r")\b" + "\n)",
        r"|requirements|qualification|qualifications|opportunities|benefits|equal employment|"
        r"job[- ]?related|"
        r")\b" + "\n)",
        1
    )

# 3) Add hard structural rejects inside _looks_like_tool_or_hard_skill
fn_anchor = "def _looks_like_tool_or_hard_skill(raw: str, norm: str) -> bool:"
idx = s.find(fn_anchor)
if idx == -1:
    raise SystemExit("❌ Could not find _looks_like_tool_or_hard_skill definition.")

# Insert rules after word-count gate, only if not already present
insert_rules = r'''
    # kill sentence fragments / boilerplate labels
    if "." in raw or ";" in raw:
        return False
    if norm.startswith(("and ", "with ", "including ")):
        return False
    if any(k in norm for k in ("requirement", "qualification", "opportunit", "benefit")):
        return False
'''.lstrip("\n")

# Find a good insertion point: right after the word-count gate block
needle = "    # word-count gate (kills sentences)\n    if len(norm.split()) > 4:\n        return False\n"
if needle not in s:
    raise SystemExit("❌ Could not find insertion needle in _looks_like_tool_or_hard_skill.")
if "kill sentence fragments" not in s:
    s = s.replace(needle, needle + "\n" + insert_rules, 1)

p.write_text(s, encoding="utf-8")
print("✅ Patched v2: stronger boilerplate/fragment rejection.")
PY

"$PYBIN" -m py_compile "$FILE"
echo "✅ Compiles. Backup: $FILE.bak.candstrict_v2_$ts"
