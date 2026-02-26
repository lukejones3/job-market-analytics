BEGIN;

ALTER TABLE resume_scores
  ADD COLUMN IF NOT EXISTS plausibility_penalty int,
  ADD COLUMN IF NOT EXISTS confidence_score int,
  ADD COLUMN IF NOT EXISTS confidence_flags jsonb;


CREATE TABLE IF NOT EXISTS resume_market_skill_stats (
  resume_id      text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  run_id         text NOT NULL REFERENCES resume_runs(run_id) ON DELETE CASCADE,
  skill_id       text NOT NULL REFERENCES skills(skill_id),
  req_jobs       int  NOT NULL DEFAULT 0,
  pref_jobs      int  NOT NULL DEFAULT 0,
  total_jobs     int  NOT NULL DEFAULT 0,
  req_freq       numeric NOT NULL DEFAULT 0, -- 0..1
  pref_freq      numeric NOT NULL DEFAULT 0, -- 0..1
  demand_score   numeric NOT NULL DEFAULT 0, -- weighted demand
  rarity_score   numeric NOT NULL DEFAULT 0, -- rarity proxy
  roi_score      numeric NOT NULL DEFAULT 0, -- demand * rarity
  created_at     timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, run_id, skill_id)
);

CREATE INDEX IF NOT EXISTS idx_rms_run_roi  ON resume_market_skill_stats(run_id, roi_score DESC);
CREATE INDEX IF NOT EXISTS idx_rms_run_dem  ON resume_market_skill_stats(run_id, demand_score DESC);


CREATE TABLE IF NOT EXISTS resume_run_flags (
  resume_id   text NOT NULL REFERENCES resumes(resume_id) ON DELETE CASCADE,
  run_id      text NOT NULL REFERENCES resume_runs(run_id) ON DELETE CASCADE,
  flag_type   text NOT NULL,     -- plausibility/confidence
  flag        text NOT NULL,
  value       numeric,
  created_at  timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (resume_id, run_id, flag_type, flag)
);

COMMIT;
