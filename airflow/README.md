# Lander Airflow

Airflow 3 orchestrates production with a dedicated PostgreSQL metadata database
and `LocalExecutor`. The API server binds only to `127.0.0.1:8080`; access it
through an SSH tunnel.

The nightly DAG runs 12 source writers with bounded concurrency. Downstream
mutation and expiry cannot run unless every source and the ingest quality gate succeeds.
`lander_shadow_validation` is read-only and must pass before production DAGs
are enabled. `lander_ats_discovery` runs weekly to discover, validate, and
activate new ATS tenants; `lander_ats_discovery_daily` handles broad-domain
discovery and stale-candidate recovery. Airflow is the sole checked-in
production scheduler.
