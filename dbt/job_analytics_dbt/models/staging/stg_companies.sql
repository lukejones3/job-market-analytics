select
  company_id,
  nullif(trim(company_name), '') as company_name,
  nullif(trim(company_type), '') as company_type,
  nullif(trim(sector), '') as sector
from {{ source('job_analytics','companies') }}
