#!/usr/bin/env bash
set -euo pipefail

OUTFILE="job_analytics_inspection.txt"
: > "$OUTFILE"

echo "Scanning key files only..."

# Only scan relevant files (skip heavy dirs)
grep -RInE \
  --exclude-dir=node_modules \
  --exclude-dir=venv \
  --exclude-dir=.git \
  --exclude-dir=__pycache__ \
  --include="*.py" \
  --include="*.sql" \
  --include="*.sh" \
  "job_id|desc_hash|ON CONFLICT|refresh_job_honesty|job_snapshots|greenhouse|workday|lever|ashby" . \
  >> "$OUTFILE" 2>&1

echo "Done. Output in $OUTFILE"
