select
  skill_id,
  skill_name,
  skill_group
from {{ ref('stg_skills') }}
