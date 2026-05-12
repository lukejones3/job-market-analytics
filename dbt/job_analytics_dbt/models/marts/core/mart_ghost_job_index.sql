-- mart_ghost_job_index.sql
-- Ghost job probability for all active Tier 1 jobs with posted_date.
-- Sigmoid function: 1 - 1/(1 + (days_open_capped/sector_median)^2)
--
-- v2: sector_medians now derived from EXPIRED jobs (calibrated baseline),
-- not from the active job pool (which was circular).
-- Excludes 2026-05-09 mass-cleanup spike (89K jobs) and days_to_close < 3.

with reference_closures as (
    -- Actual time-to-close from naturally expired jobs
    select
        jp.job_id,
        c.sector,
        jp.ingestion_source,
        jp.posted_date,
        jp.last_seen_at::date                                as closed_date,
        least(
            (jp.last_seen_at::date - jp.posted_date),
            365
        )                                                    as days_to_close_capped
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','companies') }} c
        on c.company_id = jp.company_id
    where jp.status = 'expired'
      and jp.posted_date is not null
      and jp.posted_date > '2020-01-01'
      and jp.last_seen_at is not null
      and jp.last_seen_at::date > jp.posted_date
      and (jp.last_seen_at::date - jp.posted_date) >= 3     -- floor out timezone artifacts
      and date(jp.last_seen_at) != '2026-05-09'             -- exclude 89K mass-cleanup spike
      and c.sector is not null
),

sector_medians as (
    select
        sector,
        percentile_cont(0.5) within group (
            order by days_to_close_capped)                   as median_days_to_close,
        count(*)                                             as reference_job_count
    from reference_closures
    group by sector
    having count(*) >= 10
),

global_median as (
    select percentile_cont(0.5) within group (
        order by days_to_close_capped)                       as median_days_to_close
    from reference_closures
),

capped_ages as (
    select
        jp.job_id,
        c.sector,
        jp.ingestion_source,
        jp.posted_date,
        least(current_date - jp.posted_date, 365)           as days_open_capped,
        current_date - jp.posted_date                        as days_open_raw
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','companies') }} c
        on c.company_id = jp.company_id
    where jp.data_tier = 1
      and jp.status = 'raw'
      and jp.posted_date is not null
      and jp.posted_date > '2020-01-01'
      and c.sector is not null
),

scored as (
    select
        ca.job_id,
        ca.sector,
        ca.ingestion_source,
        ca.posted_date,
        ca.days_open_raw,
        ca.days_open_capped,
        coalesce(sm.median_days_to_close, gm.median_days_to_close) as sector_median,
        round(
            (
                (1 - 1.0 / (1 + power(
                    ca.days_open_capped::float
                    / nullif(coalesce(sm.median_days_to_close,
                             gm.median_days_to_close), 0),
                2)))
                * 100
            )::numeric
        , 1) as ghost_probability
    from capped_ages ca
    left join sector_medians sm on sm.sector = ca.sector
    cross join global_median gm
)

select
    sc.job_id,
    c.company_name,
    c.sector,
    r.role_name,
    sc.ingestion_source,
    sc.posted_date,
    sc.days_open_raw,
    sc.days_open_capped,
    round(sc.sector_median::numeric)                         as sector_median_days,
    sc.ghost_probability,
    case
        when sc.ghost_probability >= 75 then 'high'
        when sc.ghost_probability >= 50 then 'medium'
        when sc.ghost_probability >= 25 then 'low'
        else 'fresh'
    end                                                      as ghost_tier
from scored sc
join {{ source('job_analytics','job_postings') }} jp
    on jp.job_id = sc.job_id
join {{ source('job_analytics','companies') }} c
    on c.company_id = jp.company_id
join {{ source('job_analytics','roles') }} r
    on r.role_id = jp.role_id
