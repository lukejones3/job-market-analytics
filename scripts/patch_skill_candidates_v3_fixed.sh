#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  echo "❌ Not in venv. Run: source .venv/bin/activate"
  exit 1
fi

PYBIN="$VIRTUAL_ENV/bin/python"
FILE="python/enrich_job_postings.py"
ts="$(date +%Y%m%d_%H%M%S)"
cp "$FILE" "$FILE.bak.cand_v3fix_$ts"

"$PYBIN" - <<'PY'
from pathlib import Path
import re

p = Path("python/enrich_job_postings.py")
s = p.read_text(encoding="utf-8")

# --- A) Add global hard reject regex if missing ---
if "_CANDIDATE_HARD_REJECT_RE" not in s:
    hard_reject = r'''
# Hard rejects: EEO/protected-class boilerplate + common geo fragments + generic junk
_CANDIDATE_HARD_REJECT_RE = re.compile(
    r"\b("
    r"equal employment|eeo|affirmative action|protected class|"
    r"race|color|religion|sex|gender|pregnan|national origin|disabilit|veteran|genetic|"
    r"age over 40|sexual orientation|marital status|"
    r")\b",
    flags=re.IGNORECASE
)
'''.lstrip("\n")

    # Insert near the other candidate regex blocks if we can, else near the top after imports.
    anchor = "_CANDIDATE_STRICT_REJECT_RE"
    i = s.find(anchor)
    if i != -1:
        s = s[:i] + hard_reject + "\n" + s[i:]
    else:
        # after imports: stick it after the first big regex section marker if present
        m = re.search(r"(?m)^\s*#\s*-{5,}\s*$", s)
        if m:
            s = s[:m.end()] + "\n\n" + hard_reject + s[m.end():]
        else:
            s = hard_reject + "\n" + s

# --- B) Patch _looks_like_tool_or_hard_skill() robustly ---
m = re.search(
    r"(?ms)^def _looks_like_tool_or_hard_skill\(raw: str, norm: str\) -> bool:\n(.*?)(?=^\S|\Z)",
    s
)
if not m:
    raise SystemExit("❌ Could not find def _looks_like_tool_or_hard_skill(raw: str, norm: str) -> bool:")

func_block = m.group(0)

# If we've already inserted these rules, skip
needles = [
    "_CANDIDATE_HARD_REJECT_RE.search(norm)",
    "re.fullmatch(r\"[a-z]{2}\", norm)",
    "re.fullmatch(r\"(usa|us|u\\.s\\.|china|japan|india|canada|uk)\"",
    "norm.endswith(\" skills\")",
]
if all(n in func_block for n in needles):
    # already patched
    pass
else:
    inject_lines = [
        "    # v3: hard rejects (EEO/protected-class boilerplate)",
        "    if _CANDIDATE_HARD_REJECT_RE.search(norm):",
        "        return False",
        "",
        "    # v3: reject 2-letter tokens (IL/CA/etc)",
        "    if re.fullmatch(r\"[a-z]{2}\", norm):",
        "        return False",
        "",
        "    # v3: reject common country tokens",
        "    if re.fullmatch(r\"(usa|us|u\\.s\\.|china|japan|india|canada|uk)\", norm):",
        "        return False",
        "",
        "    # v3: reject 'X skills' artifact",
        "    if norm.endswith(\" skills\"):",
        "        return False",
        "",
    ]
    inject = "\n".join(inject_lines) + "\n"

    # Choose insertion point:
    # 1) after the first line that assigns/normalizes norm (contains 'norm =')
    # 2) else, after docstring end if docstring exists
    # 3) else, right after def line
    lines = func_block.splitlines(True)

    # find def line index (0)
    insert_at = 1

    # find first "norm =" line in function block
    for i, ln in enumerate(lines):
        if i == 0:
            continue
        if "norm" in ln and "=" in ln and "norm" in ln.split("=",1)[0]:
            insert_at = i + 1
            break

    # If docstring exists, ensure we inject after it (unless norm assignment is later)
    # crude docstring detection: first non-empty line after def starts with triple quotes
    for i in range(1, min(len(lines), 20)):
        if lines[i].strip() == "":
            continue
        if lines[i].lstrip().startswith('"""') or lines[i].lstrip().startswith("'''"):
            q = lines[i].lstrip()[:3]
            # find docstring end
            for j in range(i, len(lines)):
                if j == i:
                    # if triple quotes open+close on same line
                    if lines[j].count(q) >= 2:
                        ds_end = j
                        break
                if j > i and q in lines[j]:
                    ds_end = j
                    break
            else:
                ds_end = i
            insert_at = max(insert_at, ds_end + 1)
            break
        break

    # Build patched block
    lines.insert(insert_at, inject)
    new_block = "".join(lines)
    s = s.replace(func_block, new_block)

# --- C) Add extra ignore terms if ignore=set([...]) exists (optional cleanup) ---
if "ignore = set([" in s:
    extra = ["financial","management","integrity","hungry","connect","passion","doe",
             "strong analytical","powerpoint skills","attitude is everything"]
    for term in extra:
        if (f"'{term}'" not in s) and (f"\"{term}\"" not in s):
            s = s.replace("ignore = set([", "ignore = set([\n            " + repr(term) + ",", 1)

p.write_text(s, encoding="utf-8")
print("✅ Applied patch v3 (fixed): robust insert into _looks_like_tool_or_hard_skill.")
PY

"$PYBIN" -m py_compile "$FILE"
echo "✅ Compiles. Backup: $FILE.bak.cand_v3fix_$ts"
