#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# B3 from a clean slate: this branch's image, an empty stack, the setup, the first sync,
# then the 7 fixture steps three times (each repeat ends with main back at step 0).
set -uo pipefail
cd "$(dirname "$0")"
mem_ok() { [ "$(free | awk '/Mem:/{printf "%d", 100*$3/$2}')" -lt 88 ]; }
until mem_ok; do echo "waiting: memory over 88%"; sleep 60; done
./stack.sh build > build3.log 2>&1 || { echo "build failed"; exit 1; }
echo "built"
git -C "$FIXTURE_DIR" push -q --force origin 'step-0^{commit}:refs/heads/main'
./stack.sh down -v > down.log 2>&1
rm -f pat.token admin.env state.json
./stack.sh up > up.log 2>&1 || { echo "up failed"; exit 1; }
echo "stack up"
$PY setup.py || exit 1
rm -f ../../reports/b3/steps.jsonl  # pre-fix results are kept in steps-before-g2-fix.jsonl
$PY live.py initial 2>/dev/null
$PY live.py run --repeats 3 2>/dev/null
echo "b3 done"
