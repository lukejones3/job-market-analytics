#!/usr/bin/env bash
set -euo pipefail

# Where your dump file lives (change if yours differs)
DUMP_FILE="$HOME/github/job-market-analytics/data/job_dump.txt"

DELIM_START="===JOB START==="
DELIM_END="===JOB END==="

mkdir -p "$(dirname "$DUMP_FILE")"

CLIP="$(pbpaste)"

# Guard: empty clipboard = do nothing
if [[ -z "${CLIP//[[:space:]]/}" ]]; then
  osascript -e 'display notification "Clipboard is empty" with title "Job Dump"'
  exit 0
fi

# Normalize line endings
CLIP="${CLIP//$'\r\n'/$'\n'}"
CLIP="${CLIP//$'\r'/$'\n'}"

# Append in your exact block format
{
  echo ""
  echo "$DELIM_START"
  echo "$CLIP"
  echo "$DELIM_END"
} >> "$DUMP_FILE"

osascript -e 'display notification "Appended job to dump file" with title "Job Dump"'
