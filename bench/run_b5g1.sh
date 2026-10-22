#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B5-G1 retry: add mention-weighted seeds to every Lite issue (graph rebuild, no embedding), score
# the held-out SWE-bench Verified issues that are not in Lite (graph, BM25 and dense), then fuse2.
set -uo pipefail
cd "$(dirname "$0")/../src"
LOG=../reports/b5a; mkdir -p $LOG
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
wait_cpu; $PY -m cg_eval.swebench run --split dev --no-dense > $LOG/g1-dev.log 2>&1
wait_cpu; $PY -m cg_eval.swebench run --split test --repos django/django --no-dense > $LOG/g1-test-django.log 2>&1 &
wait_cpu; $PY -m cg_eval.swebench run --split test --skip-repos django/django --no-dense > $LOG/g1-test-rest.log 2>&1 &
wait_cpu; $PY -m cg_eval.swebench run --split fresh --repos django/django > $LOG/g1-fresh-django.log 2>&1 &
wait_cpu; $PY -m cg_eval.swebench run --split fresh --skip-repos django/django > $LOG/g1-fresh-rest.log 2>&1 &
wait; echo "runs done"
$PY -m cg_eval.swebench fuse2 > $LOG/fuse2.log 2>&1; echo "fuse2 done"
