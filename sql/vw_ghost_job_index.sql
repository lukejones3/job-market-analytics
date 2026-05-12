-- Ghost Job Index
-- Probability a job posting is no longer actively hiring.
--
-- METHODOLOGY (v2 — calibrated baseline):
--   Sector medians are now derived from HISTORICALLY EXPIRED jobs (natural cron
--   expiry only), not from the active job pool.  Active-job medians were circular
--   — you can't build a reference distribution from the same set you're scoring.
--
-- Reference filter:
--   • status = 'expired'
--   • posted_date available and > 2020-01-01
--   • last_seen_at > posted_date  (must have been seen after posting)
--   • days_to_close >= 3          (floors out Workday date-timezone artifacts)
--   • DATE(last_seen_at) != '2026-05-09'  (excludes 89K-job mass-cleanup spike)
--
-- Active scoring:
--   • current_days_open = CURRENT_DATE - posted_date
--   • ghost_probability = sigmoid((current_days_open / sector_median)^2) * 100
--
-- Sources:
--   Greenhouse, Lever, Ashby have reliable posted_date.
--   Workday posted_date is present but timezone-shifted (hence 3-day floor).
--   Adzuna excluded from ghost scoring (no reliable posted_date).

CREATE OR REPLACE VIEW vw_ghost_job_index AS

WITH reference_closures AS (
    -- Actual time-to-close for naturally expired jobs (calibration baseline)
    SELECT
        jp.job_id,
        c.sector,
        jp.ingestion_source,
        jp.posted_date,
        jp.last_seen_at::date                                    AS closed_date,
        (jp.last_seen_at::date - jp.posted_date)                 AS days_to_close_raw,
        LEAST((jp.last_seen_at::date - jp.posted_date), 365)     AS days_to_close_capped
    FROM job_postings jp
    JOIN companies c ON c.company_id = jp.company_id
    WHERE jp.status = 'expired'
      AND jp.posted_date IS NOT NULL
      AND jp.posted_date > '2020-01-01'
      AND jp.last_seen_at IS NOT NULL
      AND jp.last_seen_at::date > jp.posted_date
      AND (jp.last_seen_at::date - jp.posted_date) >= 3    -- exclude timezone artifact blips
      AND DATE(jp.last_seen_at) != '2026-05-09'            -- exclude 89K mass-cleanup spike
      AND c.sector IS NOT NULL
),

sector_medians AS (
    SELECT
        sector,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close_capped) AS median_days_to_close,
        COUNT(*)                                                            AS reference_job_count
    FROM reference_closures
    GROUP BY sector
    HAVING COUNT(*) >= 10
),

global_median AS (
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_to_close_capped) AS median_days_to_close
    FROM reference_closures
),

active_jobs AS (
    SELECT
        jp.job_id,
        c.sector,
        jp.ingestion_source,
        jp.posted_date,
        CURRENT_DATE - jp.posted_date                        AS days_open_raw,
        LEAST(CURRENT_DATE - jp.posted_date, 365)            AS days_open_capped
    FROM job_postings jp
    JOIN companies c ON c.company_id = jp.company_id
    WHERE jp.data_tier = 1
      AND jp.status = 'raw'
      AND jp.posted_date IS NOT NULL
      AND jp.posted_date > '2020-01-01'
      AND c.sector IS NOT NULL
),

job_scores AS (
    SELECT
        aj.job_id,
        aj.sector,
        aj.ingestion_source,
        aj.posted_date,
        aj.days_open_raw,
        aj.days_open_capped,
        COALESCE(sm.median_days_to_close, gm.median_days_to_close) AS sector_median,
        ROUND(
            (
                (1 - 1.0 / (1 + POWER(
                    aj.days_open_capped::float
                        / NULLIF(COALESCE(sm.median_days_to_close, gm.median_days_to_close), 0),
                2)))
                * 100
            )::numeric
        , 1) AS ghost_probability
    FROM active_jobs aj
    LEFT JOIN sector_medians sm ON sm.sector = aj.sector
    CROSS JOIN global_median gm
)

SELECT
    jp.job_id,
    c.company_name,
    c.sector,
    r.role_name,
    js.ingestion_source,
    js.posted_date,
    js.days_open_raw,
    js.days_open_capped,
    ROUND(js.sector_median::numeric)                             AS sector_median_days,
    js.ghost_probability,
    CASE
        WHEN js.ghost_probability >= 75 THEN 'high'
        WHEN js.ghost_probability >= 50 THEN 'medium'
        WHEN js.ghost_probability >= 25 THEN 'low'
        ELSE 'fresh'
    END                                                          AS ghost_tier
FROM job_scores js
JOIN job_postings jp ON jp.job_id = js.job_id
JOIN companies c     ON c.company_id = jp.company_id
JOIN roles r         ON r.role_id = jp.role_id;
