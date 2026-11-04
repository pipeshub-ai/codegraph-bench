#!/usr/bin/env bash
# B7 v2: the rewritten (user-style) questions through six arms, then two-judge grading.
#   Claude Code (Sonnet, one prompt) x {plain, graphify, pipeshub-code, pipeshub, dump}: 2 repeats
#   PipesHub's own agent (GPT-5.6) x {code-only, with-company} on the same instance: 1 repeat
# Each step waits until CPU and memory are both under 85%.
set -uo pipefail
cd "$(dirname "$0")/b7"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"   # PY, MODELS_JSON, PIPESHUB_SRC
Q=${Q:-questions_v2.jsonl} OUT=${OUT:-b7v2}
gate() { while :; do c=$(vmstat 3 2 | tail -1 | awk '{print 100-$15}'); m=$(free | awk '/Mem/{printf "%d", $3/$2*100}');
  [ "$c" -lt 85 ] && [ "$m" -lt 85 ] && return; echo "$(date +%T) waiting: cpu $c% mem $m%"; sleep 60; done; }
L=../../reports/logs; mkdir -p $L
for r in 1 2; do
  for a in ${CC_ARMS:-plain}; do   # as measured: plain graphify pipeshub-code pipeshub dump (the post shows plain)
    gate; $PY -u ask_cc.py $a --questions $Q --out $OUT --repeat $r --jobs 3 >> $L/$OUT-cc-$a.log 2>&1 &
  done
  wait; echo "$(date +%T) cc repeat $r done"
done
for p in with-company code-only; do gate; $PY -u ask.py $p --questions $Q --out $OUT --jobs 3 >> $L/$OUT-agent-$p.log 2>&1 & done
wait; echo "$(date +%T) agent done"
$PY -u grade_v2.py --questions $Q --out $OUT > $L/$OUT-grade.log 2>&1; echo "$(date +%T) graded rc=$?"
