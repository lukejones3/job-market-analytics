#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
DUMP="${REPO}/data/job_dump.txt"
ARCHIVE_DIR="${REPO}/data/archives"
TS="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$ARCHIVE_DIR"

# 0) sanity: if dump file empty, bail
if [[ ! -s "$DUMP" ]]; then
  echo "Nothing to ingest: $DUMP is empty."
  exit 0
fi

cd "$REPO"

echo "🧾 Ingesting from: $DUMP"
python -u python/ingest_job_dump.py

echo "🧠 Enriching postings (rescan skills)..."
python -u python/enrich_job_postings.py --rescan-skills

# If we got here, ingest+enrich succeeded. Now archive and clear.
ARCHIVE_FILE="${ARCHIVE_DIR}/job_dump_${TS}.txt"
cp "$DUMP" "$ARCHIVE_FILE"
gzip -f "$ARCHIVE_FILE"

: > "$DUMP"

echo "✅ Batch complete."
echo "✅ Archived to: ${ARCHIVE_FILE}.gz"
echo "✅ Cleared: $DUMP"
