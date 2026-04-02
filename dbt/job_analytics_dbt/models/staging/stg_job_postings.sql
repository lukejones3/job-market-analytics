with src as (
  select *
  from {{ source('job_analytics','job_postings') }}
),

typed as (
  select
    job_id,
    source,
    ingested_at,
    date_found,
    company_id,
    role_id,
    location_id,
    experience_level,
    workplace_type,
    ingestion_source,
    coalesce(data_tier, case when ingestion_source = 'adzuna' then 2 else 1 end) as data_tier,

    -- legacy/raw salary fields (what you currently DO have)
    salary_min,
    salary_max,
    lower(nullif(trim(salary_period), '')) as salary_period,

    -- new canonical fields (might be null today)
    lower(nullif(trim(pay_period), '')) as pay_period,
    is_hourly,
    pay_min_raw,
    pay_max_raw,
    annualization_hours,
    salary_min_annual,
    salary_max_annual
  from src
),

canon as (
  select
    *,
    -- 1) pay_period fallback to salary_period
    coalesce(pay_period, salary_period) as pay_period_c,

    -- 2) is_hourly fallback derived from period
    coalesce(
      is_hourly,
      case when coalesce(pay_period, salary_period) = 'hour' then true
           when coalesce(pay_period, salary_period) = 'year' then false
           else null
      end
    ) as is_hourly_c,

    -- 3) raw pay fallback to legacy salary fields
    coalesce(pay_min_raw, salary_min) as pay_min_raw_c,
    coalesce(pay_max_raw, salary_max) as pay_max_raw_c,

    -- 4) annualization_hours default
    coalesce(annualization_hours, 2080) as annualization_hours_c
  from typed
),

final as (
  select
    job_id,
    source,
    ingested_at,
    date_found,
    company_id,
    role_id,
    location_id,
    experience_level,
    workplace_type,
    ingestion_source,
    data_tier,

    -- keep original fields for traceability
    salary_min as salary_min_original,
    salary_max as salary_max_original,

    -- canonical, now always populated when possible
    pay_period_c as pay_period,
    is_hourly_c as is_hourly,
    pay_min_raw_c as pay_min_raw,
    pay_max_raw_c as pay_max_raw,
    annualization_hours_c as annualization_hours,

    case
      when pay_min_raw_c is null then null
      when is_hourly_c = true then pay_min_raw_c * annualization_hours_c
      when is_hourly_c = false then pay_min_raw_c
      else null
    end as salary_min_annual,

    case
      when pay_max_raw_c is null then null
      when is_hourly_c = true then pay_max_raw_c * annualization_hours_c
      when is_hourly_c = false then pay_max_raw_c
      else null
    end as salary_max_annual

  from canon
)

select * from final
