#!/usr/bin/env bash
set -euo pipefail
FILE="python/ingest_job_dump.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.emptyhash_${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/ingest_job_dump.py")
s = p.read_text(encoding="utf-8")

# Find where desc_hash is computed (something like hashlib.md5(description_text.encode(...)).hexdigest())
m = re.search(r"(?m)^\s*desc_hash\s*=\s*.+hashlib\.md5\(", s)
if not m:
    raise SystemExit("❌ Could not find desc_hash assignment using hashlib.md5(...)")

# Insert guard a few lines BEFORE that desc_hash assignment (safe: right after description_text is set)
# We’ll locate nearest prior assignment to description_text.
pre = s.rfind("\n", 0, m.start())
# Search backwards for 'description_text' assignment within 4000 chars
window_start = max(0, m.start() - 4000)
win = s[window_start:m.start()]
m_desc = list(re.finditer(r"(?m)^\s*description_text\s*=\s*.+$", win))
if not m_desc:
    # fallback: if you use "desc =" or similar, we just inject immediately before desc_hash assignment
    insert_at = m.start()
else:
    last = m_desc[-1]
    insert_at = window_start + last.end()

guard = r"""
        # ---- Empty description guard: never hash empty text (md5('') = d41d8...) ----
        if description_text is not None:
            description_text = description_text.strip()
        if not description_text:
            desc_hash = None
        else:
"""
# We need to indent whatever the desc_hash assignment uses.
# We'll indent the existing desc_hash assignment by 8 spaces more only if we wrap it.
# Easiest: replace the desc_hash assignment line with an indented version,
# and inject the guard right before it.

# Grab that single line
line_end = s.find("\n", m.start())
line = s[m.start():line_end]

# If already guarded, bail
if "md5('') = d41d8" in s:
    print("ℹ️ Already patched.")
    raise SystemExit(0)

# Replace the desc_hash line with an indented version
indented_line = "        " + line.lstrip()

s = s[:m.start()] + indented_line + s[line_end:]

# Now inject guard at insert_at (right after description_text assignment if found)
s = s[:insert_at] + guard + s[insert_at:]

p.write_text(s, encoding="utf-8")
print("✅ Patched: desc_hash NULL when description_text empty; otherwise compute hash.")
PY

python -m py_compile "$FILE"
echo "✅ Done. Backup: ${FILE}.bak.emptyhash_${ts}"
