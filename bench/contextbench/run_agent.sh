#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# ContextBench agent tier: run_agent.sh <arm> <N shards>; a guard stops the newest shard if memory passes 88%.
cd "$(dirname "$0")"
ARM=$1; N=${2:-4}
LOG=../../reports/logs
pids=()
for i in $(seq 0 $((N - 1))); do
  $PY -u cb.py agent --arm $ARM --shard $i/$N > $LOG/cb-agent-$ARM-$i.log 2>&1 &
  pids+=($!)
done
while [ -n "$(jobs -r)" ]; do
  used=$(free | awk '/Mem:/{printf "%d", 100-100*$7/$2}')
  if [ "$used" -ge 88 ]; then
    for ((k=${#pids[@]}-1; k>=0; k--)); do
      if kill -0 "${pids[k]}" 2>/dev/null; then echo "memory ${used}%: stopping $ARM shard $k"; kill "${pids[k]}"; break; fi
    done
    sleep 30
  fi
  sleep 15
done
echo "agent $ARM done"
