select
  c.company_id,
  c.company_name,
  c.company_type,
  dc.sector
from {{ ref('stg_companies') }} c
left join {{ source('job_analytics', 'discovered_companies') }} dc
  on lower(c.company_name) = lower(dc.company_name)
