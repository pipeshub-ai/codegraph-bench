#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# Regenerate the fixtures and answer keys, then run the offline eval and the drift check.
#   PIPESHUB_PY, PY: from bench/env.sh (PipesHub commit PIPESHUB_REF)
set -euo pipefail
cd "$(dirname "$0")/.."
: "${PIPESHUB_VENV_PY:=$PY}"
export PIPESHUB_PY
stamp=$(date +%Y%m%d-%H%M%S)
python3 -m fixture_gen.generate --out outputs/fixtures --key gold --step 0
(cd src && "$PIPESHUB_VENV_PY" -m cg_eval.run --step 0 --fixtures ../outputs/fixtures --key ../gold \
    --tools --out "../reports/$stamp/neo4j")
(cd src && "$PIPESHUB_VENV_PY" -m cg_eval.run --step 0 --fixtures ../outputs/fixtures --key ../gold \
    --tools --search arango --out "../reports/$stamp/arango")
(cd src && "$PIPESHUB_VENV_PY" -m cg_eval.run --step 0 --fixtures ../outputs/fixtures --key ../gold \
    --connector gitlab --out "../reports/$stamp/gitlab")
(cd src && "$PIPESHUB_VENV_PY" -m cg_eval.drift --out "../reports/$stamp/drift")
echo "reports in reports/$stamp"
