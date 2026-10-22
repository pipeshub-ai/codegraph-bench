#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# Keep machine CPU under 90%: every retrieval shard but shard 0, and the Onyx ingest, pause above 88% busy and resume below 75%.
while ps -eo args -u "$USER" | grep -q "[c]b.py retrieve --shard\|[o]nyx_b5a.py ingest"; do
  busy=$(vmstat 5 2 | tail -1 | awk '{print 100-$15}')
  pids=$(ps -eo pid,args -u "$USER" | awk '/[p]ython -u cb.py retrieve --shard [1-9]\/|[o]nyx_b5a.py ingest/{print $1}')
  if [ "$busy" -ge 88 ]; then for p in $pids; do kill -STOP $p 2>/dev/null; done; echo "$(date +%T) cpu ${busy}%: paused"
  elif [ "$busy" -lt 75 ]; then for p in $pids; do kill -CONT $p 2>/dev/null; done; fi
  sleep 25
done
