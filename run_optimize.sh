#!/bin/bash
set -u
cd /Users/jlarkin/dev/data-manager
mkdir -p logs
# wait for the all-history PIT build to finish
while pgrep -f "build-universe-pit --history" > /dev/null 2>&1; do sleep 15; done
echo "$(date +%T) PIT history done; starting optimize with backup" 
uv run data-manager optimize-db --backup "$HOME/.prime/agent/data_manager.pre-optimize.db" > logs/optimize.log 2>&1
echo "OPTIMIZE DONE $(date +%T)" >> logs/optimize.log
cat logs/optimize.log
