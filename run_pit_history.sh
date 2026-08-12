#!/bin/bash
cd /Users/jlarkin/dev/data-manager
mkdir -p logs
uv run data-manager build-universe-pit --history > logs/pit_history.log 2>&1
echo DONE >> logs/pit_history.log
