#!/usr/bin/env bash
set -euo pipefail
FILE="python/ingest_job_dump.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "${FILE}.bak.companynoise_${ts}"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/ingest_job_dump.py")
s = p.read_text(encoding="utf-8")

# We’ll inject a small helper + stopwords list if not present
if "COMPANY_NOISE_TOKENS" not in s:
    inject = r"""
# ---- Company noise tokens (LinkedIn / dump delimiters that should never become company names) ----
COMPANY_NOISE_TOKENS = {
    "===job start===",
    "===job end===",
    "responses managed off linkedin",
    "responses managed off linkedin.",
    "responses handled off linkedin",
    "responses handled off linkedin.",
    "responses managed off linkedin\n",
    "responses managed off linkedin\r",
}
def _is_company_noise(x: str) -> bool:
    if not x:
        return True
    t = " ".join(x.strip().lower().split())
    return t in COMPANY_NOISE_TOKENS
"""
    # Put near top after imports (safe anchor: first occurrence of "DUMP_PATH" or after imports)
    anchor = re.search(r"(?m)^DUMP_PATH\s*=", s)
    if anchor:
        s = s[:anchor.start()] + inject + "\n" + s[anchor.start():]
    else:
        s = inject + "\n" + s

# Now ensure company extraction step skips noise.
# Common pattern: you have some "company_line" or company candidate variable before insert/upsert.
# We'll add a defensive check right before company upsert if we can find "company_name" assignment.
# Try to locate a line like: company_name = ...
m = re.search(r"(?m)^\s*company_name\s*=\s*(.+)\s*$", s)
if m and "_is_company_noise" not in s[m.start()-300:m.start()+300]:
    # Insert after company_name assignment
    insert_after = m.end()
    guard = "\n    if _is_company_noise(company_name):\n        company_name = None\n"
    s = s[:insert_after] + guard + s[insert_after:]
else:
    # Fallback: if we can't find company_name assignment, patch near company upsert call
    up = re.search(r"(?m)^\s*company_id\s*=\s*upsert_company\(", s)
    if up:
        s = s[:up.start()] + "    if _is_company_noise(company_name):\n        company_name = None\n\n" + s[up.start():]

p.write_text(s, encoding="utf-8")
print("✅ Patched ingest to block delimiter/LinkedIn noise from becoming company_name.")
PY

python -m py_compile "$FILE"
echo "✅ Done. Backup: ${FILE}.bak.companynoise_${ts}"
