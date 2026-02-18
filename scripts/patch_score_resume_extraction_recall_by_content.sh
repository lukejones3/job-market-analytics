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

# Ensure import re exists
if re.search(r"(?m)^\s*import re\s*$", s) is None:
    # Insert after the last import line in the first import block
    m = re.search(r"(?ms)^(?:from\s+\S+\s+import\s+.*\n|import\s+.*\n)+", s)
    if not m:
        raise SystemExit("❌ Could not find an import block to add `import re`.")
    s = s[:m.end()] + "import re\n" + s[m.end():]

# Find a function that queries skill_aliases (content-based)
# Grab a def-block that contains "FROM skill_aliases"
m = re.search(r"(?s)(^def\s+\w+\(.*?\):\n)(.*?)(?=^def\s|\Z)", s, flags=re.M)
hit_start = hit_end = None
fn_head = None

pos = 0
while True:
    m = re.search(r"(?s)(^def\s+\w+\(.*?\):\n)(.*?)(?=^def\s|\Z)", s[pos:], flags=re.M)
    if not m:
        break
    head = m.group(1)
    body = m.group(2)
    if "FROM skill_aliases" in body:
        hit_start = pos + m.start()
        hit_end = pos + m.end()
        fn_head = head
        fn_name = re.search(r"^def\s+(\w+)\(", head, flags=re.M).group(1)
        break
    pos += m.end()

if hit_start is None:
    raise SystemExit("❌ Could not find any function containing `FROM skill_aliases` to patch.")

fn_name = re.search(r"^def\s+(\w+)\(", fn_head, flags=re.M).group(1)

# New body: higher recall but still token-boundary safe
# Strategy:
# - lower + pad text with spaces, also add a "bonus" normalized punctuation->space version
# - allow short aliases only if in hard_safe set
# - use " token " containment (fast) to avoid regex overhead
new_body = r'''
    """
    Higher-recall skill extraction from text using skill_aliases.
    - Token-boundary safe via padded substring match (" alias " in " text ").
    - Allows short aliases only if hard-safe (sql, r, c++, etc).
    """
    if not text:
        return []

    t = text.lower()
    # basic padding for token-boundary substring matching
    padded = " " + re.sub(r"\s+", " ", t) + " "

    # bonus: normalize punctuation to spaces for cases like "PowerBI/SQL" or "Python,SQL"
    bonus = re.sub(r"[^a-z0-9\+#\.\s]", " ", t)
    bonus_padded = " " + re.sub(r"\s+", " ", bonus) + " "

    cur.execute("""
      SELECT sa.alias_text, sa.skill_id
      FROM skill_aliases sa
    """)
    rows = cur.fetchall()

    hard_safe = {
        "r", "c", "c++", "c#", "go",
        "sql", "etl", "aws", "gcp", "bi",
        "api", "ml", "ai"
    }

    found = set()
    for r in rows:
        alias = (r["alias_text"] or "").strip().lower()
        sid = r["skill_id"]
        if not alias or not sid:
            continue

        a = re.sub(r"\s+", " ", alias)

        # skip risky tiny aliases unless hard-safe
        if len(a) <= 2 and a not in hard_safe:
            continue

        token = f" {a} "
        if token in padded or token in bonus_padded:
            found.add(sid)

    return sorted(found)
'''.lstrip("\n")

# Replace only the function body (keep original def line)
old_block = s[hit_start:hit_end]
head_match = re.match(r"(?s)(^def\s+\w+\(.*?\):\n)", old_block)
if not head_match:
    raise SystemExit("❌ Internal: could not parse function header.")
new_block = head_match.group(1) + new_body + "\n"

s = s[:hit_start] + new_block + s[hit_end:]
p.write_text(s, encoding="utf-8")
print(f"✅ Patched extraction function: {fn_name}()")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
