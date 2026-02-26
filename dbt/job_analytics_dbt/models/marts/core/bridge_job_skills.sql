select
  js.job_id,
  js.skill_id,
  s.skill_name,
  js.skill_priority,
  js.confidence,
  js.extraction_src
from {{ ref('stg_job_skills') }} js
join {{ ref('dim_skills') }} s using (skill_id)
