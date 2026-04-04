-- mart_salary_benchmarks.sql
-- Uses BOTH tiers for broader salary signal.
-- Tier 1 = full enrichment, Tier 2 = Adzuna structured salary.

with jobs as (
  select
    fj.job_id,
    fj.ingested_at,
    fj.data_tier,
    fj.ingestion_source,
    fj.experience_level,
    fj.workplace_type,
    fj.salary_min_annual,
    fj.salary_max_annual,
    fj.pay_period,
    jp.location_id
  from {{ ref('fct_jobs') }} fj
  left join {{ source('job_analytics','job_postings') }} jp
    on jp.job_id = fj.job_id
  where fj.salary_max_annual is not null
    and fj.salary_max_annual between 30000 and 500000
    and COALESCE(fj.data_quality, 'ok') = 'ok'
),

with_location as (
  select
    j.*,
    l.state
  from jobs j
  left join {{ source('job_analytics','locations') }} l
    on l.location_id = j.location_id
)

select
  data_tier,
  ingestion_source,
  experience_level,
  workplace_type,
  state,
  count(distinct job_id)                                                               as job_count,
  round(avg(salary_min_annual)::numeric, 0)                                            as avg_salary_min,
  round(avg(salary_max_annual)::numeric, 0)                                            as avg_salary_max,
  round(percentile_cont(0.25) within group (order by salary_max_annual)::numeric, 0)  as p25_salary,
  round(percentile_cont(0.50) within group (order by salary_max_annual)::numeric, 0)  as median_salary,
  round(percentile_cont(0.75) within group (order by salary_max_annual)::numeric, 0)  as p75_salary
from with_location
group by 1,2,3,4,5
having count(distinct job_id) >= 3
order by data_tier, job_count desc
