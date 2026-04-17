-- mart_sector_benchmarks.sql
-- Sector-level rollup of all key metrics.
-- One row per sector. Used for report generation and comparisons.

with sector_jobs as (
    select
        c.sector,
        jp.job_id,
        jp.status,
        jp.salary_max_annual,
        jp.experience_level,
        jp.workplace_type,
        jp.posted_date,
        jh.honesty_score,
        current_date - jp.posted_date as days_open
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','companies') }} c
        on c.company_id = jp.company_id
    left join {{ source('job_analytics','job_honesty_latest') }} jh
        on jh.job_id = jp.job_id
    where jp.data_tier = 1
      and c.sector is not null
),

ghost as (
    select
        c.sector,
        round(avg(g.ghost_probability)::numeric, 1) as avg_ghost_prob,
        round(
            count(*) filter (where g.ghost_tier in ('high','medium'))::numeric
            / nullif(count(*), 0) * 100, 1
        ) as pct_likely_ghost
    from {{ source('job_analytics','vw_ghost_job_index') }} g
    join {{ source('job_analytics','job_postings') }} jp on jp.job_id = g.job_id
    join {{ source('job_analytics','companies') }} c on c.company_id = jp.company_id
    where c.sector is not null
    group by c.sector
),

skill_premium as (
    select
        c.sector,
        round(avg(jp.salary_max_annual) filter (
            where jp.salary_max_annual between 50000 and 500000), 0) as sector_avg_salary
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','companies') }} c on c.company_id = jp.company_id
    where jp.data_tier = 1
      and c.sector is not null
    group by c.sector
)

select
    sj.sector,
    count(distinct sj.job_id) filter (where sj.status = 'raw')         as active_roles,
    count(distinct sj.job_id)                                           as total_roles,
    count(distinct case when sj.status = 'raw' then sj.job_id end)
        / nullif(count(distinct sj.job_id), 0) * 100                   as pct_still_active,

    round(avg(case when sj.salary_max_annual is not null
        then 1.0 else 0.0 end) * 100, 1)                               as transparency_pct,
    round(avg(sj.honesty_score), 1)                                     as avg_honesty_score,

    round(percentile_cont(0.5) within group (
        order by sj.salary_max_annual) filter (
        where sj.salary_max_annual between 50000 and 500000
        and sj.status = 'raw')::numeric, 0)                             as median_max_salary,
    sp.sector_avg_salary                                                as avg_max_salary,

    g.avg_ghost_prob,
    g.pct_likely_ghost,

    round(avg(sj.days_open) filter (
        where sj.status = 'raw'
        and sj.posted_date is not null), 0)                             as avg_days_open,

    count(distinct case when sj.workplace_type = 'remote'
        then sj.job_id end) * 100.0
        / nullif(count(distinct case when sj.status = 'raw'
        then sj.job_id end), 0)                                         as pct_remote

from sector_jobs sj
left join ghost g on g.sector = sj.sector
left join skill_premium sp on sp.sector = sj.sector
group by sj.sector, g.avg_ghost_prob, g.pct_likely_ghost, sp.sector_avg_salary
having count(distinct sj.job_id) >= 10
order by active_roles desc
