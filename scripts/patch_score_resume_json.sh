#!/usr/bin/env bash
set -euo pipefail
FILE="python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.jsonfix_$ts"

python - <<'PY'
import re
from pathlib import Path

path = Path("python/score_resume.py")
s = path.read_text(encoding="utf-8")

# Ensure imports
if "import json" not in s:
    s = s.replace("import argparse", "import argparse\nimport json", 1)

# Ensure Json import
if "from psycopg2.extras import" in s and "Json" not in s:
    s = re.sub(r"from psycopg2\.extras import ([^\n]+)",
               lambda m: f"from psycopg2.extras import {m.group(1).strip()}, Json",
               s, count=1)
elif "from psycopg2.extras import Json" not in s:
    # fallback: add it near psycopg2 imports
    s = s.replace("from psycopg2.extras import DictCursor",
                  "from psycopg2.extras import DictCursor, Json", 1)

# Replace create_run() with a safe version
pat = re.compile(r"(?ms)^def\s+create_run\s*\(.*?\)\s*:\s*\n.*?(?=^\S|\Z)")
m = pat.search(s)
if not m:
    raise SystemExit("❌ Could not find create_run() to patch.")

new_fn = """def create_run(cur, run_id: str, resume_id: str, params: dict):
    # params MUST be valid JSON for jsonb column (None -> null handled by Json())
    cur.execute(
        \"""
        INSERT INTO resume_runs (run_id, resume_id, status, params, started_at)
        VALUES (%s, %s, 'running', %s, now())
        ON CONFLICT (run_id) DO UPDATE SET
            resume_id = EXCLUDED.resume_id,
            status    = EXCLUDED.status,
            params    = EXCLUDED.params,
            started_at = now()
        \""",
        (run_id, resume_id, Json(params or {})),
    )

"""

s = s[:m.start()] + new_fn + s[m.end():]
path.write_text(s, encoding="utf-8")
print("✅ Patched create_run() to use psycopg2 Json(params).")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK. Backup: ${FILE}.bak.jsonfix_${ts}"
