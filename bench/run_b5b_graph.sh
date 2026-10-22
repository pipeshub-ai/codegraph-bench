#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B5b graph arm only, rerun with the harness recording the build as finished (no stale-graph warning).
set -uo pipefail
cd "$(dirname "$0")/../src"
N=${1:-6}
LOG=../reports/b5b/logs; mkdir -p $LOG
while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 85 ]; do sleep 30; done
for i in $(seq 0 $((N - 1))); do
  $PY -m cg_eval.agentloc run --split test --arm graph --shard $i/$N > $LOG/test-graph2-$i.log 2>&1 &
done
wait
$PY -m cg_eval.agentloc score > $LOG/score-test-graph2.log 2>&1
echo "b5b graph rerun done"
