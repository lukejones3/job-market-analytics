select
  role_id,
  nullif(trim(role_name), '') as role_name,
  nullif(trim(role_archetype), '') as role_archetype,
  nullif(trim(role_domain), '') as role_domain
from {{ source('job_analytics','roles') }}
