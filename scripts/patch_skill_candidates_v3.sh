#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "❌ Not in venv. Run: source .venv/bin/activate"
  exit 1
fi

PYBIN="$VIRTUAL_ENV/bin/python"
FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.cand_v3_$ts"

"$PYBIN" - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# Must exist from earlier patch
if "def _looks_like_tool_or_hard_skill" not in s:
    raise SystemExit("❌ Could not find _looks_like_tool_or_hard_skill().")

# 1) Add a hard reject regex (EEO + geo + generic fluff) if not present
if "_CANDIDATE_HARD_REJECT_RE" not in s:
    insert = r'''
# Hard rejects: EEO/protected-class boilerplate, geo fragments, generic fluff
_CANDIDATE_HARD_REJECT_RE = re.compile(
    r"\b("
    r"equal employment|eeo|affirmative action|protected class|"
    r"race|color|religion|sex|gender|pregnan|national origin|disabilit|veteran|genetic|"
    r"age over 40|sexual orientation|marital status|"
    r")\b",
    flags=re.IGNORECASE
)
'''.lstrip("\n")
    # place it near other candidate regexes
    anchor = "_CANDIDATE_STRICT_REJECT_RE"
    idx = s.find(anchor)
    if idx == -1:
        raise SystemExit("❌ Could not find insertion anchor for candidate reject regexes.")
    s = s[:idx] + insert + "\n" + s[idx:]

# 2) Strengthen _looks_like_tool_or_hard_skill with additional rules
fn = re.search(r"def _looks_like_tool_or_hard_skill\(raw: str, norm: str\) -> bool:\n(?s)(.*?)\n\n", s)
if not fn:
    raise SystemExit("❌ Could not parse _looks_like_tool_or_hard_skill body.")

body = fn.group(1)

# Ensure these rules exist (idempotent-ish)
needed_rules = [
    ("if _CANDIDATE_HARD_REJECT_RE.search(norm):", "    if _CANDIDATE_HARD_REJECT_RE.search(norm):\n        return False\n"),
    ("if re.fullmatch(r\"[a-z]{2}\", norm):", "    # reject 2-letter tokens (IL/CA/etc)\n    if re.fullmatch(r\"[a-z]{2}\", norm):\n        return False\n"),
    ("if re.fullmatch(r\"(usa|us|u\\.s\\.|china|japan|india|canada|uk)\", norm):",
     "    # reject common country tokens\n    if re.fullmatch(r\"(usa|us|u\\.s\\.|china|japan|india|canada|uk)\", norm):\n        return False\n"),
    ("if norm.endswith(\" skills\"):", "    # reject 'X skills' artifact (tool+skills, soft phrase)\n    if norm.endswith(\" skills\"):\n        return False\n"),
]

for needle, rule in needed_rules:
    if needle not in body:
        # insert near top (after empty/stop checks usually)
        # safest: insert after the first `if not norm:` block if present, else after function signature line later
        pass

# We'll inject just after the first occurrence of "if not norm:" block
insertion_point = body.find("if not norm:")
if insertion_point == -1:
    raise SystemExit("❌ Could not find 'if not norm' in _looks_like_tool_or_hard_skill().")

# Find end of that if-block (next blank line or next comment)
# Simple: insert after the first return False following it
mret = re.search(r"if not norm:\n\s+return False\n", body)
if not mret:
    raise SystemExit("❌ Could not locate return after 'if not norm'.")

inject = ""
for needle, rule in needed_rules:
    if needle not in body:
        inject += rule + "\n"

if inject:
    body2 = body[:mret.end()] + "\n" + inject + body[mret.end():]
    s = s.replace(body, body2)

# 3) Add extra stop-words directly into ignore list if that exists
# Look for: ignore = set([...]) or ignore = {...}
if "ignore = set([" in s:
    # add a few more high-value ignores if not present
    for term in ["financial", "management", "integrity", "hungry", "connect", "passion", "doe", "mri", "strong analytical", "powerpoint skills", "attitude is everything", "other internal end-users"]:
        if f'"{term}"' not in s and f"'{term}'" not in s:
            s = s.replace("ignore = set([", "ignore = set([\n            " + repr(term) + ",", 1)

p.write_text(s, encoding="utf-8")
print("✅ Patch v3 applied (EEO/geo/generic cleanup + 'skills' artifact reject).")
PY

"$PYBIN" -m py_compile "$FILE"
echo "✅ Compiles. Backup: $FILE.bak.cand_v3_$ts"
