select
  jp.job_id,
  jp.ingested_at,
  jp.date_found,
  jp.posted_date,
  jp.last_seen_at,
  jp.status,
  jp.source,
  jp.ingestion_source,
  jp.data_tier,
  jp.data_quality,
  jp.company_id,
  jp.role_id,
  jp.location_id,

  jp.experience_level,
  jp.workplace_type,

  jp.pay_period,
  jp.is_hourly,
  jp.pay_min_raw,
  jp.pay_max_raw,
  jp.annualization_hours,
  jp.salary_min_annual,
  jp.salary_max_annual,

  -- days open (for active jobs and expired)
  case
    when jp.status = 'raw' and jp.posted_date is not null
      then current_date - jp.posted_date
    when jp.status = 'expired' and jp.posted_date is not null
      then jp.last_seen_at::date - jp.posted_date
    else null
  end as days_open,

  -- lifecycle tracking
  (jp.last_seen_at::date - jp.ingested_at::date) as days_tracked,

  -- honesty score join
  jh.honesty_score,

  -- job_url for reference
  jp.job_url

from {{ ref('stg_job_postings') }} jp
left join {{ source('job_analytics','job_honesty_latest') }} jh
  on jh.job_id = jp.job_id
