#!/usr/bin/env bash
set -euo pipefail

REPO="${HOME}/github/job-market-analytics"
DUMP="${REPO}/data/job_dump.txt"
ARCHIVE_DIR="${REPO}/data/archives"
TS="$(date +%Y%m%d_%H%M%S)"

mkdir -p "$ARCHIVE_DIR"
cd "$REPO"

# 0) sanity
if [[ ! -s "$DUMP" ]]; then
  echo "Nothing to ingest: $DUMP is empty."
  exit 0
fi

echo "🧾 Ingesting from: $DUMP"
INGEST_OUT="$(python -u python/ingest_job_dump.py | tee /dev/stderr)"

# Extract inserted job ids like J0454 from the ingest output
INSERTED_IDS="$(printf "%s\n" "$INGEST_OUT" | grep -Eo 'J[0-9]{4,}' | sort -u | tr '\n' ' ')"

if [[ -z "${INSERTED_IDS// }" ]]; then
  echo "⚠️ No inserted job_ids detected from ingest output. Skipping enrich."
else
  echo "✅ Inserted job_ids: $INSERTED_IDS"

  # If your enrich script supports --job-ids, use it (recommended)
  if python -u python/enrich_job_postings.py --help 2>&1 | grep -q -- '--job-ids'; then
    echo "🧠 Enriching ONLY inserted job_ids..."
    python -u python/enrich_job_postings.py --apply --rescan-skills --job-ids $INSERTED_IDS
  else
    # Fallback: enrich only N most recent jobs (works immediately with your current code)
    N="$(printf "%s\n" $INSERTED_IDS | wc -w | tr -d ' ')"
    echo "🧠 Enriching most recent N=$N jobs (fallback)..."
    python -u python/enrich_job_postings.py --apply --rescan-skills --limit "$N"
  fi
fi

# archive and clear
ARCHIVE_FILE="${ARCHIVE_DIR}/job_dump_${TS}.txt"
cp "$DUMP" "$ARCHIVE_FILE"
gzip -f "$ARCHIVE_FILE"
: > "$DUMP"

echo "✅ Batch complete."
echo "✅ Archived to: ${ARCHIVE_FILE}.gz"
echo "✅ Cleared: $DUMP"
