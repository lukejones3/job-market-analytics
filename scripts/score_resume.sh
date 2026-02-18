#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
cd "$REPO"

FILE="${1:?Usage: scripts/score_resume.sh /path/to/resume.pdf [role] [location]}"
ROLE="${2:-}"
LOC="${3:-}"

python -u python/score_resume.py \
  --file "$FILE" \
  ${ROLE:+--role "$ROLE"} \
  ${LOC:+--location "$LOC"} \
  --months-back 6 \
  --top-jobs 500 \
  --workplace any \
  --level any

