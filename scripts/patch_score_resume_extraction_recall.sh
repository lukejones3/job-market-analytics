#!/usr/bin/env bash
set -euo pipefail
cd "${HOME}/github/job-market-analytics"

FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.extractrecall_${ts}"
echo "🧾 Backup: ${FILE}.bak.extractrecall_${ts}"

python3 - <<'PY'
from pathlib import Path
import re

p = Path("python/score_resume.py")
s = p.read_text(encoding="utf-8")

# Find the extraction function. We’ll support common names.
fn_names = ["extract_skills_from_text", "extract_resume_skills", "extract_skills"]
fn_pos = None
fn_name = None
for name in fn_names:
    m = re.search(rf"(?m)^def {re.escape(name)}\s*\(", s)
    if m:
        fn_pos = m.start()
        fn_name = name
        break

if fn_pos is None:
    raise SystemExit("❌ Could not find a skills extraction function (extract_skills_from_text / extract_resume_skills / extract_skills).")

# Extract that function block
m = re.search(rf"(?s)^def {re.escape(fn_name)}\s*\(.*?\):\n(    .*\n)+?(?=\ndef |\Z)", s[fn_pos:])
if not m:
    raise SystemExit("❌ Could not parse extraction function block.")
start = fn_pos
end = fn_pos + m.end()
old_block = s[start:end]

# If already patched, exit
if "AGGRESSIVE SKILLS SECTION PARSING" in old_block:
    print("ℹ️ extraction recall patch already present")
    raise SystemExit(0)

# Replace body with a safer, higher-recall version but same signature.
sig = re.match(rf"(?s)^(def {re.escape(fn_name)}\s*\(.*?\):)", old_block).group(1)

new_block = sig + r'''
    """
    Higher recall, still safe:
      - normalize text so aliases match through punctuation/casing
      - aggressively parse Skills/Tools/Technologies sections
      - match multiword aliases by padded-substring (fast & precise)
    """
    if not text:
        return []

    # --- normalize ---
    raw = text
    t = raw.lower()
    t = re.sub(r"[•·\t]", " ", t)
    t = re.sub(r"[|/,:;()\[\]{}]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    padded = " " + t + " "

    # --- pull skills-ish section lines (bonus text) ---
    # We take short window after headings like: Skills, Tools, Technologies, Technical Skills
    bonus_chunks = []
    lines = raw.splitlines()
    key_re = re.compile(r"^\s*(skills?|tools?|technologies|tech stack|technical skills?)\s*[:\-]?\s*$", re.I)

    i = 0
    while i < len(lines):
        if key_re.match(lines[i] or ""):
            # capture next ~8 lines or until a blank gap
            chunk = []
            for j in range(i+1, min(i+10, len(lines))):
                if not lines[j].strip():
                    break
                chunk.append(lines[j])
            if chunk:
                bonus_chunks.append(" ".join(chunk))
            i = i + 1
        i += 1

    bonus = " ".join(bonus_chunks).lower()
    bonus = re.sub(r"[•·\t]", " ", bonus)
    bonus = re.sub(r"[|/,:;()\[\]{}]", " ", bonus)
    bonus = re.sub(r"\s+", " ", bonus).strip()
    bonus_padded = " " + bonus + " " if bonus else ""

    # --- fetch aliases ---
    cur.execute("""
      SELECT sa.alias_text, sa.skill_id
      FROM skill_aliases sa
    """)
    rows = cur.fetchall()

    # Avoid super-short false positives unless "hard safe"
    hard_safe = {"r", "c", "c++", "c#", "go", "sql", "etl", "aws", "gcp", "bi"}

    found = set()
    for r in rows:
        alias = (r["alias_text"] or "").strip().lower()
        sid = r["skill_id"]
        if not alias or not sid:
            continue

        a = re.sub(r"\s+", " ", alias)

        # skip risky tiny aliases unless hard-safe or multiword
        if len(a) <= 2 and a not in hard_safe:
            continue

        # require token boundary by padded substring
        token = " " + a + " "
        if token in padded or (bonus_padded and token in bonus_padded):
            found.add(sid)

    return sorted(found)
'''.lstrip("\n")

# Need imports: re is used in new body. Ensure `import re` exists.
if re.search(r"(?m)^\s*import re\s*$", s) is None:
    # add after other imports near top
    imp_anchor = re.search(r"(?m)^(import .*|from .* import .*)\n", s)
    if not imp_anchor:
        raise SystemExit("❌ Could not find import section to add import re.")
    # insert after first import line
    insert_at = imp_anchor.end()
    s = s[:insert_at] + "import re\n" + s[insert_at:]

s = s[:start] + new_block + s[end:]
p.write_text(s, encoding="utf-8")
print(f"✅ Patched {fn_name}(): higher recall skills extraction.")
PY

python -m py_compile python/score_resume.py
echo "✅ Compile OK"
