#!/bin/bash
cd /opt/job-market-analytics
set -a
source .env
set +a
exec .venv/bin/python -m uvicorn python.api:app --host 0.0.0.0 --port 8000 --workers 2
