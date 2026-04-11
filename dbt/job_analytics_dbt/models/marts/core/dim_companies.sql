select
  c.company_id,
  c.company_name,
  c.company_type,
  dc.sector,
  dc.active_roles,
  ch.employee_count,
  case
    when ch.employee_count is not null and ch.employee_count <= 5000
      then 'growth'
    when ch.employee_count is not null and ch.employee_count > 5000
      then 'enterprise'
    else null
  end as company_tier,
  case
    when ch.employee_count > 0 and ch.employee_count <= 5000
    then round(dc.active_roles::numeric / ch.employee_count * 100, 2)
    else null
  end as hiring_intensity_pct,
  case
    when ch.employee_count > 0 and ch.employee_count > 5000
    then round(dc.active_roles::numeric / ch.employee_count * 1000, 2)
    else null
  end as hiring_intensity_per_1k,
  case
    when ch.employee_count <= 5000
      then 'Roles per 100 employees (growth-stage)'
    when ch.employee_count > 5000
      then 'Roles per 1,000 employees (enterprise — global headcount)'
    else null
  end as intensity_label
from {{ ref('stg_companies') }} c
left join {{ source('job_analytics', 'discovered_companies') }} dc
  on lower(c.company_name) = lower(dc.company_name)
left join {{ source('job_analytics', 'company_headcount') }} ch
  on lower(c.company_name) = lower(ch.company_name)
