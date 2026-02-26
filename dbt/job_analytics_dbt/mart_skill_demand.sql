select
  experience_level,
  workplace_type,
  skill_id,
  skill_name,
  count(distinct job_id) as jobs_with_skill,
  round(percentile_cont(0.5) within group (order by salary_max_annual)::numeric, 0) as median_salary_max_annual
from skill_jobs
where salary_max_annual is not null
group by 1,2,3,4
having count(distinct job_id) >= 5
order by jobs_with_skill desc, median_salary_max_annual desc;
