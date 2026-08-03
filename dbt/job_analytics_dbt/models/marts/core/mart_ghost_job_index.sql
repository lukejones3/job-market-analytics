-- The operational view is the single implementation of ghost risk.  This mart
-- intentionally mirrors it so dbt consumers cannot drift to a second formula.
select * from public.vw_ghost_job_index
