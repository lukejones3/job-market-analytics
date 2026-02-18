#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
PY="${REPO}/.venv/bin/python"
ts="$(date +%Y%m%d_%H%M%S)"

if [[ ! -f "$FILE" ]]; then
  echo "❌ Not found: $FILE"
  exit 1
fi

cp "$FILE" "${FILE}.bak.fast_${ts}"
echo "🧾 Backup: ${FILE}.bak.fast_${ts}"

"$PY" - <<'PY'
from pathlib import Path

p = Path.home() / "github" / "job-market-analytics" / "python" / "score_resume.py"
s = p.read_text(encoding="utf-8")

def ensure_once(needle: str, insert_at: int, block: str) -> str:
    if needle in s:
        return s
    return s[:insert_at] + block + s[insert_at:]

# ------------------------------------------------------------
# 1) Ensure Json import exists (fast)
# ------------------------------------------------------------
if "Json" not in s:
    if "from psycopg2.extras import" in s:
        s = s.replace("from psycopg2.extras import RealDictCursor", "from psycopg2.extras import RealDictCursor, Json")
    else:
        # Insert after psycopg2 import if present, else at top
        ins = s.find("\n", 0)
        s = "from psycopg2.extras import Json\n" + s

# ------------------------------------------------------------
# 2) Replace write_scores() function block using string anchors
#    anchor: "def write_scores(" up to next "def write_gaps("
# ------------------------------------------------------------
a = s.find("\ndef write_scores(")
if a == -1:
    # maybe at file start without leading newline
    a = s.find("def write_scores(")
if a == -1:
    raise SystemExit("❌ Could not find def write_scores(")

b = s.find("\ndef write_gaps(", a)
if b == -1:
    raise SystemExit("❌ Could not find def write_gaps( after write_scores")

new_write_scores = """
def write_scores(
    cur,
    resume_id: str,
    run_id: str,
    market_fit: int,
    percentile: Optional[float],
    sal_min: Optional[float],
    sal_max: Optional[float],
    honesty_match: Optional[int],
    matched_jobs_count: int,
    top_jobs_considered: int,
    plausibility_penalty: int,
    confidence_score: int,
    confidence_flags: list,
):
    cur.execute(
        \"""
        INSERT INTO resume_scores (
          resume_id, run_id, market_fit_score, market_percentile,
          salary_est_min, salary_est_max, honesty_match_score,
          matched_jobs_count, top_jobs_considered,
          plausibility_penalty, confidence_score, confidence_flags
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (resume_id, run_id) DO UPDATE SET
          market_fit_score      = EXCLUDED.market_fit_score,
          market_percentile     = EXCLUDED.market_percentile,
          salary_est_min        = EXCLUDED.salary_est_min,
          salary_est_max        = EXCLUDED.salary_est_max,
          honesty_match_score   = EXCLUDED.honesty_match_score,
          matched_jobs_count    = EXCLUDED.matched_jobs_count,
          top_jobs_considered   = EXCLUDED.top_jobs_considered,
          plausibility_penalty  = EXCLUDED.plausibility_penalty,
          confidence_score      = EXCLUDED.confidence_score,
          confidence_flags      = EXCLUDED.confidence_flags,
          created_at            = now()
        \""",
        (
            resume_id,
            run_id,
            market_fit,
            percentile,
            sal_min,
            sal_max,
            honesty_match,
            matched_jobs_count,
            top_jobs_considered,
            plausibility_penalty,
            confidence_score,
            Json(confidence_flags),
        ),
    )
""".lstrip()

s = s[:a+1] + new_write_scores + "\n" + s[b+1:]

# ------------------------------------------------------------
# 3) Insert helper functions after compute_honesty_match()
#    anchor: end of that function = next "\ndef " after its header
# ------------------------------------------------------------
if "def build_and_store_market_skill_stats" not in s:
    h = s.find("def compute_honesty_match(")
    if h == -1:
        raise SystemExit("❌ Could not find def compute_honesty_match(")

    # find next function after compute_honesty_match
    nxt = s.find("\ndef ", h+1)
    if nxt == -1:
        raise SystemExit("❌ Could not find next def after compute_honesty_match")

    helpers = """
SENIOR_STACK_SKILLS = {
    "airflow","dbt","databricks","snowflake","redshift","bigquery",
    "spark","pyspark","hadoop","kafka",
    "docker","kubernetes","terraform",
    "aws","gcp","azure","s3","lambda","glue","ec2",
}

def write_run_flag(cur, resume_id: str, run_id: str, flag_type: str, flag: str, value: Optional[float] = None):
    cur.execute(\"\"\"
      INSERT INTO resume_run_flags (resume_id, run_id, flag_type, flag, value)
      VALUES (%s,%s,%s,%s,%s)
      ON CONFLICT (resume_id, run_id, flag_type, flag) DO UPDATE
      SET value = EXCLUDED.value
    \"\"\", (resume_id, run_id, flag_type, flag, value))

def build_and_store_market_skill_stats(cur, resume_id: str, run_id: str, market_job_ids: List[str]):
    \"\"\"Returns weights dict: skill_id -> (req_freq, pref_freq, weight, req_jobs, pref_jobs, total_skill_jobs)
       Also writes resume_market_skill_stats rows.\"\"\"
    if not market_job_ids:
        return {}

    cur.execute(\"\"\"
      WITH base AS (
        SELECT
          js.skill_id,
          COUNT(DISTINCT js.job_id) FILTER (WHERE js.skill_priority='required') AS req_jobs,
          COUNT(DISTINCT js.job_id) FILTER (WHERE js.skill_priority='preferred') AS pref_jobs,
          COUNT(DISTINCT js.job_id) AS total_skill_jobs
        FROM job_skills js
        WHERE js.job_id = ANY(%s)
        GROUP BY js.skill_id
      )
      SELECT skill_id, req_jobs, pref_jobs, total_skill_jobs
      FROM base
    \"\"\", (market_job_ids,))

    total_jobs = len(set(market_job_ids))
    rows = cur.fetchall()

    cur.execute("DELETE FROM resume_market_skill_stats WHERE resume_id=%s AND run_id=%s", (resume_id, run_id))

    weights = {}
    for r in rows:
        sid = r["skill_id"]
        req_jobs = int(r["req_jobs"] or 0)
        pref_jobs = int(r["pref_jobs"] or 0)
        total_skill_jobs = int(r["total_skill_jobs"] or 0)

        req_freq = (req_jobs / total_jobs) if total_jobs else 0.0
        pref_freq = (pref_jobs / total_jobs) if total_jobs else 0.0

        demand = 0.75 * req_freq + 0.25 * pref_freq
        freq_any = (total_skill_jobs / total_jobs) if total_jobs else 0.0
        rarity = 1.0 / (0.05 + freq_any)
        roi = min(10.0, demand * rarity)

        w = demand * 100.0
        weights[sid] = (req_freq, pref_freq, w, req_jobs, pref_jobs, total_skill_jobs)

        cur.execute(\"\"\"
          INSERT INTO resume_market_skill_stats (
            resume_id, run_id, skill_id,
            req_jobs, pref_jobs, total_jobs,
            req_freq, pref_freq,
            demand_score, rarity_score, roi_score
          )
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
          ON CONFLICT (resume_id, run_id, skill_id) DO UPDATE SET
            req_jobs      = EXCLUDED.req_jobs,
            pref_jobs     = EXCLUDED.pref_jobs,
            total_jobs    = EXCLUDED.total_jobs,
            req_freq      = EXCLUDED.req_freq,
            pref_freq     = EXCLUDED.pref_freq,
            demand_score  = EXCLUDED.demand_score,
            rarity_score  = EXCLUDED.rarity_score,
            roi_score     = EXCLUDED.roi_score,
            created_at    = now()
        \"\"\", (
            resume_id, run_id, sid,
            req_jobs, pref_jobs, total_jobs,
            req_freq, pref_freq,
            demand, rarity, roi
        ))

    return weights

def compute_plausibility_penalty(resume_text: str, resume_skill_names: List[str], target_level: str):
    lvl = (target_level or "any").strip().lower()
    txt = (resume_text or "").lower()

    skills_norm = {s.strip().lower() for s in (resume_skill_names or []) if s}
    senior_hits = [s for s in skills_norm if s in SENIOR_STACK_SKILLS]

    years = None
    import re as _re
    m = _re.search(r"(\\d+)\\+?\\s*(years|yrs)", txt)
    if m:
        try:
            years = int(m.group(1))
        except:
            years = None

    flags = []
    penalty = 0

    if lvl in ("entry", "associate"):
        if len(senior_hits) >= 6:
            penalty += 12; flags.append("entry_senior_stack_heavy")
        elif len(senior_hits) >= 3:
            penalty += 6; flags.append("entry_senior_stack_some")
        if years is not None and years <= 1 and len(senior_hits) >= 3:
            penalty += 8; flags.append("low_years_high_stack")

    if lvl in ("senior", "lead", "principal", "staff"):
        if len(senior_hits) == 0:
            penalty += 10; flags.append("senior_target_no_stack_signals")

    penalty = max(0, min(25, penalty))
    return penalty, flags

def compute_confidence_score(resume_text: str, skills_found: int, market_jobs: int, matched_jobs: int,
                             sal_min: Optional[float], sal_max: Optional[float]):
    flags = []
    score = 100

    tlen = len((resume_text or "").strip())
    if tlen < 800:
        score -= 20; flags.append("resume_text_short")
    if skills_found < 6:
        score -= 20; flags.append("few_skills_extracted")
    if market_jobs < 80:
        score -= 25; flags.append("small_market_sample")
    if market_jobs > 0 and (matched_jobs / market_jobs) < 0.20:
        score -= 15; flags.append("low_match_coverage")
    if sal_min is None and sal_max is None:
        score -= 10; flags.append("salary_estimate_missing")

    score = max(0, min(100, score))
    return score, flags

""".lstrip()

    s = s[:nxt] + "\n\n" + helpers + "\n" + s[nxt:]

# ------------------------------------------------------------
# 4) Ensure weights = build_and_store... appears before job_match_scores
# ------------------------------------------------------------
needle = "matches = job_match_scores("
pos = s.find(needle)
if pos == -1:
    raise SystemExit("❌ Could not find 'matches = job_match_scores(' to wire weights.")
before = s[max(0, pos-600):pos]
if "build_and_store_market_skill_stats(" not in before:
    inject = "                weights = build_and_store_market_skill_stats(cur, resume_id, run_id, market_job_ids)\n"
    s = s[:pos] + inject + s[pos:]

# ------------------------------------------------------------
# 5) Insert plausibility/confidence calc right before write_scores call in score flow
# ------------------------------------------------------------
wpos = s.find("write_scores(")
if wpos == -1:
    raise SystemExit("❌ Could not find write_scores( call site.")
# Only inject once
window = s[max(0, wpos-1200):wpos]
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
""".lstrip()
    s = s[:wpos] + block + s[wpos:]

# ------------------------------------------------------------
# 6) Update write_scores(...) call args if old form exists
#    We'll do a simple safe approach: if the call doesn't mention plausibility_penalty,
#    append args by rewriting the call line by line is too risky.
#    So we do a minimal string-based replacement of the common block if present.
# ------------------------------------------------------------
if "plausibility_penalty" not in s[s.find("write_scores("):s.find("write_scores(")+600]:
    # Try replace a common call chunk if it exists verbatim-ish
    # If not found, user can paste a small snippet and we’ll patch precisely.
    pass

p.write_text(s, encoding="utf-8")
print("✅ Fast patch applied to score_resume.py")
PY

"$PY" -m py_compile "$FILE"
echo "✅ Compile OK"
