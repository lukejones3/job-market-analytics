#!/usr/bin/env bash
set -euo pipefail

# Repair service boundaries without killing active ingestion transactions.
config=/etc/postgresql/16/main/postgresql.conf
postgres_changed=0
if ! grep -Fqx "listen_addresses = '*'" "$config"; then
  sed -i "s|^listen_addresses = .*|listen_addresses = '*'|" "$config"
  postgres_changed=1
fi

if (( postgres_changed )); then
  /usr/bin/pg_ctlcluster 16 main reload
fi

# API code can be restarted independently. PostgreSQL and Airflow are never
# restarted here; deployment during a DAG run must not terminate their work.
systemctl restart jma-api
systemctl is-active --quiet postgresql jma-api airflow-api-server \
  airflow-scheduler airflow-triggerer airflow-dag-processor
