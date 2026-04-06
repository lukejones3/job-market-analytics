select
  c.company_id,
  c.company_name,
  c.company_type,
  dc.sector,
  dc.active_roles,
  ch.employee_count,
  case
    when ch.employee_count > 0
    then round(dc.active_roles::numeric / ch.employee_count * 100, 2)
    else null
  end as hiring_intensity_pct
from {{ ref('stg_companies') }} c
left join {{ source('job_analytics', 'discovered_companies') }} dc
  on lower(c.company_name) = lower(dc.company_name)
left join {{ source('job_analytics', 'company_headcount') }} ch
  on lower(c.company_name) = lower(ch.company_name)
