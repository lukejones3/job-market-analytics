#!/usr/bin/env bash
# run_discovery_test.sh
# Runs company_probe against a 100-company subset in dry-run mode.
# Use this to verify the async rewrite finds the same tenants as v1.

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

LOG_ASYNC=/tmp/ats_test_async.log
LOG_V1=/tmp/ats_test_v1.log

echo "=== Async rewrite: company_probe, 100 companies, dry-run ==="
PYTHONPATH=. python python/discover_ats_aggressive.py \
    --source company_probe \
    --limit 100 \
    --dry-run \
    2>&1 | tee "$LOG_ASYNC"

echo ""
echo "=== Async test done. Output: $LOG_ASYNC ==="
echo ""

# Optional: compare against v1 (uncomment if v1 isn't currently running)
# echo "=== v1 (sync): company_probe, 100 companies, dry-run ==="
# PYTHONPATH=. python python/discover_ats_aggressive_v1.py \
#     --source company_probe \
#     --limit 100 \
#     --dry-run \
#     2>&1 | tee "$LOG_V1"
#
# echo ""
# echo "Tenants found by async:"
# grep "DRY RUN" "$LOG_ASYNC" | awk '{print $5}' | sort > /tmp/async_tenants.txt
# echo "Tenants found by v1:"
# grep "DRY RUN" "$LOG_V1"   | awk '{print $5}' | sort > /tmp/v1_tenants.txt
# diff /tmp/v1_tenants.txt /tmp/async_tenants.txt || true
# echo ""
# echo "=== Comparison done ==="
