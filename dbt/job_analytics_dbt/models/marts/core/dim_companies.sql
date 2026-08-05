with discovered as (
  select
    lower(company_name) as company_name_key,
    max(sector) filter (where sector is not null) as sector
  from {{ source('job_analytics', 'discovered_companies') }}
  group by lower(company_name)
),

headcount as (
  select
    lower(company_name) as company_name_key,
    max(employee_count) as employee_count
  from {{ source('job_analytics', 'company_headcount') }}
  group by lower(company_name)
)

select
  c.company_id,
  c.company_name,
  c.company_type,
  coalesce(c.sector, dc.sector) as sector,
  coalesce(jp_counts.active_roles, 0) as active_roles,
  ch.employee_count,
  case
    when ch.employee_count is not null and ch.employee_count <= 5000
      then 'growth'
    when ch.employee_count is not null and ch.employee_count > 5000
      then 'enterprise'
    else null
  end as company_tier,
  case
    when ch.employee_count > 0 and coalesce(jp_counts.active_roles, 0) > 0
    then round(coalesce(jp_counts.active_roles, 0)::numeric / ch.employee_count * 100, 2)
    else null
  end as hiring_intensity_pct,
  case
    when ch.employee_count > 0 and coalesce(jp_counts.active_roles, 0) > 0
    then round(coalesce(jp_counts.active_roles, 0)::numeric / ch.employee_count * 1000, 2)
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
left join discovered dc
  on lower(c.company_name) = dc.company_name_key
left join headcount ch
  on lower(c.company_name) = ch.company_name_key
left join (
  select company_id, count(*) as active_roles
  from {{ source('job_analytics', 'job_postings') }}
  where status = 'raw' and data_tier = 1
  group by company_id
) jp_counts on jp_counts.company_id = c.company_id
