-- Ghost Job Index
-- Probability a job posting is no longer actively hiring
-- based on days open vs sector median days open
-- Uses posted_date from ATS (available for Greenhouse, Lever, Ashby)
-- Workday/Amazon/Eightfold excluded (no posted_date)
-- Sector medians capped at 365 days to exclude Lever outliers
-- Last updated: April 2026

CREATE OR REPLACE VIEW vw_ghost_job_index AS

WITH capped_ages AS (
    SELECT
        jp.job_id,
        c.sector,
        jp.ingestion_source,
        jp.posted_date,
        LEAST(CURRENT_DATE - jp.posted_date, 365) as days_open_capped,
        CURRENT_DATE - jp.posted_date as days_open_raw
    FROM job_postings jp
    JOIN companies c ON c.company_id = jp.company_id
    WHERE jp.data_tier = 1
    AND jp.status = 'raw'
    AND jp.posted_date IS NOT NULL
    AND jp.posted_date > '2020-01-01'
    AND c.sector IS NOT NULL
),

sector_medians AS (
    SELECT
        sector,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_open_capped) as median_days_open,
        COUNT(*) as sector_job_count
    FROM capped_ages
    GROUP BY sector
    HAVING COUNT(*) >= 10
),

global_median AS (
    SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY days_open_capped) as median_days_open
    FROM capped_ages
),

job_scores AS (
    SELECT
        ca.job_id,
        ca.sector,
        ca.ingestion_source,
        ca.posted_date,
        ca.days_open_raw,
        ca.days_open_capped,
        COALESCE(sm.median_days_open, gm.median_days_open) as sector_median,
        ROUND(
            (
                (1 - 1.0 / (1 + POWER(
                    ca.days_open_capped::float / NULLIF(COALESCE(sm.median_days_open, gm.median_days_open), 0),
                2)))
                * 100
            )::numeric
        , 1) as ghost_probability
    FROM capped_ages ca
    LEFT JOIN sector_medians sm ON sm.sector = ca.sector
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
    ROUND(js.sector_median::numeric) as sector_median_days,
    js.ghost_probability,
    CASE
        WHEN js.ghost_probability >= 75 THEN 'high'
        WHEN js.ghost_probability >= 50 THEN 'medium'
        WHEN js.ghost_probability >= 25 THEN 'low'
        ELSE 'fresh'
    END as ghost_tier
FROM job_scores js
JOIN job_postings jp ON jp.job_id = js.job_id
JOIN companies c ON c.company_id = jp.company_id
JOIN roles r ON r.role_id = jp.role_id;
