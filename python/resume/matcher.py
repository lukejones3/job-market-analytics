"""Match resume skills against active jobs and rank by fit.

Match formula:
    match_score = (
        0.50 * skill_overlap_pct
      + 0.20 * exp_level_fit
      + 0.20 * salary_fit
      + 0.10 * freshness_quality
    )

skill_overlap_pct = |resume_skills ∩ job_required_skills| / |job_required_skills|
exp_level_fit     = 1.0 if exact, 0.5 if ±1 level, 0.0 otherwise
salary_fit        = 1.0 if no floor or job >= floor; 0.0 if disclosed and below
freshness_quality = quality_score (0-1) * recency_decay

Returns top N jobs with breakdown.
"""

from typing import Dict, List, Set, Optional, Any
from datetime import datetime, timedelta

try:
    from company_blocklist import ABSOLUTE_BLOCK as _ABSOLUTE_BLOCK
except ImportError:
    import os as _os, sys as _sys
    _parent = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))
    if _parent not in _sys.path:
        _sys.path.insert(0, _parent)
    from company_blocklist import ABSOLUTE_BLOCK as _ABSOLUTE_BLOCK

_BLOCKED_LOWER = [name.lower() for name in _ABSOLUTE_BLOCK]


EXP_ORDER = {"entry": 0, "associate": 1, "mid": 2, "senior": 3}


def _exp_level_fit(resume_level: str, job_level: Optional[str]) -> float:
    """1.0 if exact match, 0.5 if ±1 level, 0.0 otherwise."""
    if not job_level or job_level not in EXP_ORDER:
        return 0.7  # unknown — treat as mild positive (don't penalize too much)
    if resume_level not in EXP_ORDER:
        return 0.5
    diff = abs(EXP_ORDER[resume_level] - EXP_ORDER[job_level])
    if diff == 0:
        return 1.0
    if diff == 1:
        return 0.5
    return 0.0


def _salary_fit(salary_floor: Optional[float], job_min: Optional[float], job_max: Optional[float]) -> float:
    """1.0 if no floor or job pays it. 0.0 if disclosed and below floor. 0.7 if undisclosed."""
    if salary_floor is None or salary_floor <= 0:
        return 1.0
    if job_min is None and job_max is None:
        return 0.7  # undisclosed — neutral-to-positive
    job_high = job_max if job_max else job_min
    if job_high is None:
        return 0.7
    if job_high >= salary_floor:
        return 1.0
    # Below floor — partial credit if close (within 10%)
    if job_high >= salary_floor * 0.9:
        return 0.5
    return 0.0


def _freshness_quality(quality: Optional[float], ingested_at: Optional[datetime]) -> float:
    """Combine quality score (0-1 or 0-100) with recency decay."""
    if quality is None:
        q = 0.5
    else:
        q = quality / 100.0 if quality > 1.0 else float(quality)
        q = max(0.0, min(1.0, q))

    if ingested_at is None:
        return q

    # Decay: 1.0 if <3d, linearly to 0.5 at 30d
    if isinstance(ingested_at, datetime):
        age_days = (datetime.now(ingested_at.tzinfo) - ingested_at).days
    else:
        return q
    if age_days <= 3:
        decay = 1.0
    elif age_days >= 30:
        decay = 0.5
    else:
        decay = 1.0 - (0.5 * (age_days - 3) / 27)
    return q * decay


def _skill_overlap_pct(resume_skill_ids: Set[str], job_skill_ids: Set[str]) -> float:
    """Balanced resume/job skill fit (F1), not one-sided job coverage.

    The old denominator contained only job skills, so a posting with four generic
    skills could score 100% against a fifteen-skill resume. F1 retains coverage
    while penalizing shallow, generic matches.
    """
    n_job = len(job_skill_ids)
    if n_job == 0:
        return 0.0  # no signal
    overlap = resume_skill_ids & job_skill_ids
    if not overlap or not resume_skill_ids:
        return 0.0
    job_recall = len(overlap) / n_job
    resume_precision = len(overlap) / len(resume_skill_ids)
    raw = 2 * job_recall * resume_precision / (job_recall + resume_precision)
    if n_job == 1:
        return min(raw, 0.40)
    if n_job == 2:
        return min(raw, 0.55)
    if n_job == 3:
        return min(raw, 0.75)
    return raw


def match_jobs(
    resume_skills: Dict[str, Dict[str, Any]],
    exp_level: str,
    salary_floor: Optional[float],
    db_cursor,
    top_n: int = 50,
    resume_embedding: Optional[List[float]] = None,
) -> List[Dict[str, Any]]:
    """Rank active jobs by match score against the resume.

    Args:
        resume_skills: {skill_id: {name, confidence, ...}} from extract_skills()
        exp_level: 'entry' | 'associate' | 'mid' | 'senior'
        salary_floor: User's salary floor in USD (None = no preference)
        db_cursor: Active psycopg2 cursor
        top_n: How many top matches to return
        resume_embedding: 384-dim sentence-transformer vector. When provided,
            pre-ranks via pgvector cosine distance and blends semantic similarity
            into the score (0.60 * semantic + 0.15 * skill + 0.15 * exp + 0.10 * salary).
            Falls back to skill-overlap-only scoring when None.

    Returns:
        List of dicts, sorted by match_score desc:
        {
            job_id, role_name, company_name, sector,
            loc_city, loc_state, workplace_type,
            salary_min, salary_max, experience_level,
            quality_score, ingested_at, job_url,
            semantic_similarity, match_score, skill_overlap_pct,
            matched_skills, missing_skills, score_breakdown
        }
    """
    resume_skill_ids = set(resume_skills.keys())
    use_embedding = resume_embedding is not None

    if use_embedding:
        emb_str = '[' + ','.join(str(x) for x in resume_embedding) + ']'
        db_cursor.execute("""
            WITH nearest AS MATERIALIZED (
              SELECT
                jp.job_id, jp.experience_level, jp.salary_min_annual, jp.salary_max_annual,
                jp.workplace_type, jp.ingested_at, jp.job_url, jp.loc_city, jp.loc_state,
                jp.loc_country, r.role_name, c.company_name, c.sector,
                jh.honesty_score AS quality_score,
                1 - (jp.embedding <=> %s::vector) AS semantic_similarity
              FROM job_postings jp
              JOIN roles r ON r.role_id = jp.role_id
              JOIN companies c ON c.company_id = jp.company_id
              LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
              WHERE jp.status = 'raw'
                AND COALESCE(jp.data_tier, 1) = 1
                AND COALESCE(jp.loc_country, 'US') IN ('US', 'unknown')
                AND jp.domain IS NOT NULL
                AND jp.role_category IS NOT NULL
                AND jp.role_category != 'non_data'
                AND jp.embedding IS NOT NULL
                AND LOWER(c.company_name) != ALL(%s)
              ORDER BY jp.embedding <=> %s::vector
              LIMIT 200
            )
            SELECT
                nearest.*,
                COALESCE(
                    ARRAY_AGG(DISTINCT js.skill_id) FILTER (WHERE js.skill_id IS NOT NULL),
                    '{}'::text[]
                ) AS skill_ids
            FROM nearest
            LEFT JOIN job_skills js ON js.job_id = nearest.job_id
            GROUP BY nearest.job_id, nearest.experience_level, nearest.salary_min_annual,
                nearest.salary_max_annual, nearest.workplace_type, nearest.ingested_at,
                nearest.job_url, nearest.loc_city, nearest.loc_state, nearest.loc_country,
                nearest.role_name, nearest.company_name, nearest.sector,
                nearest.quality_score, nearest.semantic_similarity
            ORDER BY nearest.semantic_similarity DESC
        """, (emb_str, _BLOCKED_LOWER, emb_str))
    else:
        # Fallback: skill-overlap-only path (no embedding required)
        db_cursor.execute("""
            SELECT
                jp.job_id,
                jp.experience_level,
                jp.salary_min_annual,
                jp.salary_max_annual,
                jp.workplace_type,
                jp.ingested_at,
                jp.job_url,
                jp.loc_city,
                jp.loc_state,
                jp.loc_country,
                r.role_name,
                c.company_name,
                c.sector,
                jh.honesty_score AS quality_score,
                COALESCE(
                    ARRAY_AGG(DISTINCT js.skill_id) FILTER (WHERE js.skill_id IS NOT NULL),
                    '{}'::text[]
                ) AS skill_ids
            FROM job_postings jp
            JOIN roles r ON r.role_id = jp.role_id
            LEFT JOIN companies c ON c.company_id = jp.company_id
            LEFT JOIN job_skills js ON js.job_id = jp.job_id
            LEFT JOIN job_honesty_latest jh ON jh.job_id = jp.job_id
            WHERE jp.status = 'raw'
              AND COALESCE(jp.data_tier, 1) = 1
              AND COALESCE(jp.loc_country, 'US') IN ('US', 'unknown')
              AND jp.domain IS NOT NULL
              AND jp.role_category IS NOT NULL
              AND jp.role_category != 'non_data'
              AND LOWER(c.company_name) != ALL(%s)
            GROUP BY
                jp.job_id, jp.experience_level, jp.salary_min_annual, jp.salary_max_annual,
                jp.workplace_type, jp.ingested_at, jp.job_url,
                jp.loc_city, jp.loc_state, jp.loc_country,
                r.role_name, c.company_name, c.sector, jh.honesty_score
            HAVING COUNT(DISTINCT js.skill_id) > 0
        """, (_BLOCKED_LOWER,))

    rows = db_cursor.fetchall()
    matches = []

    # Bulk fetch skill names for all skills involved (resume + jobs)
    all_job_skill_ids = set()
    for r in rows:
        sids = r["skill_ids"] if isinstance(r, dict) else r["skill_ids"]
        if sids:
            all_job_skill_ids.update(sids)

    needed_ids = list(all_job_skill_ids | resume_skill_ids)
    skill_name_map = {}
    if needed_ids:
        db_cursor.execute(
            "SELECT skill_id, skill_name FROM skills WHERE skill_id = ANY(%s)",
            (needed_ids,),
        )
        for row in db_cursor.fetchall():
            sid = row["skill_id"] if isinstance(row, dict) else row[0]
            sname = row["skill_name"] if isinstance(row, dict) else row[1]
            skill_name_map[sid] = sname

    for r in rows:
        get = (lambda k: r[k]) if isinstance(r, dict) else (lambda k: r[k])

        job_skill_ids = set(get("skill_ids") or [])
        overlap_pct = _skill_overlap_pct(resume_skill_ids, job_skill_ids)
        exp_fit = _exp_level_fit(exp_level, get("experience_level"))
        sal_fit = _salary_fit(salary_floor, get("salary_min_annual"), get("salary_max_annual"))
        fresh_q = _freshness_quality(get("quality_score"), get("ingested_at"))

        if use_embedding:
            semantic_sim = float(get("semantic_similarity") or 0.0)
            score = (
                0.60 * semantic_sim
                + 0.15 * overlap_pct
                + 0.15 * exp_fit
                + 0.10 * sal_fit
            )
            breakdown = {
                "semantic": round(semantic_sim, 3),
                "skill_overlap": round(overlap_pct, 3),
                "exp_fit": round(exp_fit, 3),
                "salary_fit": round(sal_fit, 3),
            }
        else:
            semantic_sim = 0.0
            score = (
                0.50 * overlap_pct
                + 0.20 * exp_fit
                + 0.20 * sal_fit
                + 0.10 * fresh_q
            )
            breakdown = {
                "skill_overlap": round(overlap_pct, 3),
                "exp_fit": round(exp_fit, 3),
                "salary_fit": round(sal_fit, 3),
                "freshness_quality": round(fresh_q, 3),
            }

        matched = sorted([skill_name_map.get(s, s) for s in (resume_skill_ids & job_skill_ids)])
        missing = sorted([skill_name_map.get(s, s) for s in (job_skill_ids - resume_skill_ids)])

        matches.append({
            "job_id": get("job_id"),
            "role_name": get("role_name"),
            "company_name": get("company_name"),
            "sector": get("sector"),
            "loc_city": get("loc_city"),
            "loc_state": get("loc_state"),
            "loc_country": get("loc_country"),
            "workplace_type": get("workplace_type"),
            "salary_min": get("salary_min_annual"),
            "salary_max": get("salary_max_annual"),
            "experience_level": get("experience_level"),
            "quality_score": get("quality_score"),
            "ingested_at": get("ingested_at"),
            "job_url": get("job_url"),
            "semantic_similarity": round(semantic_sim, 4),
            "match_score": round(score, 4),
            "skill_overlap_pct": round(overlap_pct, 4),
            "matched_skills": matched,
            "missing_skills": missing,
            "score_breakdown": breakdown,
        })

    if use_embedding:
        matches.sort(key=lambda m: -m["match_score"])
    else:
        matches.sort(
            key=lambda m: (
                -m["match_score"],
                -m["skill_overlap_pct"],
                -(m["score_breakdown"]["freshness_quality"]),
            )
        )
    return matches[:top_n]


def find_skill_gaps(
    resume_skills: Dict[str, Dict[str, Any]],
    exp_level: str,
    db_cursor,
    top_n: int = 5,
    min_unlock_jobs: int = 3,
) -> List[Dict[str, Any]]:
    """Find high-value missing skills that would unlock more jobs.

    For each skill the resume DOESN'T have:
      - Count active jobs requiring it where the resume already matches >=70% of OTHER skills
      - Compute median salary delta vs jobs the resume currently matches

    Args:
        resume_skills: {skill_id: ...} from extract_skills()
        exp_level: For exp-fit filtering of unlocked jobs
        db_cursor: psycopg2 cursor
        top_n: Top N gaps to return
        min_unlock_jobs: Only return skills that unlock at least this many jobs

    Returns:
        List of dicts, sorted by unlock_count desc:
        {
            skill_id, skill_name,
            unlock_count,           # jobs this skill would unlock
            current_median_salary,  # median of jobs you currently strong-match
            unlock_median_salary,   # median of jobs unlocked by this skill
            salary_delta            # difference (positive = boost)
        }
    """
    resume_skill_ids = set(resume_skills.keys())

    if not resume_skill_ids:
        return []

    # Pull all active tier-1 jobs + their skills + salary
    db_cursor.execute("""
        SELECT
            jp.job_id,
            jp.experience_level,
            COALESCE(jp.salary_max_annual, jp.salary_min_annual) AS salary,
            COALESCE(
                ARRAY_AGG(DISTINCT js.skill_id) FILTER (WHERE js.skill_id IS NOT NULL),
                '{}'::text[]
            ) AS skill_ids
        FROM job_postings jp
        LEFT JOIN companies c ON c.company_id = jp.company_id
        LEFT JOIN job_skills js ON js.job_id = jp.job_id
        WHERE jp.status = 'raw'
          AND COALESCE(jp.data_tier, 1) = 1
          AND COALESCE(jp.loc_country, 'US') IN ('US', 'unknown')
          AND (jp.role_category IS NULL OR jp.role_category != 'non_data')
          AND LOWER(c.company_name) != ALL(%s)
        GROUP BY jp.job_id, jp.experience_level, jp.salary_min_annual, jp.salary_max_annual
        HAVING COUNT(DISTINCT js.skill_id) > 0
    """, (_BLOCKED_LOWER,))

    rows = db_cursor.fetchall()

    # Filter to exp-level-relevant jobs (within ±1 level)
    target_levels = set()
    if exp_level in EXP_ORDER:
        idx = EXP_ORDER[exp_level]
        for lvl, i in EXP_ORDER.items():
            if abs(i - idx) <= 1:
                target_levels.add(lvl)

    # Build per-skill stats for skills NOT on the resume
    # For each candidate skill: count of jobs where (resume covers >=70% of other skills) AND (job has this skill)
    from collections import defaultdict
    import statistics

    # Skill -> list of (job_id, salary, missing_skills_count, total_skills, has_target_level)
    candidate_data = defaultdict(list)
    current_match_salaries = []  # jobs where resume already strongly matches

    for r in rows:
        get = (lambda k: r[k]) if isinstance(r, dict) else (lambda k: r[k])
        job_level = get("experience_level")
        if target_levels and job_level not in target_levels:
            continue
        job_skills = set(get("skill_ids") or [])
        if not job_skills:
            continue
        salary = get("salary")
        # Skip if no salary signal — can't compute delta
        # (we still count unlock_count based on all jobs)
        overlap = resume_skill_ids & job_skills
        overlap_pct = len(overlap) / len(job_skills)

        if overlap_pct >= 0.70:
            if salary:
                current_match_salaries.append(float(salary))

        # For every skill in this job that's NOT on the resume, this is a candidate
        missing_in_job = job_skills - resume_skill_ids

        # Only count it as "would unlock" if the resume covers most of the OTHER skills
        # (i.e., adding this one skill would push overlap closer to 100%)
        for missing_sid in missing_in_job:
            other_skills = job_skills - {missing_sid}
            if not other_skills:
                continue
            other_overlap_pct = len(resume_skill_ids & other_skills) / len(other_skills)
            if other_overlap_pct >= 0.70:
                candidate_data[missing_sid].append({
                    "job_id": get("job_id"),
                    "salary": float(salary) if salary else None,
                })

    if not candidate_data:
        return []

    # Compute current median salary
    current_median = statistics.median(current_match_salaries) if current_match_salaries else None

    # Pull skill names
    candidate_ids = list(candidate_data.keys())
    db_cursor.execute(
        "SELECT skill_id, skill_name FROM skills WHERE skill_id = ANY(%s)",
        (candidate_ids,),
    )
    skill_name_map = {}
    for row in db_cursor.fetchall():
        sid = row["skill_id"] if isinstance(row, dict) else row[0]
        sname = row["skill_name"] if isinstance(row, dict) else row[1]
        skill_name_map[sid] = sname

    # Build output list
    gaps = []
    for sid, jobs in candidate_data.items():
        unlock_count = len(jobs)
        if unlock_count < min_unlock_jobs:
            continue
        salaries = [j["salary"] for j in jobs if j["salary"]]
        unlock_median = statistics.median(salaries) if salaries else None
        salary_delta = None
        if unlock_median is not None and current_median is not None:
            salary_delta = unlock_median - current_median

        gaps.append({
            "skill_id": sid,
            "skill_name": skill_name_map.get(sid, sid),
            "unlock_count": unlock_count,
            "current_median_salary": current_median,
            "unlock_median_salary": unlock_median,
            "salary_delta": salary_delta,
        })

    # Sort: prefer high unlock_count, then high salary_delta
    gaps.sort(
        key=lambda g: (
            -g["unlock_count"],
            -(g["salary_delta"] or 0),
        )
    )
    return gaps[:top_n]
