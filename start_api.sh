#!/bin/bash
cd /opt/job-market-analytics
set -a
source .env
set +a
exec .venv/bin/python -m uvicorn python.api:app --host "${LANDER_API_HOST:-127.0.0.1}" --port 8000 --workers 1 --no-server-header
