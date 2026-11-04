#!/usr/bin/env bash
# B7 v2: rerun the dump arm (export now carries the Jira fields), 2 repeats, CPU and memory under 85%.
cd "$(dirname "$0")/b7"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"   # PY, MODELS_JSON, PIPESHUB_SRC
gate() { while :; do c=$(vmstat 3 2 | tail -1 | awk '{print 100-$15}'); m=$(free | awk '/Mem/{printf "%d", $3/$2*100}');
  [ "$c" -lt 85 ] && [ "$m" -lt 85 ] && return; echo "$(date +%T) waiting: cpu $c% mem $m%"; sleep 60; done; }
for r in 1 2; do gate; $PY -u ask_cc.py dump --questions questions_v2.jsonl --out b7v2 --repeat $r --jobs 3 >> ../../reports/logs/b7v2-cc-dump.log 2>&1; done
echo "$(date +%T) dump done"
