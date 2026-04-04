-- mart_market_coverage.sql
-- Market-level signals using ALL tiers.
-- Used for market context reporting and trend analysis.

with jobs as (
  select
    fj.job_id,
    fj.ingested_at,
    fj.data_tier,
    fj.ingestion_source,
    fj.experience_level,
    fj.workplace_type,
    fj.salary_max_annual,
    fj.data_quality,
    date_trunc('month', fj.ingested_at) as month_start
  from {{ ref('fct_jobs') }} fj
),

aggregated as (
  select
    month_start,
    data_tier,
    ingestion_source,
    experience_level,
    workplace_type,
    count(distinct job_id)                                             as job_count,
    count(distinct job_id) filter (where salary_max_annual is not null) as jobs_with_salary,
    percentile_cont(0.5) within group (order by salary_max_annual)    as median_salary_raw,
    round(
      count(distinct job_id) filter (where salary_max_annual is not null)::numeric
      / nullif(count(distinct job_id), 0) * 100
    , 1)                                                               as salary_disclosure_pct
  from jobs
  where data_tier IN (1,2)
    and COALESCE(data_quality, 'ok') = 'ok'
  group by 1,2,3,4,5
)

select
  month_start,
  data_tier,
  ingestion_source,
  experience_level,
  workplace_type,
  job_count,
  jobs_with_salary,
  round(median_salary_raw::numeric, 0) as median_salary,
  salary_disclosure_pct
from aggregated
order by month_start desc, job_count desc
