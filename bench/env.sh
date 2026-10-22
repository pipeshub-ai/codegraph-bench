# Shared settings for every benchmark script. Sourced, not run.
# Override any of these in config.env at the repo root (see config.env.example) or in the environment.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
[ -f "$ROOT/config.env" ] && . "$ROOT/config.env"
export WORK=${WORK:-$ROOT/work}                                   # clones, tools, scratch
export PIPESHUB_REF=${PIPESHUB_REF:-6c6e29ab5}                    # PipesHub commit under test; bench/run.sh sets it per benchmark (pins.toml)
export PIPESHUB_SRC=${PIPESHUB_SRC:-$WORK/pipeshub@$PIPESHUB_REF}  # its worktree (bootstrap.sh pipeshub)
export PIPESHUB_PY=${PIPESHUB_PY:-$PIPESHUB_SRC/backend/python}  # its backend, imported by cg_eval
# One venv per distinct dependency set (pyproject.toml), built by bootstrap.sh pipeshub.
_deps=$(sha256sum "$PIPESHUB_PY/pyproject.toml" 2>/dev/null | cut -c1-12 || true)
export PY=${PY:-$WORK/venv-${_deps:-missing}/bin/python}
export FIXTURE_DIR=${FIXTURE_DIR:-$WORK/fixtures/pipeshub-codegraph-fixture-b3}  # B3/B3b fixture clone
export MODELS_JSON=${MODELS_JSON:-$ROOT/models.json}             # model config for B3 and B5 (models.example.json)
