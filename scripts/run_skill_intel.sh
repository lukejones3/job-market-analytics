#!/usr/bin/env bash
set -euo pipefail

REPO="$HOME/github/job-market-analytics"
DB="${PGDATABASE:-job_analytics}"
RUN_ID="skillintel_$(date +%Y%m%d_%H%M%S)"

cd "$REPO"

echo "▶️  Promoting skill candidates (min_seen=3, min_conf=0.80)..."
psql "$DB" -v ON_ERROR_STOP=1 -c "SELECT * FROM promote_skill_candidates(3, 0.80);"

echo "▶️  Refreshing monthly snapshots (months_back=6), run_id=$RUN_ID ..."
psql "$DB" -v ON_ERROR_STOP=1 -c "SELECT refresh_skill_demand_monthly_snapshots('$RUN_ID', 6);"

echo "▶️  Top emerging skills (MoM, top 25):"
psql "$DB" -v ON_ERROR_STOP=1 -c "SELECT * FROM v_skill_emerging_mom LIMIT 25;"

echo "✅ Done. run_id=$RUN_ID"

