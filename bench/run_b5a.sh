#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B5a: SWE-bench Lite file localization. Dev serially, then test in three workers split by
# repo, so each repo's embedding cache fills once. Waits while the machine is over 90% busy.
set -uo pipefail
cd "$(dirname "$0")/../src"
LOG=../reports/b5a; mkdir -p $LOG
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
wait_cpu; $PY -m cg_eval.swebench run --split dev > $LOG/dev.log 2>&1; echo "dev done"
wait_cpu; $PY -m cg_eval.swebench run --split test --repos django/django > $LOG/test-django.log 2>&1 &
wait_cpu; $PY -m cg_eval.swebench run --split test --repos sympy/sympy > $LOG/test-sympy.log 2>&1 &
wait_cpu; $PY -m cg_eval.swebench run --split test --skip-repos django/django sympy/sympy > $LOG/test-rest.log 2>&1 &
wait; echo "test done"
$PY -m cg_eval.swebench fuse > $LOG/fuse.log 2>&1; echo "fuse done"
