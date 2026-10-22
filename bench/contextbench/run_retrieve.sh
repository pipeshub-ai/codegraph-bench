#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# ContextBench retrieval tier in N shards; a guard stops the newest shard if memory passes 88%.
cd "$(dirname "$0")"
N=${1:-6}
LOG=../../reports/logs
pids=()
for i in $(seq 0 $((N - 1))); do
  $PY -u cb.py retrieve --shard $i/$N > $LOG/cb-retrieve-$i.log 2>&1 &
  pids+=($!)
done
while kill -0 "${pids[@]}" 2>/dev/null || [ -n "$(jobs -r)" ]; do
  used=$(free | awk '/Mem:/{printf "%d", 100-100*$7/$2}')
  if [ "$used" -ge 88 ]; then
    for ((k=${#pids[@]}-1; k>=0; k--)); do
      if kill -0 "${pids[k]}" 2>/dev/null; then echo "memory ${used}%: stopping shard $k"; kill "${pids[k]}"; break; fi
    done
  fi
  [ -z "$(jobs -r)" ] && break
  sleep 15
done
echo "retrieve done"
