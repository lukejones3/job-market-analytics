#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"
bak="${FILE}.bak.plausconf_${ts}"
cp "$FILE" "$bak"
echo "🧾 Backup: $bak"

python3 - <<'PY'
from pathlib import Path

p = Path.home() / "github/job-market-analytics/python/score_resume.py"
s = p.read_text(encoding="utf-8")

# ------------------------------------------------------------
# 1) Inject helper functions once (after write_job_matches() or before score_resume())
# ------------------------------------------------------------
marker = "\n\ndef score_resume("
if "def compute_plausibility_penalty(" not in s:
    inject = r'''

def compute_plausibility_penalty(resume_text: str, resume_skill_names: list, target_level: str):
    """
    Plausibility penalty: how 'off' is the target level vs what the resume signals.
    This is intentionally heuristic (fast + deterministic).
    Returns: (penalty_int, flags_list)
    """
    txt = (resume_text or "").lower()
    skills = [(" ".join(x.lower().split())) for x in (resume_skill_names or [])]

    flags = []
    pen = 0

    # Quick signals from text
    has_intern = any(k in txt for k in ["intern", "internship", "student"])
    has_senior_title = any(k in txt for k in [" senior ", " sr ", " lead ", " principal ", " staff "])

    # Senior-stack-ish skills (degenerate list, tweak freely)
    senior_stack = {
        "kubernetes","k8s","terraform","aws","gcp","azure",
        "kafka","spark","databricks","snowflake",
        "microservices","distributed systems","airflow","dbt",
        "mlops","docker","helm","ci/cd","kinesis","redshift"
    }
    senior_hits = 0
    for sk in skills:
        for token in senior_stack:
            if token in sk:
                senior_hits += 1
                break

    # Target-level rules
    lvl = (target_level or "any").lower().strip()

    # If user targets entry but resume screams senior
    if lvl == "entry":
        if has_senior_title:
            pen += 8
            flags.append("resume_senior_title_but_target_entry")
        if senior_hits >= 3:
            pen += 8
            flags.append("senior_stack_signal_for_entry_target")
        if not has_intern and "years" in txt and any(x in txt for x in ["10+","8+","7+","6+","5+"]):
            pen += 6
            flags.append("high_years_signal_for_entry_target")

    # If user targets senior but resume has strong entry signals
    if lvl == "senior":
        if has_intern:
            pen += 10
            flags.append("intern_signal_but_target_senior")
        # Lack of any senior words could be weak-signal; small nudge
        if not has_senior_title and senior_hits == 0:
            pen += 6
            flags.append("low_senior_signal_for_senior_target")

    # Any-level: no penalty
    pen = max(0, min(int(pen), 25))
    return pen, flags


def compute_confidence_score(resume_text: str, skills_found: int, market_jobs: int, matched_jobs: int,
                             sal_min, sal_max):
    """
    Confidence = how trustworthy the output is (data sufficiency + extraction quality proxy).
    Returns: (score_int 0-100, flags_list)
    """
    flags = []
    score = 100

    # Skills extraction weakness
    if skills_found <= 5:
        score -= 25
        flags.append("low_skills_extracted")
    elif skills_found <= 8:
        score -= 12
        flags.append("modest_skills_extracted")

    # Market size weakness
    if market_jobs < 50:
        score -= 25
        flags.append("small_market_sample")
    elif market_jobs < 150:
        score -= 10
        flags.append("medium_market_sample")

    # Match coverage weakness
    if market_jobs > 0:
        coverage = matched_jobs / float(market_jobs)
        if coverage < 0.20:
            score -= 20
            flags.append("low_market_match_coverage")
        elif coverage < 0.50:
            score -= 8
            flags.append("medium_market_match_coverage")

    # Salary estimate availability
    if sal_min is None and sal_max is None:
        score -= 10
        flags.append("salary_estimate_missing")

    score = max(0, min(int(round(score)), 100))
    return score, flags


def write_run_flag(cur, resume_id: str, run_id: str, flag_type: str, flag: str, value=None):
    cur.execute("""
      INSERT INTO resume_run_flags (resume_id, run_id, flag_type, flag, value)
      VALUES (%s,%s,%s,%s,%s)
      ON CONFLICT (resume_id, run_id, flag_type, flag) DO UPDATE SET
        value = EXCLUDED.value
    """, (resume_id, run_id, flag_type, flag, value))
'''.strip("\n") + "\n\n"

    i = s.find(marker)
    if i == -1:
        raise SystemExit("❌ Could not find score_resume() to anchor injection.")
    s = s[:i] + "\n\n" + inject + s[i:]

# ------------------------------------------------------------
# 2) Inject calculation block right before write_scores(...)
#    Anchor: the comment '# write outputs'
# ------------------------------------------------------------
anchor = "                # write outputs"
pos = s.find(anchor)
if pos == -1:
    raise SystemExit("❌ Could not find '# write outputs' anchor in score_resume().")

# Only inject once
if "compute_plausibility_penalty(" not in s[pos:pos+1500]:
    block = r'''
                # ----------------------------
                # Plausibility + Confidence
                # ----------------------------
                cur.execute("""
                  SELECT s.skill_name
                  FROM resume_skills rs
                  JOIN skills s ON s.skill_id = rs.skill_id
                  WHERE rs.resume_id=%s
                """, (resume_id,))
                resume_skill_names = [r["skill_name"] for r in cur.fetchall()]

                pl_pen, pl_flags = compute_plausibility_penalty(resume_text, resume_skill_names, experience_level)
                for f in pl_flags:
                    write_run_flag(cur, resume_id, run_id, "plausibility", f, None)

                conf_score, conf_flags = compute_confidence_score(
                    resume_text=resume_text,
                    skills_found=len(resume_skill_ids),
                    market_jobs=len(market_job_ids),
                    matched_jobs=len(matches),
                    sal_min=sal_min, sal_max=sal_max
                )
                for f in conf_flags:
                    write_run_flag(cur, resume_id, run_id, "confidence", f, None)
'''.strip("\n") + "\n\n"
    s = s[:pos] + block + s[pos:]

# ------------------------------------------------------------
# 3) Patch the write_scores(...) call to pass new args (keyword-safe)
# ------------------------------------------------------------
call_snip = "                write_scores(cur, resume_id, run_id, market_fit, pct, sal_min, sal_max, honesty_match,"
call_pos = s.find(call_snip)
if call_pos == -1:
    raise SystemExit("❌ Could not find write_scores(...) call site.")

# Replace the 2-line call (your current one) with a version that passes new args.
old_call = """                write_scores(cur, resume_id, run_id, market_fit, pct, sal_min, sal_max, honesty_match,
                             matched_jobs_count=len(matches), top_jobs_considered=len(market_job_ids))"""
new_call = """                write_scores(
                    cur, resume_id, run_id,
                    market_fit, pct, sal_min, sal_max, honesty_match,
                    matched_jobs_count=len(matches),
                    top_jobs_considered=len(market_job_ids),
                    plausibility_penalty=pl_pen,
                    confidence_score=conf_score,
                    confidence_flags=conf_flags
                )"""

if old_call in s and "plausibility_penalty" not in s[s.find("write_scores("):s.find("write_scores(")+400]:
    s = s.replace(old_call, new_call)

p.write_text(s, encoding="utf-8")
print("✅ Patched: plausibility + confidence + run flags + write_scores args.")
PY

python3 -m py_compile "$FILE"
echo "✅ Compile OK"
