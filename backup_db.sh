#!/bin/bash

cd ~/github/job-market-analytics || exit

pg_dump job_analytics > backups/job_analytics_$(date +%F).sql
