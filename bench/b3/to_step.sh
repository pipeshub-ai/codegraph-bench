#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# Fresh stack with this branch's image, the first sync, then steps 1..N and stop, to inspect step N.
set -uo pipefail
cd "$(dirname "$0")"
N=${1:-6}
./stack.sh build > build4.log 2>&1 || { echo "build failed"; exit 1; }
echo built
git -C "$FIXTURE_DIR" push -q --force origin 'step-0^{commit}:refs/heads/main'
./stack.sh down -v > down.log 2>&1
rm -f pat.token admin.env state.json
./stack.sh up > up.log 2>&1 || { echo "up failed"; exit 1; }
$PY setup.py || exit 1
rm -f ../../reports/b3/steps.jsonl
$PY live.py initial 2>/dev/null
for n in $(seq 1 "$N"); do $PY live.py step "$n" 2>/dev/null; done
echo "at step $N"
