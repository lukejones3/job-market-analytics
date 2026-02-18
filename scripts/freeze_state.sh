#!/usr/bin/env bash
set -e

TS=$(date +"%Y%m%d_%H%M%S")
BACKUP_DIR="backups/$TS"

echo "🧊 Creating freeze snapshot: $TS"

mkdir -p "$BACKUP_DIR"

# 1. Git snapshot
git add .
git commit -m "Freeze snapshot $TS" || echo "Nothing to commit"
git tag "freeze_$TS"

echo "✅ Git snapshot + tag created"

# 2. Full database dump
pg_dump job_analytics > "$BACKUP_DIR/job_analytics.sql"
gzip "$BACKUP_DIR/job_analytics.sql"

echo "✅ Database dump created"

# 3. Schema-only dump
pg_dump --schema-only job_analytics > "$BACKUP_DIR/schema.sql"

echo "✅ Schema snapshot created"

# 4. Capture state metrics
psql job_analytics <<EOF > "$BACKUP_DIR/state_metrics.txt"
SELECT 'jobs' AS metric, COUNT(*) FROM job_postings;
SELECT 'job_skills' AS metric, COUNT(*) FROM job_skills;
SELECT 'skills' AS metric, COUNT(*) FROM skills;
SELECT 'skill_aliases' AS metric, COUNT(*) FROM skill_aliases;
SELECT 'skill_candidates' AS metric, COUNT(*) FROM skill_candidates;

SELECT 'avg_skills_per_job' AS metric,
ROUND(AVG(skill_rows)::numeric, 2)
FROM (
  SELECT jp.job_id, COUNT(js.*) AS skill_rows
  FROM job_postings jp
  LEFT JOIN job_skills js ON js.job_id = jp.job_id
  GROUP BY jp.job_id
) t;
EOF

echo "✅ State metrics captured"

echo "🧊 Freeze complete: backups/$TS"


chmod +x scripts/freeze_state.sh
