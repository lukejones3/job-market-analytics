-- mart_company_scorecard.sql
-- One row per company. Powers recruiter outreach scorecards.
-- Combines hiring activity, difficulty, honesty, salary, ghost rate, top skills.

with active_jobs as (
    select
        jp.company_id,
        count(distinct jp.job_id)                                           as active_roles,
        round(avg(jp.salary_max_annual) filter (
            where jp.salary_max_annual between 50000 and 500000), 0)        as avg_max_salary,
        round(avg(case when jp.salary_max_annual is not null
            then 1.0 else 0.0 end) * 100, 1)                               as transparency_pct,
        round(avg(jh.honesty_score), 1)                                     as avg_honesty_score,
        mode() within group (order by jp.experience_level)                  as primary_level,
        mode() within group (order by jp.workplace_type)                    as primary_workplace
    from {{ source('job_analytics','job_postings') }} jp
    left join {{ source('job_analytics','job_honesty_latest') }} jh
        on jh.job_id = jp.job_id
    where jp.data_tier = 1
      and jp.status = 'raw'
    group by jp.company_id
),

ghost_summary as (
    select
        jp.company_id,
        round(avg(g.ghost_probability)::numeric, 1)                         as avg_ghost_probability,
        count(*) filter (where g.ghost_tier = 'high')                       as high_ghost_count,
        count(*)                                                             as scoreable_jobs
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','vw_ghost_job_index') }} g
        on g.job_id = jp.job_id
    where jp.data_tier = 1
      and jp.status = 'raw'
    group by jp.company_id
),

skill_counts as (
    select
        jp.company_id,
        s.skill_name,
        count(*) as skill_freq
    from {{ source('job_analytics','job_postings') }} jp
    join {{ source('job_analytics','job_skills') }} js on js.job_id = jp.job_id
    join {{ source('job_analytics','skills') }} s on s.skill_id = js.skill_id
    where jp.data_tier = 1
      and jp.status = 'raw'
      and js.skill_priority = 'required'
      and s.difficulty_relevant = true
    group by jp.company_id, s.skill_name
),

top_skills as (
    select
        company_id,
        string_agg(skill_name, ', ' order by skill_freq desc)              as top_skills
    from skill_counts
    group by company_id
),

difficulty as (
    select
        c.company_id,
        vhd.difficulty_score,
        vhd.rarity_score,
        vhd.complexity_score,
        vhd.salary_below_score
    from {{ source('job_analytics','vw_hiring_difficulty') }} vhd
    join {{ source('job_analytics','companies') }} c
        on lower(c.company_name) = lower(vhd.company_name)
)

select distinct on (dc.company_name)
    dc.company_id,
    dc.company_name,
    dc.sector,
    dc.company_tier,
    dc.employee_count,
    dc.hiring_intensity_pct,
    dc.hiring_intensity_per_1k,

    aj.active_roles,
    aj.avg_max_salary,
    aj.transparency_pct,
    aj.avg_honesty_score,
    aj.primary_level,
    aj.primary_workplace,

    d.difficulty_score,
    d.rarity_score,
    d.complexity_score,
    d.salary_below_score,

    gs.avg_ghost_probability,
    gs.high_ghost_count,
    gs.scoreable_jobs,
    round(
        gs.high_ghost_count::numeric / nullif(gs.scoreable_jobs, 0) * 100, 1
    ) as ghost_rate_pct,

    ts.top_skills

from {{ ref('dim_companies') }} dc
left join active_jobs aj on aj.company_id = dc.company_id
left join ghost_summary gs on gs.company_id = dc.company_id
left join top_skills ts on ts.company_id = dc.company_id
left join difficulty d on d.company_id = dc.company_id
where aj.active_roles >= 3
order by dc.company_name, aj.active_roles desc nulls last
