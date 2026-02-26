BEGIN;

-- ============================
-- 0) Core tables
-- ============================

CREATE TABLE IF NOT EXISTS resumes (
  resume_id      text PRIMARY KEY,
  user_id        text,
  uploaded_at    timestamptz NOT NULL DEFAULT now(),
  resume_text    text,
  resume_hash    text,
  source         text,      -- pdf/docx/txt
  parse_version  text,
  status         text NOT NULL DEFAULT 'uploaded', -- uploaded/parsed/scored/failed
  error          text,
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_resumes_user     ON resumes(user_id);
CREATE INDEX IF NOT EXISTS idx_resumes_hash     ON resumes(resume_hash);
CREATE INDEX IF NOT EXISTS idx_resumes_status   ON resumes(status);


CREATE TABLE IF NOT EXISTS resume_entities (
  resume_id     text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  entity_type   text NOT NULL,  -- company/title/school/cert/etc
  entity_text   text NOT NULL,
  confidence    numeric,
  created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_resume_entities_resume ON resume_entities(resume_id);
CREATE INDEX IF NOT EXISTS idx_resume_entities_type   ON resume_entities(entity_type);


CREATE TABLE IF NOT EXISTS resume_targets (
  resume_id               text PRIMARY KEY REFERENCES resumes(resume_id) ON DELETE CASCADE,
  target_role_id          text REFERENCES roles(role_id),
  target_location_id      text REFERENCES locations(location_id),
  target_workplace_type   text, -- remote/hybrid/onsite/any
  target_experience_level text, -- entry/associate/mid/senior/any
  created_at              timestamptz NOT NULL DEFAULT now(),
  updated_at              timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_resume_targets_role     ON resume_targets(target_role_id);
CREATE INDEX IF NOT EXISTS idx_resume_targets_location ON resume_targets(target_location_id);


CREATE TABLE IF NOT EXISTS resume_skills (
  resume_id      text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  skill_id       text NOT NULL REFERENCES skills(skill_id),
  evidence_text  text,
  confidence     numeric NOT NULL DEFAULT 0.80, -- 0..1
  source         text NOT NULL DEFAULT 'alias',  -- alias/section/heuristic/llm
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_resume_skills_resume ON resume_skills(resume_id);
CREATE INDEX IF NOT EXISTS idx_resume_skills_skill  ON resume_skills(skill_id);


-- ============================
-- 1) Scoring runs + outputs
-- ============================

CREATE TABLE IF NOT EXISTS resume_runs (
  run_id        text PRIMARY KEY,
  resume_id     text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  started_at    timestamptz NOT NULL DEFAULT now(),
  finished_at   timestamptz,
  status        text NOT NULL DEFAULT 'running', -- running/success/failed
  params        jsonb NOT NULL DEFAULT '{}'::jsonb,
  error         text
);

CREATE INDEX IF NOT EXISTS idx_resume_runs_resume   ON resume_runs(resume_id);
CREATE INDEX IF NOT EXISTS idx_resume_runs_started  ON resume_runs(started_at);


CREATE TABLE IF NOT EXISTS resume_scores (
  resume_id           text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  run_id              text NOT NULL REFERENCES resume_runs(run_id) ON DELETE CASCADE,
  market_fit_score    int  NOT NULL, -- 0-100
  market_percentile   numeric(6,2),
  salary_est_min      numeric,
  salary_est_max      numeric,
  honesty_match_score int,
  matched_jobs_count  int NOT NULL,
  top_jobs_considered int NOT NULL,
  created_at          timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_resume_scores_run   ON resume_scores(run_id);
CREATE INDEX IF NOT EXISTS idx_resume_scores_fit   ON resume_scores(market_fit_score);


CREATE TABLE IF NOT EXISTS resume_skill_gaps (
  resume_id     text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  run_id        text NOT NULL REFERENCES resume_runs(run_id) ON DELETE CASCADE,
  skill_id      text NOT NULL REFERENCES skills(skill_id),
  gap_type      text NOT NULL, -- missing_required/missing_preferred/emerging/low_signal
  impact_score  numeric NOT NULL,
  req_freq      numeric,
  pref_freq     numeric,
  why           text,
  created_at    timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, run_id, skill_id, gap_type)
);

CREATE INDEX IF NOT EXISTS idx_resume_gaps_run     ON resume_skill_gaps(run_id);
CREATE INDEX IF NOT EXISTS idx_resume_gaps_impact  ON resume_skill_gaps(impact_score DESC);


CREATE TABLE IF NOT EXISTS resume_job_matches (
  resume_id        text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  run_id           text NOT NULL REFERENCES resume_runs(run_id) ON DELETE CASCADE,
  job_id           text NOT NULL REFERENCES job_postings(job_id) ON DELETE CASCADE,
  match_score      numeric NOT NULL,
  honesty_score    int,
  salary_min       numeric,
  salary_max       numeric,
  workplace_type   text,
  experience_level text,
  created_at       timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, run_id, job_id)
);

CREATE INDEX IF NOT EXISTS idx_resume_matches_run   ON resume_job_matches(run_id);
CREATE INDEX IF NOT EXISTS idx_resume_matches_score ON resume_job_matches(match_score DESC);


-- ============================
-- 2) Views
-- ============================

CREATE OR REPLACE VIEW resume_scores_latest AS
SELECT DISTINCT ON (rs.resume_id)
  rs.resume_id,
  rs.run_id,
  rs.market_fit_score,
  rs.market_percentile,
  rs.salary_est_min,
  rs.salary_est_max,
  rs.honesty_match_score,
  rs.matched_jobs_count,
  rs.top_jobs_considered,
  rs.created_at
FROM resume_scores rs
ORDER BY rs.resume_id, rs.created_at DESC;


CREATE OR REPLACE VIEW resume_dashboard_latest AS
SELECT
  r.resume_id,
  r.user_id,
  r.uploaded_at,
  r.source,
  r.status,
  rt.target_role_id,
  rr.role_name AS target_role_name,
  rt.target_location_id,
  COALESCE(NULLIF(l.location,''), NULLIF(l.state,''), rt.target_location_id) AS target_location_name,
  rt.target_workplace_type,
  rt.target_experience_level,
  sl.market_fit_score,
  sl.market_percentile,
  sl.salary_est_min,
  sl.salary_est_max,
  sl.honesty_match_score,
  sl.matched_jobs_count,
  sl.top_jobs_considered,
  sl.created_at AS scored_at
FROM resumes r
LEFT JOIN resume_targets rt ON rt.resume_id = r.resume_id
LEFT JOIN roles rr ON rr.role_id = rt.target_role_id
LEFT JOIN locations l ON l.location_id = rt.target_location_id
LEFT JOIN resume_scores_latest sl ON sl.resume_id = r.resume_id;

COMMIT;
