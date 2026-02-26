select
  company_id,
  company_name,
  company_type
from {{ ref('stg_companies') }}
