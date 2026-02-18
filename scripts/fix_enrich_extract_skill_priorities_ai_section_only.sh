#!/usr/bin/env bash
set -euo pipefail

FILE="python/enrich_job_postings.py"
TS="$(date +%Y%m%d_%H%M%S)"
BAK="${FILE}.bak.fix_ai_section_${TS}"

cp "$FILE" "$BAK"
echo "🧾 Backup: $BAK"

python - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# Replace the entire extract_skill_priorities() definition block.
# We match from "def extract_skill_priorities(" through the first "return out" at base indentation.
pat = re.compile(
    r"(?s)\ndef\s+extract_skill_priorities\(\n.*?\n\s*return\s+out\s*\n"
)

m = pat.search(s)
if not m:
    raise SystemExit("❌ Could not find extract_skill_priorities() block to replace.")

replacement = r'''
def extract_skill_priorities(
    desc: str, compiled_patterns: List[Tuple[re.Pattern, str, str]]
) -> Dict[str, str]:
    """
    Returns: {skill_id: skill_priority}
    Default: required
    Strongest wins: required > preferred > nice-to-have

    ai_section_only:
      - the short alias "ai" is ONLY counted if it appears inside a skill/qual/req section block
        (prevents noise like "help of AI", "powered by AI", etc. in generic prose).
    """
    priority_rank = {"required": 3, "preferred": 2, "nice-to-have": 1}

    # full text lines (for normal skills)
    lines = [clean_text(x) for x in desc.splitlines()]
    lines = [x for x in lines if x]

    # skill-section-only lines (to prevent "help of AI" etc.)
    section_lines = extract_job_skill_section_lines(desc)
    section_norm = set(normalize_for_matching(x) for x in section_lines if x)

    current_section = None
    out: Dict[str, str] = {}

    for line in lines:
        new_section = infer_section_priority(line)
        if new_section:
            current_section = new_section

        line_priority = detect_priority_from_line(line)
        t = normalize_for_matching(line)

        for pat, skill_id, _alias in compiled_patterns:
            # Restrict ONLY the ambiguous short alias "ai" to skill sections
            if _alias == "ai":
                if t not in section_norm:
                    continue

            if pat.search(t):
                p = line_priority or current_section or "required"
                if skill_id not in out:
                    out[skill_id] = p
                else:
                    if priority_rank.get(p, 0) > priority_rank.get(out[skill_id], 0):
                        out[skill_id] = p

    return out
'''.lstrip("\n")

s2 = s[:m.start()] + "\n" + replacement + s[m.end():]
p.write_text(s2, encoding="utf-8")
print("✅ Replaced extract_skill_priorities() with clean AI-section-only logic.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"

echo ""
echo "Next step (recommended):"
echo "  python -u python/enrich_job_postings.py --rescan-skills"

