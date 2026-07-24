# Lander Airflow

Airflow 3 orchestrates production with a dedicated PostgreSQL metadata database
and `LocalExecutor`. The API server binds only to `127.0.0.1:8080`; access it
through an SSH tunnel.

The nightly DAG serializes the nine source writers. Downstream mutation and
expiry cannot run unless every source and the ingest quality gate succeeds.
`lander_shadow_validation` is read-only and must pass before production DAGs
are enabled.
