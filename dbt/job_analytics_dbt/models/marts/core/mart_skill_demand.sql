with jobs as (
  select
    job_id,
    experience_level,
    workplace_type,
    salary_max_annual,
    data_tier,
    ingestion_source
  from {{ ref('fct_jobs') }}
  where data_tier = 1  -- Tier 1 only: full descriptions with skill extraction
),
skill_jobs as (
  select
    b.skill_id,
    b.skill_name,
    j.experience_level,
    j.workplace_type,
    j.salary_max_annual,
    j.job_id
  from {{ ref('bridge_job_skills') }} b
  join jobs j using (job_id)
)
select
  experience_level,
  workplace_type,
  skill_name,
  count(distinct job_id) as jobs_with_skill,
  round(percentile_cont(0.5) within group (order by salary_max_annual)::numeric, 0) as median_salary_max_annual
from skill_jobs
where salary_max_annual is not null
group by 1,2,3
having count(distinct job_id) >= 5
order by jobs_with_skill desc, median_salary_max_annual desc
