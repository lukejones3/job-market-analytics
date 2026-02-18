#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
PY="${REPO}/.venv/bin/python"
ts="$(date +%Y%m%d_%H%M%S)"

cp "$FILE" "${FILE}.bak.callsite_${ts}"
echo "🧾 Backup: ${FILE}.bak.callsite_${ts}"

"$PY" - <<'PY'
from pathlib import Path

p = Path.home() / "github" / "job-market-analytics" / "python" / "score_resume.py"
s = p.read_text(encoding="utf-8")

# 1) Find the CALL SITE (not the def)
# We require a newline + some indentation + write_scores(
call_token = "\n                write_scores("  # 16 spaces; matches your score_resume() block style
call_pos = s.find(call_token)
if call_pos == -1:
    # fallback: any indented call that is NOT preceded by "def "
    import re
    m = re.search(r"\n( +)write_scores\(", s)
    if not m:
        raise SystemExit("❌ Could not find an indented write_scores( call site.")
    # ensure it's not "def write_scores("
    if s[max(0, m.start()-10):m.start()].strip().startswith("def"):
        # search for next occurrence
        m2 = None
        for m_iter in re.finditer(r"\n( +)write_scores\(", s):
            if not s[max(0, m_iter.start()-10):m_iter.start()].strip().startswith("def"):
                m2 = m_iter
                break
        if not m2:
            raise SystemExit("❌ Found only def write_scores(, no call site.")
        call_pos = m2.start()
    else:
        call_pos = m.start()

# 2) Inject block right BEFORE that call, only if not already present nearby
window = s[max(0, call_pos-2000):call_pos]
if "compute_plausibility_penalty(" not in window:
    block = """
                # Fetch resume skill names (for plausibility heuristics)
                cur.execute(\"\"\"
                  SELECT s.skill_name
                  FROM resume_skills rs
                  JOIN skills s ON s.skill_id = rs.skill_id
                  WHERE rs.resume_id=%s
                \"\"\", (resume_id,))
                resume_skill_names = [r["skill_name"] for r in cur.fetchall()]

                # Plausibility + flags
                pl_pen, pl_flags = compute_plausibility_penalty(resume_text, resume_skill_names, experience_level)
                for f in pl_flags:
                    write_run_flag(cur, resume_id, run_id, "plausibility", f, None)

                # Confidence + flags
                conf_score, conf_flags = compute_confidence_score(
                    resume_text=resume_text,
                    skills_found=len(resume_skill_ids),
                    market_jobs=len(market_job_ids),
                    matched_jobs=len(matches),
                    sal_min=smin, sal_max=smax
                )
                for f in conf_flags:
                    write_run_flag(cur, resume_id, run_id, "confidence", f, None)

""".lstrip("\n")
    s = s[:call_pos+1] + block + s[call_pos+1:]

# 3) Patch the call arguments to include pl_pen, conf_score, conf_flags
# We will rewrite only the first call expression lines until the closing paren.
call_pos = s.find(call_token)
if call_pos == -1:
    # re-find via regex as above
    import re
    m = re.search(r"\n( +)write_scores\(", s)
    if not m:
        raise SystemExit("❌ After injection, could not find write_scores( call site.")
    call_pos = m.start()

# Find the call block end: the first line that starts with the same indent and a closing ")"
# We'll just scan forward to the next ")\n" at same indentation depth.
indent = "                "  # 16 spaces (matches token)
start = call_pos + 1
end = s.find("\n" + indent + ")\n", start)
if end == -1:
    # fallback: first occurrence of "\n                )" (without trailing newline)
    end = s.find("\n" + indent + ")", start)
    if end == -1:
        raise SystemExit("❌ Could not find end of write_scores(...) call block.")

call_block = s[start:end+len("\n"+indent+")")]

# If already has pl_pen/conf_score/conf_flags, do nothing
if "pl_pen" not in call_block or "conf_score" not in call_block or "conf_flags" not in call_block:
    # naive but safe: add the 3 args right before the closing line that contains just ")"
    lines = call_block.splitlines(True)
    # find the line that is exactly indent + ")"
    close_i = None
    for i, line in enumerate(lines):
        if line.strip() == ")" and line.startswith(indent):
            close_i = i
            break
    if close_i is None:
        raise SystemExit("❌ Could not locate closing ')' line in write_scores call.")

    # Ensure we don't duplicate if partially present
    insert_args = []
    if "pl_pen" not in call_block:
        insert_args.append(indent + "    pl_pen,\n")
    if "conf_score" not in call_block:
        insert_args.append(indent + "    conf_score,\n")
    if "conf_flags" not in call_block:
        insert_args.append(indent + "    conf_flags,\n")

    lines[close_i:close_i] = insert_args
    new_call_block = "".join(lines)

    s = s[:start] + new_call_block + s[end+len("\n"+indent+")"):]

p.write_text(s, encoding="utf-8")
print("✅ Patched call site: injected plausibility/confidence + updated write_scores(...) args")
PY

"$PY" -m py_compile "$FILE"
echo "✅ Compile OK"
