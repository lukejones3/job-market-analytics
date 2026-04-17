-- mart_honesty_scores.sql
-- Company-level honesty score aggregates from job_honesty_latest

select
    c.company_id,
    c.company_name,
    c.sector,
    count(distinct jp.job_id)                        as scored_jobs,
    round(avg(jh.honesty_score), 1)                  as avg_honesty_score,
    round(min(jh.honesty_score), 1)                  as min_honesty_score,
    round(max(jh.honesty_score), 1)                  as max_honesty_score,
    round(percentile_cont(0.5) within group (
        order by jh.honesty_score)::numeric, 1)      as median_honesty_score,
    round(avg(case when jp.salary_max_annual is not null
        then 1.0 else 0.0 end) * 100, 1)            as transparency_pct,
    count(*) filter (
        where jh.honesty_score >= 90)                as high_honesty_jobs,
    count(*) filter (
        where jh.honesty_score < 70)                 as low_honesty_jobs
from {{ source('job_analytics','job_honesty_latest') }} jh
join {{ source('job_analytics','job_postings') }} jp
    on jp.job_id = jh.job_id
join {{ source('job_analytics','companies') }} c
    on c.company_id = jp.company_id
where jp.data_tier = 1
  and jp.status = 'raw'
  and c.sector is not null
group by c.company_id, c.company_name, c.sector
having count(distinct jp.job_id) >= 3
order by avg_honesty_score desc
