#!/bin/bash
LOGDIR=/opt/job-market-analytics/logs
TODAY=$(date +%F)
FOUND=0
echo "[$(date -u +'%F %T')] === Error scan ==="
for logfile in $LOGDIR/*.log; do
    name=$(basename $logfile)
    if [ -z "$(find $logfile -newermt "$TODAY 00:00" 2>/dev/null)" ]; then
        continue
    fi
    # Tighter match: only real errors (Traceback, explicit ERROR/FATAL, exceptions)
    errors=$(grep -E '(Traceback|^ERROR |FATAL|Exception|psycopg2\.errors|psql: error|OSError|ConnectionError|password authentication failed|UnboundLocalError|NameError|TypeError|ValueError|KeyError|AttributeError)' $logfile 2>/dev/null | grep -v 'ERROR=0' | grep -v 'errors: 0' | tail -5)
    if [ -n "$errors" ]; then
        echo ""
        echo "WARN $name:"
        echo "$errors"
        FOUND=1
    fi
done
if [ $FOUND -eq 0 ]; then
    echo "All clean"
fi
