#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B5b: llm_1 localizes SWE-bench Lite issues with and without PipesHub's code graph.
# usage: run_b5b.sh dev|test [workers per arm]
set -uo pipefail
cd "$(dirname "$0")/../src"
SPLIT=${1:-dev}; N=${2:-4}
LOG=../reports/b5b/logs; mkdir -p $LOG
while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done
for arm in search graph; do
  for i in $(seq 0 $((N - 1))); do
    $PY -m cg_eval.agentloc run --split $SPLIT --arm $arm --shard $i/$N > $LOG/$SPLIT-$arm-$i.log 2>&1 &
  done
done
wait
$PY -m cg_eval.agentloc score > $LOG/score-$SPLIT.log 2>&1
echo "$SPLIT done"
