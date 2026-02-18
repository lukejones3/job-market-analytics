#!/bin/bash
# @raycast.schemaVersion 1
# @raycast.title Capture LinkedIn Job
# @raycast.mode silent
# @raycast.packageName Job Tools

# @raycast.icon 📋
# @raycast.description Paste clipboard into job_dump.txt with delimiters

FILE="$HOME/github/job-market-analytics/data/job_dump.txt"

CLIP=$(pbpaste)

if [ -z "$CLIP" ]; then
  echo "Clipboard empty"
  exit 1
fi

{
  echo "===JOB START==="
  echo "$CLIP"
  echo "===JOB END==="
  echo ""
} >> "$FILE"

echo "Job appended to job_dump.txt"
