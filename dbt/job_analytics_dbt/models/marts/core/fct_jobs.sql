select
  jp.job_id,
  jp.ingested_at,
  jp.date_found,
  jp.source,
  jp.ingestion_source,
  jp.data_tier,

  jp.experience_level,
  jp.workplace_type,

  jp.pay_period,
  jp.is_hourly,
  jp.pay_min_raw,
  jp.pay_max_raw,
  jp.annualization_hours,

  jp.salary_min_annual,
  jp.salary_max_annual

from {{ ref('stg_job_postings') }} jp
