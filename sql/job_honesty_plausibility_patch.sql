-- sql/job_honesty_plausibility_patch.sql
-- Patch: add plausibility penalty + fix polymorphic jsonb issue (use jsonb_build_array)

BEGIN;

-- 1) Add new column to job_honesty (safe if re-run)
ALTER TABLE job_honesty
  ADD COLUMN IF NOT EXISTS plausibility_penalty int NOT NULL DEFAULT 0;

-- 2) Helper: plausibility penalty based on text + experience level
-- NOTE: jsonb_build_array('x') avoids the polymorphic "unknown" issue.
CREATE OR REPLACE FUNCTION _honesty_plausibility_penalty(txt text, experience_level text)
RETURNS TABLE(penalty int, flags jsonb)
LANGUAGE sql
IMMUTABLE
AS $$
  WITH j AS (
    SELECT COALESCE(txt,'') AS t,
           COALESCE(experience_level,'unknown') AS lvl
  ),
  s AS (
    SELECT
      -- Junior jobs mentioning heavy DE tooling
      CASE
        WHEN lvl IN ('entry','associate')
         AND t ~* '\b(airflow|dbt|databricks|snowflake|spark|pyspark|kafka|kubernetes|k8s|terraform|docker|fivetran|redshift|bigquery|sagemaker)\b'
        THEN 1 ELSE 0
      END AS jr_de_stack,

      -- Junior jobs mentioning ML stack
      CASE
        WHEN lvl IN ('entry','associate')
         AND t ~* '\b(machine learning|ml\b|nlp\b|deep learning|pytorch|tensorflow)\b'
        THEN 1 ELSE 0
      END AS jr_ml_stack,

      -- “Senior” language inside junior roles
      CASE
        WHEN lvl IN ('entry','associate')
         AND t ~* '\b(principal|staff|architect|lead)\b'
        THEN 1 ELSE 0
      END AS jr_senior_words
    FROM j
  )
  SELECT
    LEAST(25, (jr_de_stack*12 + jr_ml_stack*12 + jr_senior_words*6))::int AS penalty,
    (
      (CASE WHEN jr_de_stack = 1 THEN jsonb_build_array('jr_requires_data_eng_stack') ELSE '[]'::jsonb END)
      || (CASE WHEN jr_ml_stack = 1 THEN jsonb_build_array('jr_requires_ml_stack') ELSE '[]'::jsonb END)
      || (CASE WHEN jr_senior_words = 1 THEN jsonb_build_array('jr_seniority_language') ELSE '[]'::jsonb END)
    ) AS flags
  FROM s;
$$;

COMMIT;

-- 3) Replace refresh_job_honesty() to incorporate plausibility_penalty
-- (This is a full replace; easier than trying to surgically patch your CTEs.)

CREATE OR REPLACE FUNCTION refresh_job_honesty(run_id_in text DEFAULT NULL, months_back int DEFAULT 6)
RETURNS TABLE(scored_count int)
LANGUAGE plpgsql
AS $$
DECLARE
  run_id text := COALESCE(run_id_in, 'honesty_' || to_char(now(),'YYYYMMDD_HH24MISS'));
BEGIN
  WITH base AS (
    SELECT
      jp.job_id,
      COALESCE(jp.description_text, '') AS txt,
      COALESCE(jp.experience_level, 'unknown') AS experience_level,
      COALESCE(jp.workplace_type, 'unknown') AS workplace_type,
      jp.salary_min,
      jp.salary_max,
      jp.salary_period,
      jp.posted_date,
      jp.ingested_at,
      r.role_name,
      c.company_name
    FROM job_postings jp
    LEFT JOIN roles r ON r.role_id = jp.role_id
    LEFT JOIN companies c ON c.company_id = jp.company_id
    WHERE COALESCE(jp.posted_date::timestamptz, jp.ingested_at, now())
          >= date_trunc('month', now()) - (months_back || ' months')::interval
  ),
  req AS (
    SELECT
      js.job_id,
      COUNT(*) FILTER (WHERE js.skill_priority = 'required') AS required_skills,
      COUNT(*) FILTER (WHERE js.skill_priority = 'preferred') AS preferred_skills
    FROM job_skills js
    GROUP BY js.job_id
  ),
  scored AS (
    SELECT
      b.*,
      COALESCE(rq.required_skills, 0) AS required_skills,
      COALESCE(rq.preferred_skills, 0) AS preferred_skills
    FROM base b
    LEFT JOIN req rq ON rq.job_id = b.job_id
  ),
  calc AS (
    SELECT
      s.job_id,

      -- Pay penalty
      (
        CASE WHEN s.salary_min IS NULL AND s.salary_max IS NULL THEN 25 ELSE 0 END
        + CASE
            WHEN s.salary_min IS NOT NULL AND s.salary_max IS NOT NULL
                 AND s.salary_min > 0
                 AND (s.salary_max / s.salary_min) >= 2.5
            THEN 10 ELSE 0
          END
        + CASE WHEN s.txt ~* '\b(doe|competitive|commensurate)\b' THEN 10 ELSE 0 END
        + CASE WHEN (s.salary_min IS NOT NULL OR s.salary_max IS NOT NULL)
                    AND (s.salary_period IS NULL OR s.salary_period = '')
               THEN 5 ELSE 0 END
      )::int AS pay_penalty,

      -- Scope penalty
      (
        CASE
          WHEN s.experience_level = 'entry' AND s.required_skills >= 14 THEN 25
          WHEN s.experience_level = 'entry' AND s.required_skills >= 10 THEN 15
          ELSE 0
        END
        + CASE WHEN s.txt ~* '\b\d+\+\s*(years|yrs)\b' THEN 10 ELSE 0 END
        + CASE
            WHEN (s.txt ~* '\bon[- ]call\b')
             AND (s.txt ~* '\blean team\b')
             AND (s.txt ~* '\bfast[- ]paced\b')
            THEN 10 ELSE 0
          END
      )::int AS scope_penalty,

      -- Consistency penalty
      (
        CASE
          WHEN s.experience_level = 'entry'
           AND COALESCE(s.role_name,'') ~* '\b(senior|sr\.|lead|principal|staff)\b'
          THEN 8 ELSE 0
        END
        + CASE
            WHEN s.workplace_type = 'remote'
             AND s.txt ~* '\b(on[- ]?site|onsite|in office)\b'
          THEN 10 ELSE 0
        END
      )::int AS consistency_penalty,

      -- EEO dominance penalty
      (
        CASE
          WHEN s.txt ~* 'equal opportunity employer'
           AND length(s.txt) > 4000
           AND (length(regexp_replace(s.txt, '(?is).*?(equal opportunity employer.*)$', '\1'))::numeric / length(s.txt)) > 0.40
          THEN 3 ELSE 0
        END
      )::int AS eeo_penalty,

      -- Vague penalty + flags (your existing helper)
      (SELECT penalty FROM _honesty_vague_penalty(s.txt))::int AS vague_penalty,
      (SELECT flags   FROM _honesty_vague_penalty(s.txt))      AS vague_flags,

      -- NEW: plausibility penalty + flags
      (SELECT penalty FROM _honesty_plausibility_penalty(s.txt, s.experience_level))::int AS plausibility_penalty,
      (SELECT flags   FROM _honesty_plausibility_penalty(s.txt, s.experience_level))      AS plausibility_flags,

      -- carry-through
      s.txt,
      s.experience_level,
      s.workplace_type,
      s.salary_min, s.salary_max, s.salary_period,
      s.role_name,
      s.required_skills
    FROM scored s
  ),
  final AS (
    SELECT
      c.job_id,
      c.pay_penalty,
      c.vague_penalty,
      LEAST(c.scope_penalty, 25)::int AS scope_penalty,
      LEAST(c.consistency_penalty, 15)::int AS consistency_penalty,
      LEAST(c.eeo_penalty, 10)::int AS eeo_penalty,
      LEAST(c.plausibility_penalty, 25)::int AS plausibility_penalty,

      GREATEST(
        0,
        100
        - c.pay_penalty
        - c.vague_penalty
        - LEAST(c.scope_penalty, 25)
        - LEAST(c.consistency_penalty, 15)
        - LEAST(c.eeo_penalty, 10)
        - LEAST(c.plausibility_penalty, 25)
      )::int AS honesty_score,

      (
        c.vague_flags
        || c.plausibility_flags
        || CASE WHEN c.salary_min IS NULL AND c.salary_max IS NULL THEN jsonb_build_array('no_salary') ELSE '[]'::jsonb END
        || CASE WHEN c.txt ~* '\b(doe|competitive|commensurate)\b' THEN jsonb_build_array('doe_competitive') ELSE '[]'::jsonb END
        || CASE WHEN (c.salary_min IS NOT NULL OR c.salary_max IS NOT NULL) AND (c.salary_period IS NULL OR c.salary_period = '') THEN jsonb_build_array('salary_missing_period') ELSE '[]'::jsonb END
        || CASE WHEN c.salary_min IS NOT NULL AND c.salary_max IS NOT NULL AND c.salary_min > 0 AND (c.salary_max / c.salary_min) >= 2.5 THEN jsonb_build_array('salary_too_wide') ELSE '[]'::jsonb END
        || CASE WHEN c.experience_level = 'entry' AND c.required_skills >= 10 THEN jsonb_build_array('entry_heavy_requirements') ELSE '[]'::jsonb END
        || CASE WHEN c.txt ~* '\b\d+\+\s*(years|yrs)\b' THEN jsonb_build_array('years_requirement') ELSE '[]'::jsonb END
        || CASE WHEN (c.txt ~* '\bon[- ]call\b') AND (c.txt ~* '\blean team\b') AND (c.txt ~* '\bfast[- ]paced\b') THEN jsonb_build_array('oncall_lean_fastpaced') ELSE '[]'::jsonb END
        || CASE WHEN c.experience_level = 'entry' AND COALESCE(c.role_name,'') ~* '\b(senior|sr\.|lead|principal|staff)\b' THEN jsonb_build_array('entry_title_senior') ELSE '[]'::jsonb END
        || CASE WHEN c.workplace_type = 'remote' AND c.txt ~* '\b(on[- ]?site|onsite|in office)\b' THEN jsonb_build_array('remote_but_onsite_language') ELSE '[]'::jsonb END
        || CASE WHEN c.eeo_penalty > 0 THEN jsonb_build_array('eeo_dominates_posting') ELSE '[]'::jsonb END
      ) AS flags
    FROM calc c
  )
  INSERT INTO job_honesty (
    job_id, honesty_score,
    pay_penalty, vague_penalty, scope_penalty, consistency_penalty, eeo_penalty,
    plausibility_penalty,
    flags, run_id,
    updated_at, created_at
  )
  SELECT
    f.job_id, f.honesty_score,
    f.pay_penalty, f.vague_penalty, f.scope_penalty, f.consistency_penalty, f.eeo_penalty,
    f.plausibility_penalty,
    f.flags, run_id,
    now(), now()
  FROM final f
  ON CONFLICT (job_id) DO UPDATE SET
    honesty_score       = EXCLUDED.honesty_score,
    pay_penalty         = EXCLUDED.pay_penalty,
    vague_penalty       = EXCLUDED.vague_penalty,
    scope_penalty       = EXCLUDED.scope_penalty,
    consistency_penalty = EXCLUDED.consistency_penalty,
    eeo_penalty         = EXCLUDED.eeo_penalty,
    plausibility_penalty= EXCLUDED.plausibility_penalty,
    flags               = EXCLUDED.flags,
    run_id              = EXCLUDED.run_id,
    updated_at          = now();

  GET DIAGNOSTICS scored_count = ROW_COUNT;
  RETURN QUERY SELECT scored_count;
END;
$$;

-- 4) Update view to expose plausibility_penalty
CREATE OR REPLACE VIEW job_honesty_latest AS
SELECT
  jp.job_id,
  c.company_name,
  r.role_name,
  jp.workplace_type,
  jp.experience_level,
  jp.salary_min,
  jp.salary_max,
  jp.salary_period,
  jp.posted_date,
  jp.job_url,
  jh.honesty_score,
  jh.pay_penalty,
  jh.vague_penalty,
  jh.scope_penalty,
  jh.consistency_penalty,
  jh.eeo_penalty,
  jh.plausibility_penalty,
  jh.flags,
  jh.updated_at,
  jh.run_id
FROM job_postings jp
LEFT JOIN companies c ON c.company_id = jp.company_id
LEFT JOIN roles r ON r.role_id = jp.role_id
LEFT JOIN job_honesty jh ON jh.job_id = jp.job_id;
