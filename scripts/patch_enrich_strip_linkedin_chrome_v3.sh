#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# ----------------------------
# Helper already added in v2 run; keep idempotent anyway
# ----------------------------
if "def strip_linkedin_chrome(" not in s:
    raise SystemExit("❌ strip_linkedin_chrome() helper not found. Re-run your v2 helper insert first.")

# ----------------------------
# Find the main processing loop and a desc assignment that references description_text
# ----------------------------
loop = re.search(r"(?ms)^\s*for\s+job\s+in\s+jobs\s*:\s*\n(?P<body>.*?)(?=^\s*conn\.commit\(\)|^\s*except\s+Exception:)", s)
if not loop:
    raise SystemExit("❌ Could not locate main loop: `for job in jobs:`")

body = loop.group("body")

# Find a line like:
#   desc = job["description_text"] ...
#   desc = job.get("description_text") ...
#   desc = (job["description_text"] if ... else ...)
desc_line = re.search(r"(?m)^(?P<ind>\s*)desc\s*=\s*.*description_text.*$", body)
if not desc_line:
    # fallback: maybe they don't use 'desc' variable name; try 'description' variable
    alt = re.search(r"(?m)^(?P<ind>\s*)(desc|description)\s*=\s*.*description_text.*$", body)
    if not alt:
        raise SystemExit("❌ Could not find any assignment line containing `description_text` inside the loop.")
    desc_line = alt

ind = desc_line.group("ind")
varname = "desc"
m_var = re.match(rf"^{re.escape(ind)}(?P<v>[a-zA-Z_][a-zA-Z0-9_]*)\s*=", desc_line.group(0))
if m_var:
    varname = m_var.group("v")

# Don’t double-insert
insertion = f"{ind}{varname} = strip_linkedin_chrome({varname})\n"
post = body[desc_line.end(): desc_line.end()+250]
if "strip_linkedin_chrome(" in post:
    print("ℹ️ strip_linkedin_chrome() call already present after description_text assignment; no change.")
    raise SystemExit(0)

body2 = body[:desc_line.end()] + "\n" + insertion + body[desc_line.end():]

s2 = s[:loop.start("body")] + body2 + s[loop.end("body"):]
p.write_text(s2, encoding="utf-8")
print(f"✅ Inserted `{varname} = strip_linkedin_chrome({varname})` after the description_text assignment in the jobs loop.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
