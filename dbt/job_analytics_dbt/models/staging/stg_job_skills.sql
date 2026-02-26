select
  job_skills_id,
  job_id,
  skill_id,
  lower(skill_priority) as skill_priority,
  confidence,
  extraction_src
from {{ source('job_analytics','job_skills') }}
