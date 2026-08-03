-- Ghost-risk model v3.
--
-- This is an explainable risk estimate, not a claim that a vacancy is fake.
-- It combines: (1) age relative to natural closures for the same source/sector,
-- (2) same-requisition disappear/reappear cycles, and (3) additional postings
-- in the same canonical opportunity.  All active published jobs remain in the
-- view; insufficient evidence is explicitly `unscored`.
CREATE OR REPLACE VIEW vw_ghost_job_index AS
WITH reference_closures AS (
    SELECT jp.job_id, c.sector, jp.ingestion_source,
           LEAST(jp.last_seen_at::date - jp.posted_date, 365) AS days_to_close
    FROM job_postings jp
    LEFT JOIN companies c ON c.company_id=jp.company_id
    WHERE jp.status='expired' AND jp.expired_reason='natural_cron'
      AND jp.posted_date > DATE '2020-01-01'
      AND jp.last_seen_at::date - jp.posted_date BETWEEN 3 AND 365
),
source_sector_baselines AS (
    SELECT sector, ingestion_source,
           percentile_cont(0.5) WITHIN GROUP (ORDER BY days_to_close) AS median_days,
           count(*) AS sample_size
    FROM reference_closures GROUP BY sector, ingestion_source HAVING count(*) >= 20
),
sector_baselines AS (
    SELECT sector, percentile_cont(0.5) WITHIN GROUP (ORDER BY days_to_close) AS median_days,
           count(*) AS sample_size
    FROM reference_closures GROUP BY sector HAVING count(*) >= 20
),
global_baseline AS (
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY days_to_close) AS median_days,
           count(*) AS sample_size FROM reference_closures
),
event_signals AS (
    SELECT job_id,
           count(*) FILTER (WHERE event_type='reappeared') AS reappearance_count,
           max(gap_days) FILTER (WHERE event_type='reappeared') AS longest_gap_days
    FROM job_posting_events GROUP BY job_id
),
canonical_signals AS (
    SELECT canonical_opportunity_id, count(DISTINCT job_id)-1 AS related_posting_count,
           count(DISTINCT ingestion_source) AS related_source_count
    FROM job_postings WHERE canonical_opportunity_id IS NOT NULL
    GROUP BY canonical_opportunity_id
),
active AS (
    SELECT jp.*, c.company_name, c.sector, r.role_name,
           GREATEST(CURRENT_DATE-jp.posted_date, 0) AS days_open_raw,
           LEAST(GREATEST(CURRENT_DATE-jp.posted_date, 0),365) AS days_open_capped,
           COALESCE(ssb.median_days,sb.median_days,gb.median_days) AS baseline_days,
           COALESCE(ssb.sample_size,sb.sample_size,gb.sample_size,0) AS baseline_sample_size,
           CASE WHEN ssb.median_days IS NOT NULL THEN 'source_sector'
                WHEN sb.median_days IS NOT NULL THEN 'sector'
                WHEN gb.median_days IS NOT NULL THEN 'global' END AS baseline_type,
           COALESCE(es.reappearance_count,0) AS reappearance_count,
           es.longest_gap_days, COALESCE(cs.related_posting_count,0) AS related_posting_count,
           COALESCE(cs.related_source_count,0) AS related_source_count
    FROM job_postings jp
    LEFT JOIN companies c ON c.company_id=jp.company_id
    LEFT JOIN roles r ON r.role_id=jp.role_id
    LEFT JOIN source_sector_baselines ssb ON ssb.sector=c.sector AND ssb.ingestion_source=jp.ingestion_source
    LEFT JOIN sector_baselines sb ON sb.sector=c.sector
    CROSS JOIN global_baseline gb
    LEFT JOIN event_signals es ON es.job_id=jp.job_id
    LEFT JOIN canonical_signals cs ON cs.canonical_opportunity_id=jp.canonical_opportunity_id
    WHERE jp.data_tier=1 AND jp.status='raw' AND jp.role_id IS NOT NULL
      AND COALESCE(jp.loc_country,'unknown') IN ('US','unknown')
),
components AS (
    SELECT active.*,
      CASE WHEN posted_date IS NULL OR posted_date <= DATE '2020-01-01' OR baseline_days IS NULL
           THEN NULL ELSE (1-1.0/(1+power(days_open_capped::float/NULLIF(baseline_days,0),2)))*100 END AS age_risk,
      LEAST(reappearance_count*25.0,100.0) AS reappearance_risk,
      LEAST(related_posting_count*12.5,100.0) AS repost_risk
    FROM active
), scored AS (
    SELECT components.*,
      CASE WHEN age_risk IS NULL AND reappearance_count=0 AND related_posting_count=0 THEN NULL
           ELSE round((COALESCE(age_risk,0)*0.65 + reappearance_risk*0.20 + repost_risk*0.15)::numeric,1)
      END AS ghost_probability
    FROM components
)
SELECT job_id, company_name, sector, role_name, ingestion_source, posted_date,
       days_open_raw, days_open_capped, round(baseline_days::numeric) AS sector_median_days,
       baseline_type, baseline_sample_size, round(age_risk::numeric,1) AS age_risk,
       reappearance_count, longest_gap_days, round(reappearance_risk::numeric,1) AS reappearance_risk,
       related_posting_count, related_source_count, round(repost_risk::numeric,1) AS repost_risk,
       ghost_probability,
       CASE WHEN ghost_probability IS NULL THEN 'unscored'
            WHEN ghost_probability >= 75 THEN 'high' WHEN ghost_probability >= 50 THEN 'medium'
            WHEN ghost_probability >= 25 THEN 'low' ELSE 'fresh' END AS ghost_tier,
       CASE WHEN ghost_probability IS NULL THEN 'low'
            WHEN baseline_sample_size >= 100 AND posted_date IS NOT NULL THEN 'high'
            WHEN baseline_sample_size >= 20 OR reappearance_count>0 OR related_posting_count>0 THEN 'medium'
            ELSE 'low' END AS score_confidence
FROM scored;
