with ranked as (
  select
    job_skills_id,
    job_id,
    skill_id,
    lower(skill_priority) as skill_priority,
    confidence,
    extraction_src,
    row_number() over (
      partition by job_id, skill_id
      order by confidence desc nulls last, job_skills_id
    ) as row_rank
  from {{ source('job_analytics','job_skills') }}
)

select
  job_skills_id,
  job_id,
  skill_id,
  skill_priority,
  confidence,
  extraction_src
from ranked
where row_rank = 1
