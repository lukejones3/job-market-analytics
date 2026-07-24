#!/usr/bin/env bash
set -euo pipefail
root=/opt/job-market-analytics
backup_dir="$root/backups/airflow"
stamp="$(date -u +%F)"
temporary="$backup_dir/job_analytics_${stamp}.dump.tmp"
final="$backup_dir/job_analytics_${stamp}.dump"
install -d -m 0770 "$backup_dir"
rm -f "$temporary"
pg_dump --format=custom --file="$temporary"
pg_restore --list "$temporary" >/dev/null
chmod 0660 "$temporary"
mv -f "$temporary" "$final"
find "$backup_dir" -type f -name 'job_analytics_*.dump' -mtime +7 -delete
echo "Verified backup: $final"
