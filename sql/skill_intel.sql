/* ============================================================
   SKILL INTELLIGENCE ENGINE
   - Skill candidate promotion
   - Monthly skill demand snapshots
   - Emerging skill detection (MoM)
   ============================================================ */


/* ============================================================
   1) AUTO-PROMOTE SKILL CANDIDATES
   ============================================================ */

CREATE OR REPLACE FUNCTION promote_skill_candidates(
    min_seen integer DEFAULT 3,
    min_conf  numeric DEFAULT 0.80
)
RETURNS TABLE(promoted_count integer)
LANGUAGE plpgsql
AS $$
DECLARE
    r RECORD;
    new_skill_id text;
    promoted integer := 0;
BEGIN
    FOR r IN
        SELECT *
        FROM skill_candidates
        WHERE status = 'new'
          AND mapped_skill_id IS NULL
          AND seen_count >= min_seen
          AND confidence >= min_conf
    LOOP
        -- generate next skill_id like S001, S002...
        SELECT 'S' ||
               lpad((COALESCE(MAX(regexp_replace(skill_id,'\D','','g')::int),0)+1)::text,3,'0')
        INTO new_skill_id
        FROM skills;

        -- insert canonical skill
        INSERT INTO skills (skill_id, skill_name, skill_group)
        VALUES (new_skill_id, r.normalized_text, r.skill_type_guess);

        -- insert alias mapping
        INSERT INTO skill_aliases (alias_text, skill_id, note)
        VALUES (r.normalized_text, new_skill_id, 'auto-promoted from skill_candidates');

        -- mark candidate mapped
        UPDATE skill_candidates
        SET mapped_skill_id = new_skill_id,
            status = 'mapped'
        WHERE candidate_id = r.candidate_id;

        promoted := promoted + 1;
    END LOOP;

    RETURN QUERY SELECT promoted;
END;
$$;



/* ============================================================
   2) MONTHLY SKILL DEMAND SNAPSHOTS
   (Aligned to your real table schema)
   ============================================================ */

CREATE OR REPLACE FUNCTION refresh_skill_demand_monthly_snapshots(
    run_id_in text,
    months_back integer DEFAULT 6
)
RETURNS void
LANGUAGE plpgsql
AS $$
BEGIN

  INSERT INTO skill_demand_monthly_snapshots (
    snapshot_date,
    run_id,
    month_bucket,
    skill_id,
    experience_level,
    skill_priority,
    jobs_count,
    avg_salary_min,
    avg_salary_max,
    updated_at,
    created_at
  )
  SELECT
    CURRENT_DATE AS snapshot_date,
    run_id_in    AS run_id,
    date_trunc('month', COALESCE(jp.posted_date, jp.created_at, now()))::date AS month_bucket,
    js.skill_id,
    COALESCE(jp.experience_level, 'unknown') AS experience_level,
    COALESCE(js.skill_priority, 'required')  AS skill_priority,
    COUNT(DISTINCT js.job_id) AS jobs_count,
    AVG(jp.salary_min) AS avg_salary_min,
    AVG(jp.salary_max) AS avg_salary_max,
    now() AS updated_at,
    now() AS created_at
  FROM job_skills js
  JOIN job_postings jp ON jp.job_id = js.job_id
  WHERE COALESCE(jp.posted_date, jp.created_at, now())
        >= date_trunc('month', now()) - (months_back || ' months')::interval
  GROUP BY 1,2,3,4,5,6;

END;
$$;



/* ============================================================
   3) EMERGING SKILLS (Month-over-Month)
   Uses latest snapshot per month
   Aggregates across experience + priority
   ============================================================ */

CREATE OR REPLACE VIEW v_skill_emerging_mom AS
WITH month_keys AS (
  SELECT
    date_trunc('month', now())::date AS cur_m,
    (date_trunc('month', now()) - interval '1 month')::date AS prev_m
),
latest_per_month AS (
  SELECT
    month_bucket,
    MAX(snapshot_date) AS snapshot_date
  FROM skill_demand_monthly_snapshots
  GROUP BY month_bucket
),
cur AS (
  SELECT
    sms.skill_id,
    SUM(sms.jobs_count) AS jobs_count
  FROM skill_demand_monthly_snapshots sms
  JOIN month_keys mk ON sms.month_bucket = mk.cur_m
  JOIN latest_per_month lpm
    ON lpm.month_bucket = sms.month_bucket
   AND lpm.snapshot_date = sms.snapshot_date
  GROUP BY sms.skill_id
),
prev AS (
  SELECT
    sms.skill_id,
    SUM(sms.jobs_count) AS jobs_count
  FROM skill_demand_monthly_snapshots sms
  JOIN month_keys mk ON sms.month_bucket = mk.prev_m
  JOIN latest_per_month lpm
    ON lpm.month_bucket = sms.month_bucket
   AND lpm.snapshot_date = sms.snapshot_date
  GROUP BY sms.skill_id
)
SELECT
  s.skill_id,
  s.skill_name,
  COALESCE(p.jobs_count, 0) AS prev_month_jobs,
  COALESCE(c.jobs_count, 0) AS cur_month_jobs,
  (COALESCE(c.jobs_count, 0) - COALESCE(p.jobs_count, 0)) AS delta_jobs
FROM skills s
LEFT JOIN cur c  ON c.skill_id = s.skill_id
LEFT JOIN prev p ON p.skill_id = s.skill_id
WHERE COALESCE(c.jobs_count, 0) > 0
ORDER BY delta_jobs DESC, cur_month_jobs DESC;



/* ============================================================
   4) EMERGING SKILLS BY EXPERIENCE LEVEL (Optional but Powerful)
   ============================================================ */

CREATE OR REPLACE VIEW v_skill_emerging_by_experience AS
WITH month_keys AS (
  SELECT
    date_trunc('month', now())::date AS cur_m,
    (date_trunc('month', now()) - interval '1 month')::date AS prev_m
),
latest_per_month AS (
  SELECT month_bucket, MAX(snapshot_date) AS snapshot_date
  FROM skill_demand_monthly_snapshots
  GROUP BY month_bucket
),
cur AS (
  SELECT skill_id, experience_level, SUM(jobs_count) AS jobs
  FROM skill_demand_monthly_snapshots sms
  JOIN month_keys mk ON sms.month_bucket = mk.cur_m
  JOIN latest_per_month lpm
    ON lpm.month_bucket = sms.month_bucket
   AND lpm.snapshot_date = sms.snapshot_date
  GROUP BY 1,2
),
prev AS (
  SELECT skill_id, experience_level, SUM(jobs_count) AS jobs
  FROM skill_demand_monthly_snapshots sms
  JOIN month_keys mk ON sms.month_bucket = mk.prev_m
  JOIN latest_per_month lpm
    ON lpm.month_bucket = sms.month_bucket
   AND lpm.snapshot_date = sms.snapshot_date
  GROUP BY 1,2
)
SELECT
  s.skill_name,
  c.experience_level,
  COALESCE(p.jobs,0) AS prev_jobs,
  c.jobs AS cur_jobs,
  (c.jobs - COALESCE(p.jobs,0)) AS delta
FROM cur c
JOIN skills s ON s.skill_id = c.skill_id
LEFT JOIN prev p
  ON p.skill_id = c.skill_id
 AND p.experience_level = c.experience_level
ORDER BY delta DESC, cur_jobs DESC;
