#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
FILE="${REPO}/python/score_resume.py"
ts="$(date +%Y%m%d_%H%M%S)"

if [[ ! -f "$FILE" ]]; then
  echo "❌ Not found: $FILE"
  exit 1
fi

cp "$FILE" "${FILE}.bak.features_${ts}"
echo "🧾 Backup: ${FILE}.bak.features_${ts}"

python - <<'PY'
import re
from pathlib import Path

p = Path.home() / "github" / "job-market-analytics" / "python" / "score_resume.py"
s = p.read_text(encoding="utf-8")

# Ensure Json import exists (do not duplicate)
if "from psycopg2.extras import" in s and "Json" not in s:
    s = re.sub(r"(from psycopg2\.extras import[^\n]+)\n", r"\1, Json\n", s, count=1)

# ----------------------------
# 1) Replace write_scores() with extended version
# ----------------------------
new_write_scores = r'''
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
        """
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
        """,
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
'''.strip() + "\n\n"

# Replace the whole existing write_scores function block
m = re.search(r"(?ms)^def write_scores\([^)]*\):\n(?:.*\n)*?\n(?=def write_gaps\()", s)
if not m:
    raise SystemExit("❌ Could not locate write_scores() block (expected def write_gaps after it).")
s = s[:m.start()] + new_write_scores + s[m.end():]

# ----------------------------
# 2) Insert helper functions after compute_honesty_match()
# ----------------------------
helpers = r'''
SENIOR_STACK_SKILLS = {
    "airflow","dbt","databricks","snowflake","redshift","bigquery",
    "spark","pyspark","hadoop","kafka",
    "docker","kubernetes","terraform",
    "aws","gcp","azure","s3","lambda","glue","ec2",
}

def write_run_flag(cur, resume_id: str, run_id: str, flag_type: str, flag: str, value: Optional[float] = None):
    cur.execute("""
      INSERT INTO resume_run_flags (resume_id, run_id, flag_type, flag, value)
      VALUES (%s,%s,%s,%s,%s)
      ON CONFLICT (resume_id, run_id, flag_type, flag) DO UPDATE
      SET value = EXCLUDED.value
    """, (resume_id, run_id, flag_type, flag, value))

def build_and_store_market_skill_stats(cur, resume_id: str, run_id: str, market_job_ids: List[str]) -> Dict[str, Tuple[float,float,float,int,int,int]]:
    """
    Returns weights dict:
      skill_id -> (req_freq, pref_freq, weight, req_jobs, pref_jobs, total_jobs)
    Also writes resume_market_skill_stats rows.
    """
    if not market_job_ids:
        return {}

    cur.execute("""
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
      SELECT
        b.skill_id,
        b.req_jobs,
        b.pref_jobs,
        b.total_skill_jobs
      FROM base b
    """, (market_job_ids,))

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

        cur.execute("""
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
        """, (
            resume_id, run_id, sid,
            req_jobs, pref_jobs, total_jobs,
            req_freq, pref_freq,
            demand, rarity, roi
        ))

    return weights

def compute_plausibility_penalty(resume_text: str, resume_skill_names: List[str], target_level: str) -> Tuple[int, List[str]]:
    lvl = (target_level or "any").strip().lower()
    txt = (resume_text or "").lower()

    skills_norm = {s.strip().lower() for s in resume_skill_names if s}
    senior_hits = [s for s in skills_norm if s in SENIOR_STACK_SKILLS]

    years = None
    m = re.search(r"(\d+)\+?\s*(years|yrs)", txt)
    if m:
        try:
            years = int(m.group(1))
        except:
            years = None

    flags = []
    penalty = 0

    if lvl in ("entry", "associate"):
        if len(senior_hits) >= 6:
            penalty += 12
            flags.append("entry_senior_stack_heavy")
        elif len(senior_hits) >= 3:
            penalty += 6
            flags.append("entry_senior_stack_some")

        if years is not None and years <= 1 and len(senior_hits) >= 3:
            penalty += 8
            flags.append("low_years_high_stack")

    if lvl in ("senior", "lead", "principal", "staff"):
        if len(senior_hits) == 0:
            penalty += 10
            flags.append("senior_target_no_stack_signals")

    penalty = max(0, min(25, penalty))
    return penalty, flags

def compute_confidence_score(resume_text: str, skills_found: int, market_jobs: int, matched_jobs: int,
                             sal_min: Optional[float], sal_max: Optional[float]) -> Tuple[int, List[str]]:
    flags = []
    score = 100

    tlen = len((resume_text or "").strip())
    if tlen < 800:
        score -= 20
        flags.append("resume_text_short")
    if skills_found < 6:
        score -= 20
        flags.append("few_skills_extracted")
    if market_jobs < 80:
        score -= 25
        flags.append("small_market_sample")
    if market_jobs > 0 and (matched_jobs / market_jobs) < 0.20:
        score -= 15
        flags.append("low_match_coverage")
    if sal_min is None and sal_max is None:
        score -= 10
        flags.append("salary_estimate_missing")

    score = max(0, min(100, score))
    return score, flags
'''.strip() + "\n\n"

# Insert helpers only if not already present
if "build_and_store_market_skill_stats" not in s:
    mh = re.search(r"(?ms)^def compute_honesty_match\([^)]*\):\n(?:.*\n)*?\n(?=def write_scores\()", s)
    if not mh:
        raise SystemExit("❌ Could not locate compute_honesty_match() block to insert helpers after.")
    s = s[:mh.end()] + "\n" + helpers + s[mh.end():]

# ----------------------------
# 3) Wire into score_resume flow:
#    - weights now from build_and_store_market_skill_stats()
#    - compute plausibility + confidence and store run flags
#    - pass new fields to write_scores()
# ----------------------------
# Replace any existing call that defines weights from elsewhere only if we can see "matches = job_match_scores"
# We'll insert weights creation immediately BEFORE the matches call.

anchor = re.search(r"(?m)^\s*matches\s*=\s*job_match_scores\(", s)
if not anchor:
    raise SystemExit("❌ Could not locate matches = job_match_scores(...) to wire features.")

# Prevent double-insertion
pre = s[max(0, anchor.start()-800):anchor.start()]
if "build_and_store_market_skill_stats" not in pre:
    insert = r'''
                # Build market skill distribution + cache stats (used for weights + ROI)
                weights = build_and_store_market_skill_stats(cur, resume_id, run_id, market_job_ids)
'''.strip() + "\n"
    s = s[:anchor.start()] + insert + s[anchor.start():]

# Now patch the write_scores call site:
# find "write_scores(" call inside score_resume and replace arguments block by matching until closing paren at same indent.
ws = re.search(r"(?ms)^\s*write_scores\(\s*\n(?:.*\n)*?\s*\)\s*$", s)
if not ws:
    # fallback: search a line with write_scores(cur, ... on one line)
    ws = re.search(r"(?m)^\s*write_scores\(", s)
    if not ws:
        raise SystemExit("❌ Could not locate write_scores(...) call to update arguments.")

# We'll do a safer targeted replacement by replacing the known old signature call pattern (market_fit_score etc.)
s = re.sub(
    r"(?ms)^\s*write_scores\(\s*cur,\s*resume_id\s*=\s*resume_id,\s*run_id\s*=\s*run_id,\s*market_fit\s*=\s*fit,\s*percentile\s*=\s*pct,\s*sal_min\s*=\s*smin,\s*sal_max\s*=\s*smax,\s*honesty_match\s*=\s*honesty,\s*matched_jobs_count\s*=\s*len\(matches\),\s*top_jobs_considered\s*=\s*len\(market_job_ids\)\s*\)\s*$",
    r"""                write_scores(
                    cur,
                    resume_id=resume_id,
                    run_id=run_id,
                    market_fit=fit,
                    percentile=pct,
                    sal_min=smin,
                    sal_max=smax,
                    honesty_match=honesty,
                    matched_jobs_count=len(matches),
                    top_jobs_considered=len(market_job_ids),
                    plausibility_penalty=pl_pen,
                    confidence_score=conf_score,
                    confidence_flags=conf_flags
                )""",
    s
)

# If not replaced (maybe your write_scores call is formatted differently), we’ll insert plausibility/confidence calc
# near the end of score_resume right before write_scores(...) is called, by anchoring on "write_scores(" again.
# Insert plausibility/confidence calc only if not present.
if "compute_plausibility_penalty" not in s:
    wline = re.search(r"(?m)^\s*write_scores\(", s)
    if not wline:
        raise SystemExit("❌ Could not locate write_scores(...) line to insert plausibility/confidence logic.")
    inject = r'''
                # Fetch resume skill names (for plausibility heuristics)
                cur.execute("""
                  SELECT s.skill_name
                  FROM resume_skills rs
                  JOIN skills s ON s.skill_id = rs.skill_id
                  WHERE rs.resume_id=%s
                """, (resume_id,))
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
'''.strip() + "\n\n"
    s = s[:wline.start()] + inject + s[wline.start():]

# Ensure we still call write_gaps with the weights we generated.
# (No change needed; just make sure weights exists by now.)

p.write_text(s, encoding="utf-8")
print("✅ Patched python/score_resume.py with: market stats cache + plausibility + confidence + extended resume_scores.")
PY

python -m py_compile "$FILE"
echo "✅ Compile OK"
