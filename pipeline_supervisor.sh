#!/bin/bash
# Chains the FMP stages: waits for prices to finish, then fundamentals, then ratios.
cd /Users/jlarkin/dev/data-manager
echo "[supervisor] waiting for prices (PIDs: $1 $2)..." >> pipeline.log
while kill -0 $1 2>/dev/null || kill -0 $2 2>/dev/null; do
  sleep 30
done
echo "[supervisor] prices done - starting fundamentals $(date +%H:%M:%S)" >> pipeline.log
nohup uv run data-manager update-fundamentals --all > fundamentals.log 2>&1 &
FPID=$!
while kill -0 $FPID 2>/dev/null; do sleep 30; done
echo "[supervisor] fundamentals done - starting ratios $(date +%H:%M:%S)" >> pipeline.log
nohup uv run data-manager update-ratios --all > ratios.log 2>&1 &
RPID=$!
while kill -0 $RPID 2>/dev/null; do sleep 30; done
echo "[supervisor] RATIOS DONE - pipeline complete $(date +%H:%M:%S)" >> pipeline.log
touch PIPELINE_DONE
uv run data-manager status >> pipeline.log 2>&1
