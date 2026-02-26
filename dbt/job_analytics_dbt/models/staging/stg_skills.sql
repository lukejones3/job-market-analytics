select
  skill_id,
  trim(skill_name) as skill_name,
  nullif(trim(skill_group), '') as skill_group
from {{ source('job_analytics','skills') }}
