#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"

python - <<'PY'
from pathlib import Path
import re
from datetime import datetime

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# ----------------------------
# Backup
# ----------------------------
ts = datetime.now().strftime("%Y%m%d_%H%M%S")
bak = p.with_suffix(p.suffix + f".bak.strip_linkedin_{ts}")
bak.write_text(s, encoding="utf-8")
print(f"🧾 Backup: {bak}")

# ----------------------------
# 1) Insert helper (idempotent)
# Put it right after clean_text() definition block.
# ----------------------------
if "def strip_linkedin_chrome(" not in s:
    m = re.search(r"(?ms)^def\s+clean_text\s*\(.*?\)\s*:\s*\n(?P<body>.*?)(?=^\s*def\s+normalize_for_matching\s*\()", s)
    if not m:
        raise SystemExit("❌ Could not locate clean_text() block to anchor helper insertion.")

    helper = r'''
def strip_linkedin_chrome(text: str) -> str:
    """
    Remove common LinkedIn/Chrome copy UI chrome that pollutes skill extraction.
    Designed to be conservative (only removes well-known UI lines).
    """
    if not text:
        return text

    kill_exact = {
        "share",
        "show more options",
        "apply",
        "save",
        "show match details",
        "tailor my resume",
        "help me update my profile",
        "create cover letter",
        "is this information helpful?",
        "try premium for $0",
        "people you can reach out to",
        "show all",
        "beta",
    }

    out = []
    for raw in text.splitlines():
        ln = clean_text(raw)
        low = normalize_for_matching(ln)

        if not low:
            out.append(raw)
            continue

        # Exact UI chrome
        if low in kill_exact:
            continue

        # "Matches your job preferences..." etc.
        if low.startswith("matches your job preferences"):
            continue

        # "Responses managed off LinkedIn" / "Promoted by hirer"
        if "responses managed off linkedin" in low:
            continue
        if low.startswith("promoted by"):
            continue

        # "X people clicked apply"
        if re.search(r"\bpeople clicked apply\b", low):
            continue

        # "Save <title> at <company>"
        if low.startswith("save ") and " at " in low:
            continue

        # "Company · Location (Hybrid)" duplicates (we keep title elsewhere)
        # Leave it alone; too risky to drop.

        out.append(raw)

    return "\n".join(out)
'''.lstrip("\n")

    s = s[:m.end()] + "\n\n" + helper + "\n" + s[m.end():]
    print("✅ Inserted strip_linkedin_chrome() helper.")
else:
    print("ℹ️ strip_linkedin_chrome() helper already present; leaving it unchanged.")

# ----------------------------
# 2) Insert call in main loop (flexible anchor)
# Find: for job in jobs: ... and then the first assignment that references description_text.
# Then inject: desc = strip_linkedin_chrome(desc)
# ----------------------------
loop = re.search(
    r"(?ms)^\s*for\s+job\s+in\s+jobs\s*:\s*\n(?P<body>.*?)(?=^\s*conn\.commit\(\)|^\s*except\s+Exception:|^\s*finally:)",
    s
)
if not loop:
    raise SystemExit("❌ Could not locate main loop: `for job in jobs:`")

body = loop.group("body")

desc_line = re.search(r"(?m)^(?P<ind>\s*)(?P<var>[a-zA-Z_][a-zA-Z0-9_]*)\s*=\s*.*description_text.*$", body)
if not desc_line:
    raise SystemExit("❌ Could not find any assignment line containing `description_text` inside the jobs loop.")

ind = desc_line.group("ind")
var = desc_line.group("var")

# Prevent double insert
after_window = body[desc_line.end(): desc_line.end()+400]
if "strip_linkedin_chrome(" in after_window:
    print("ℹ️ strip_linkedin_chrome() call already present after description_text assignment; skipping insertion.")
else:
    inject = f"{ind}{var} = strip_linkedin_chrome({var})\n"
    body2 = body[:desc_line.end()] + "\n" + inject + body[desc_line.end():]
    s = s[:loop.start("body")] + body2 + s[loop.end("body"):]
    print(f"✅ Inserted `{var} = strip_linkedin_chrome({var})` in main jobs loop.")

p.write_text(s, encoding="utf-8")
print("✅ Wrote patched file.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"

echo ""
echo "Next step:"
echo "  python -u python/enrich_job_postings.py --rescan-skills"
