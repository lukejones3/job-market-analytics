-- Hiring Difficulty Score View
-- Composite metric predicting which companies data roles are hardest to fill
-- Components: skill rarity (30%), role complexity (25%), salary competitiveness (30%), opacity (15%)
-- Normalized 0-100 within dataset. Higher = harder to fill.
-- Excludes skills flagged difficulty_relevant=false (BI tools, productivity apps)
-- Salary benchmarks capped at $500K to exclude Netflix L5/L6 outliers
-- Last updated: April 2026

CREATE OR REPLACE VIEW vw_hiring_difficulty AS

WITH

skill_freq AS (
    SELECT js.skill_id,
        COUNT(DISTINCT js.job_id)::float /
        (SELECT COUNT(*) FROM job_postings WHERE data_tier = 1) as pct
    FROM job_skills js
    JOIN job_postings jp ON jp.job_id = js.job_id
    WHERE jp.data_tier = 1
    GROUP BY js.skill_id
),

sector_medians AS (
    SELECT c.sector, jp.experience_level,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY jp.salary_max_annual) as median_max
    FROM job_postings jp
    JOIN companies c ON c.company_id = jp.company_id
    WHERE jp.data_tier = 1
    AND jp.salary_max_annual BETWEEN 50000 AND 500000
    AND jp.experience_level IS NOT NULL
    AND c.sector IS NOT NULL
    GROUP BY c.sector, jp.experience_level
),

sector_skill_avg AS (
    SELECT c.sector, jp.experience_level,
        AVG(sc.skill_count) as avg_skills
    FROM job_postings jp
    JOIN companies c ON c.company_id = jp.company_id
    JOIN (
        SELECT js.job_id, COUNT(*) as skill_count
        FROM job_skills js
        JOIN skills s ON s.skill_id = js.skill_id
        WHERE js.skill_priority = 'required'
        AND s.difficulty_relevant = true
        GROUP BY js.job_id
    ) sc ON sc.job_id = jp.job_id
    WHERE jp.data_tier = 1
    AND jp.experience_level IS NOT NULL
    AND c.sector IS NOT NULL
    GROUP BY c.sector, jp.experience_level
),

job_skill_counts AS (
    SELECT js.job_id, COUNT(*) as skill_count
    FROM job_skills js
    JOIN skills s ON s.skill_id = js.skill_id
    WHERE js.skill_priority = 'required'
    AND s.difficulty_relevant = true
    GROUP BY js.job_id
),

job_niche_ratio AS (
    SELECT js.job_id,
        AVG(CASE WHEN sf.pct < 0.05 THEN 1.0 ELSE 0.0 END) as niche_ratio
    FROM job_skills js
    JOIN skills s ON s.skill_id = js.skill_id
    JOIN skill_freq sf ON sf.skill_id = js.skill_id
    JOIN job_postings jp ON jp.job_id = js.job_id
    WHERE jp.data_tier = 1
    AND s.difficulty_relevant = true
    GROUP BY js.job_id
),

company_base AS (
    SELECT
        c.company_id,
        c.company_name,
        c.sector,
        COUNT(DISTINCT jp.job_id) as total_roles,
        MODE() WITHIN GROUP (ORDER BY jp.experience_level) as primary_level,
        ROUND(AVG(CASE WHEN jp.salary_max_annual IS NOT NULL THEN 1.0 ELSE 0.0 END) * 100, 1) as transparency_pct,
        AVG(CASE WHEN jp.salary_max_annual BETWEEN 50000 AND 500000 THEN jp.salary_max_annual END) as avg_max_salary,
        AVG(jsc.skill_count) as avg_skill_count,
        AVG(jnr.niche_ratio) as niche_skill_ratio
    FROM job_postings jp
    JOIN companies c ON c.company_id = jp.company_id
    LEFT JOIN job_skill_counts jsc ON jsc.job_id = jp.job_id
    LEFT JOIN job_niche_ratio jnr ON jnr.job_id = jp.job_id
    WHERE jp.data_tier = 1
    AND c.sector IS NOT NULL
    GROUP BY c.company_id, c.company_name, c.sector
    HAVING COUNT(DISTINCT jp.job_id) >= 5
),

scored AS (
    SELECT
        cb.*,
        sm.median_max as sector_median,
        ssa.avg_skills as sector_avg_skills,
        ROUND((100 - cb.transparency_pct)::numeric, 1) as opacity_score,
        CASE
            WHEN cb.avg_max_salary IS NOT NULL AND sm.median_max IS NOT NULL THEN
                CASE
                    WHEN cb.avg_max_salary >= sm.median_max THEN 0
                    ELSE LEAST(100, ROUND(((sm.median_max - cb.avg_max_salary) / sm.median_max * 100)::numeric, 1))
                END
            ELSE ROUND((10 + (100 - cb.transparency_pct) * 0.50)::numeric, 1)
        END as salary_below_score,
        CASE
            WHEN ssa.avg_skills IS NULL OR ssa.avg_skills = 0 OR cb.avg_skill_count IS NULL THEN 50
            ELSE LEAST(100, GREATEST(0, ROUND((cb.avg_skill_count / ssa.avg_skills * 50)::numeric, 1)))
        END as complexity_score,
        ROUND((cb.niche_skill_ratio * 100)::numeric, 1) as rarity_score
    FROM company_base cb
    LEFT JOIN sector_medians sm ON sm.sector = cb.sector AND sm.experience_level = cb.primary_level
    LEFT JOIN sector_skill_avg ssa ON ssa.sector = cb.sector AND ssa.experience_level = cb.primary_level
),

raw_scores AS (
    SELECT *,
        ROUND((
            rarity_score * 0.30 +
            complexity_score * 0.25 +
            salary_below_score * 0.30 +
            opacity_score * 0.15
        )::numeric, 2) as raw_difficulty
    FROM scored
),

normalized AS (
    SELECT *,
        MIN(raw_difficulty) OVER () as min_raw,
        MAX(raw_difficulty) OVER () as max_raw
    FROM raw_scores
)

SELECT
    company_name,
    sector,
    total_roles,
    primary_level,
    ROUND(((raw_difficulty - min_raw) / NULLIF(max_raw - min_raw, 0) * 100)::numeric, 1) as difficulty_score,
    raw_difficulty as raw_score,
    rarity_score,
    complexity_score,
    salary_below_score,
    opacity_score,
    ROUND(transparency_pct) as pct_transparent,
    ROUND(avg_max_salary) as avg_max_salary,
    ROUND(sector_median) as sector_median
FROM normalized;
