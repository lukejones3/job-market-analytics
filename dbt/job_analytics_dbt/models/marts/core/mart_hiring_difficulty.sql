-- mart_hiring_difficulty.sql
-- Composite hiring difficulty score per company
-- Components: skill rarity (30%), complexity (25%), salary competitiveness (30%), opacity (15%)
-- Normalized 0-100 within dataset

with skill_freq as (
    select
        js.skill_id,
        count(distinct js.job_id)::float
            / (select count(*) from {{ source('job_analytics','job_postings') }}
               where data_tier = 1)                  as pct
    from {{ source('job_analytics','job_skills') }} js
    join {{ source('job_analytics','job_postings') }} jp
        on jp.job_id = js.job_id
    where jp.data_tier = 1
    group by js.skill_id
),

sector_medians as (
    select
        c.sector,
        jp.experience_level,
        percentile_cont(0.5) within group (
            order by jp.salary_max_annual)           as median_max
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','companies') }} c
        on c.company_id = jp.company_id
    where jp.data_tier = 1
      and jp.salary_max_annual between 50000 and 500000
      and jp.experience_level is not null
      and c.sector is not null
    group by c.sector, jp.experience_level
),

job_skill_counts as (
    select js.job_id, count(*) as skill_count
    from {{ source('job_analytics','job_skills') }} js
    join {{ source('job_analytics','skills') }} s
        on s.skill_id = js.skill_id
    where js.skill_priority = 'required'
      and s.difficulty_relevant = true
    group by js.job_id
),

job_niche_ratio as (
    select
        js.job_id,
        avg(case when sf.pct < 0.05 then 1.0 else 0.0 end) as niche_ratio
    from {{ source('job_analytics','job_skills') }} js
    join {{ source('job_analytics','skills') }} s
        on s.skill_id = js.skill_id
    join skill_freq sf on sf.skill_id = js.skill_id
    join {{ source('job_analytics','job_postings') }} jp
        on jp.job_id = js.job_id
    where jp.data_tier = 1
      and s.difficulty_relevant = true
    group by js.job_id
),

sector_skill_avg as (
    select
        c.sector,
        jp.experience_level,
        avg(sc.skill_count) as avg_skills
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','companies') }} c
        on c.company_id = jp.company_id
    join job_skill_counts sc on sc.job_id = jp.job_id
    where jp.data_tier = 1
      and jp.experience_level is not null
      and c.sector is not null
    group by c.sector, jp.experience_level
),

company_base as (
    select
        c.company_id,
        c.company_name,
        c.sector,
        count(distinct jp.job_id)                    as total_roles,
        mode() within group (
            order by jp.experience_level)            as primary_level,
        round(avg(case when jp.salary_max_annual is not null
            then 1.0 else 0.0 end) * 100, 1)        as transparency_pct,
        avg(case when jp.salary_max_annual between 50000 and 500000
            then jp.salary_max_annual end)           as avg_max_salary,
        avg(jsc.skill_count)                         as avg_skill_count,
        avg(jnr.niche_ratio)                         as niche_skill_ratio
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','companies') }} c
        on c.company_id = jp.company_id
    left join job_skill_counts jsc on jsc.job_id = jp.job_id
    left join job_niche_ratio jnr on jnr.job_id = jp.job_id
    where jp.data_tier = 1
      and c.sector is not null
    group by c.company_id, c.company_name, c.sector
    having count(distinct jp.job_id) >= 5
),

scored as (
    select
        cb.*,
        sm.median_max                                as sector_median,
        ssa.avg_skills                               as sector_avg_skills,
        round((100 - cb.transparency_pct)::numeric, 1) as opacity_score,
        case
            when cb.avg_max_salary is not null
             and sm.median_max is not null then
                case
                    when cb.avg_max_salary >= sm.median_max then 0
                    else least(100, round(
                        ((sm.median_max - cb.avg_max_salary)
                         / sm.median_max * 100)::numeric, 1))
                end
            else round((10 + (100 - cb.transparency_pct) * 0.50)::numeric, 1)
        end                                          as salary_below_score,
        case
            when ssa.avg_skills is null
              or ssa.avg_skills = 0
              or cb.avg_skill_count is null then 50
            else least(100, greatest(0, round(
                (cb.avg_skill_count / ssa.avg_skills * 50)::numeric, 1)))
        end                                          as complexity_score,
        round((cb.niche_skill_ratio * 100)::numeric, 1) as rarity_score
    from company_base cb
    left join sector_medians sm
        on sm.sector = cb.sector
       and sm.experience_level = cb.primary_level
    left join sector_skill_avg ssa
        on ssa.sector = cb.sector
       and ssa.experience_level = cb.primary_level
),

raw_scores as (
    select *,
        round((
            rarity_score * 0.30 +
            complexity_score * 0.25 +
            salary_below_score * 0.30 +
            opacity_score * 0.15
        )::numeric, 2)                               as raw_difficulty
    from scored
),

normalized as (
    select *,
        min(raw_difficulty) over ()                  as min_raw,
        max(raw_difficulty) over ()                  as max_raw
    from raw_scores
)

select
    company_id,
    company_name,
    sector,
    total_roles,
    primary_level,
    round(
        ((raw_difficulty - min_raw)
         / nullif(max_raw - min_raw, 0) * 100)::numeric
    , 1)                                             as difficulty_score,
    raw_difficulty                                   as raw_score,
    rarity_score,
    complexity_score,
    salary_below_score,
    opacity_score,
    round(transparency_pct)                          as pct_transparent,
    round(avg_max_salary)                            as avg_max_salary,
    round(sector_median)                             as sector_median
from normalized
