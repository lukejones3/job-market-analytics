#!/usr/bin/env bash
set -euo pipefail

# hard fail if not in venv
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "❌ Not in a virtualenv. Run: source .venv/bin/activate"
  exit 1
fi

PYBIN="$VIRTUAL_ENV/bin/python"
FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.candstrict_$ts"

"$PYBIN" - <<'PY'
from pathlib import Path

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# 1) Insert strict gate helpers (only once), right before def record_skill_candidates if possible.
gate_block = r'''
# -----------------------------
# Candidate skill strict gates
# -----------------------------
_CANDIDATE_STRICT_GATES = {
    # generic fluff words you do NOT want as "skills"
    "attitude","passion","learning","education","knowledge","clients","client","benefits",
    "abilities","ability","skills","skill","experience","preferred qualifications","required qualifications",
    "strong analytical","verbal","written","grow","family support","continuing education",
    "technologies on the job","experience is useful","skills matter",
}

# Reject EEO/boilerplate and protected-class words (not skills)
_CANDIDATE_STRICT_REJECT_RE = re.compile(
    r"(?i)\b("
    r"equal opportunity|eeo|accommodation|drug[- ]?free|background check|"
    r"race|color|religion|creed|sex|gender|pregnancy|marital status|"
    r"national origin|sexual orientation|genetic information|ancestry|disability|veteran"
    r")\b"
)

def _looks_like_tool_or_hard_skill(raw: str, norm: str) -> bool:
    # length gates
    if len(norm) < 2 or len(norm) > 40:
        return False

    # word-count gate (kills sentences)
    if len(norm.split()) > 4:
        return False

    if norm in _CANDIDATE_STRICT_GATES:
        return False
    if _CANDIDATE_STRICT_REJECT_RE.search(norm):
        return False

    # obvious tool-ish signals
    if re.fullmatch(r"[a-z]{2,10}\d{0,2}", norm):   # acronyms like sql, ga4, dbt, jira
        return True
    if any(ch in raw for ch in "+/&.-"):            # C++, A/B, Power-BI, etc.
        return True
    if any(ch.isdigit() for ch in raw):             # GA4, ISO27001, etc.
        return True
    # TitleCase / CamelCase signal
    raw_str = raw.strip()
    if raw_str[:1].isupper() and any(c.isupper() for c in raw_str[1:]):
        return True

    # allow a tiny curated set of hard-skill nouns (keep small!)
    allow = {"statistics","forecasting","modeling","regression","etl","api","dashboarding","visualization"}
    if norm in allow:
        return True

    return False
'''.lstrip("\n")

if "_CANDIDATE_STRICT_GATES" not in s:
    anchor = "def record_skill_candidates("
    idx = s.find(anchor)
    if idx == -1:
        raise SystemExit("❌ Could not find def record_skill_candidates(...) to anchor insertion.")
    s = s[:idx] + gate_block + "\n\n" + s[idx:]

# 2) Inject gating line inside record_skill_candidates candidate loop
needle = "for raw, norm in _extract_candidate_phrases_from_line(ln):"
pos = s.find(needle)
if pos == -1:
    raise SystemExit("❌ Could not find candidate loop: " + needle)

# determine indent of the 'for' line
line_start = s.rfind("\n", 0, pos) + 1
line = s[line_start : s.find("\n", pos)]
indent = line.split("for")[0]

gate_line = f"{indent}    if not _looks_like_tool_or_hard_skill(raw, norm):\n{indent}        continue\n"
# only add if not already present
after = s[pos : pos + 400]
if "_looks_like_tool_or_hard_skill(raw, norm)" not in after:
    insert_at = s.find("\n", pos) + 1
    s = s[:insert_at] + gate_line + s[insert_at:]

p.write_text(s, encoding="utf-8")
print("✅ Strict gating added: candidates must look like tools/hard-skills; EEO/fluff rejected.")
PY

"$PYBIN" -m py_compile "$FILE"
echo "✅ Compiles. Backup: $FILE.bak.candstrict_$ts"
