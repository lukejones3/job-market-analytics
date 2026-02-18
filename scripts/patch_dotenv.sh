#!/usr/bin/env bash
set -Eeuo pipefail

ts="$(date +%Y%m%d_%H%M%S)"
for f in python/enrich_job_postings.py python/run_enrich_with_logging.py; do
  cp "$f" "$f.bak.$ts"
done

python - <<'PY'
from pathlib import Path
import re

FILES = [
    Path("python/enrich_job_postings.py"),
    Path("python/run_enrich_with_logging.py"),
]

def ensure_dotenv_block(text: str) -> str:
    # If we already have explicit dotenv_path, do nothing
    if "load_dotenv(dotenv_path=" in text:
        return text

    # Replace any occurrence of:
    #   from dotenv import load_dotenv
    #   load_dotenv()
    # ...with explicit path loading.
    # Handles whitespace + optional blank lines.
    pat = re.compile(
        r"from dotenv import load_dotenv\s*\n\s*load_dotenv\(\)\s*\n",
        flags=re.M
    )

    replacement = (
        "from pathlib import Path\n"
        "from dotenv import load_dotenv\n\n"
        "# Always load .env from repo root (safe in heredocs / python -c)\n"
        "load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / \".env\")\n"
    )

    if pat.search(text):
        return pat.sub(replacement, text, count=1)

    # If file imports load_dotenv but doesn’t call it (or vice versa), handle both
    if "from dotenv import load_dotenv" in text and "load_dotenv(" not in text:
        # insert call after import
        text = text.replace(
            "from dotenv import load_dotenv\n",
            replacement + "\n"
        )
        return text

    if "load_dotenv()" in text:
        text = text.replace("load_dotenv()", "load_dotenv(dotenv_path=Path(__file__).resolve().parents[1] / \".env\")")
        if "from pathlib import Path" not in text:
            text = "from pathlib import Path\n" + text
        return text

    return text

for p in FILES:
    s = p.read_text(encoding="utf-8")
    s2 = ensure_dotenv_block(s)
    p.write_text(s2, encoding="utf-8")

# Runner: make count_proc use sys.executable too
runner = Path("python/run_enrich_with_logging.py")
s = runner.read_text(encoding="utf-8")

# ensure sys import exists
if not re.search(r"(?m)^import\s+sys\s*$", s):
    s = re.sub(r"(?m)^((?:from|import)\s+[^\n]+)\n", r"\1\nimport sys\n", s, count=1)

s = s.replace('["python3", ENRICH_SCRIPT, "--count-only"]', '[sys.executable, ENRICH_SCRIPT, "--count-only"]')
s = s.replace('["python3", ENRICH_SCRIPT, "--count-only"]', '[sys.executable, ENRICH_SCRIPT, "--count-only"]')

runner.write_text(s, encoding="utf-8")

print("✅ Patched dotenv loading (explicit .env path) + runner count_proc uses venv python.")
PY

python -m py_compile python/enrich_job_postings.py
python -m py_compile python/run_enrich_with_logging.py

echo "✅ Compiled OK. Backups created with .$ts"
