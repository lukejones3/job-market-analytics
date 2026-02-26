select
  role_id,
  role_name,
  role_archetype,
  role_domain
from {{ ref('stg_roles') }}
