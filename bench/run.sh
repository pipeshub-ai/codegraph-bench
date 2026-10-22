#!/usr/bin/env bash
# One entry point per benchmark. Each sets the PipesHub commit that produced the published numbers
# (pins.toml [pipeshub]) unless PIPESHUB_REF is already set, then runs the measuring script unchanged.
#
#   bench/run.sh truth [name ...]          SCIP answer keys for B1 (or: ./bootstrap.sh data)
#   bench/run.sh b1 [name ...]             accuracy, 4 tools (TOOLS="pipeshub graphify cbm cgc" to choose)
#   bench/run.sh b2                        graph size, memory, latency on every subject (Neo4j in docker)
#   bench/run.sh b2-speed                  PipesHub build time on home-assistant (the 271 s figure)
#   bench/run.sh b2-tools                  Graphify 0.9.92 and codebase-memory-mcp 0.11.0 on home-assistant
#   bench/run.sh b3                        live GitHub sync + 7 steps x 3 + crash recovery (needs a GitHub token)
#   bench/run.sh b3b                       incremental vs fresh builds on the fixture, 4 tools
#   bench/run.sh b5a | b5b | b5c           SWE-bench ranking (no LLM) | SWE-bench agent | ContextBench
#   bench/run.sh verify [bench ...]        compare reports/ with expected/ (see verify.py)
set -uo pipefail
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ref() { python3 -c "import tomllib;print(tomllib.load(open('$HERE/../pins.toml','rb'))['pipeshub']['$1'])"; }
use() { [ -n "${PIPESHUB_REF_USER:-}" ] || export PIPESHUB_REF=$(ref "$1"); unset PIPESHUB_SRC PIPESHUB_PY; . "$HERE/env.sh"
        [ -d "$PIPESHUB_SRC" ] || { echo "missing $PIPESHUB_SRC: run ./bootstrap.sh pipeshub $PIPESHUB_REF"; exit 1; }
        echo "PipesHub $PIPESHUB_REF ($PIPESHUB_SRC)"; }
[ -n "${PIPESHUB_REF:-}" ] && export PIPESHUB_REF_USER=1
declare -A DIR=([requests]=requests [django]=django [pipeshub-py]=pipeshub-ai/backend/python [excalidraw]=excalidraw
                [pipeshub-ts]=pipeshub-ai/frontend [gin]=gin [client-go]=client-go [spring-petclinic]=spring-petclinic
                [guava]=guava [home-assistant]=home-assistant)
ALL9="requests django pipeshub-py excalidraw pipeshub-ts gin client-go spring-petclinic guava"
projects() { local out=""; for n in "$@"; do out+="$n:${DIR[$n]} "; done; echo "$out"; }

cmd=${1:-}; shift || true
case $cmd in
  truth) use b1; exec "$HERE/make_truth.sh" "$@" ;;
  b1)
    names=${*:-$ALL9}
    ( use b1; PROJECTS=$(projects $names) NO_COMPILE=1 "$HERE/run_b1_v2.sh" )
    (cd "$HERE" && python3 compile_b1.py > ../reports/b1-v2/COMPARISON.md) && echo "reports/b1-v2/COMPARISON.md" ;;
  b2)       use b2_graph; PROJECTS=$(projects $ALL9 home-assistant) exec "$HERE/run_b2.sh" ;;
  b2-speed) ( use b2_speed_base; "$HERE/run_b2_speed.sh" base 1 home-assistant:home-assistant )   # the 631 s "before"
            use b2_speed; exec "$HERE/run_b2_speed.sh" speed3 8 home-assistant:home-assistant ;;           # the 271 s
  b2-tools) use b2_graph; exec "$HERE/run_tools_large.sh" ;;   # tools/graphify is 0.9.92, tools/cbm is 0.11.0
  b3)       use b3_live; cd "$HERE/b3" && ./full_run.sh && "$PY" recovery.py ;;
  b3b)
    use b3b_pipeshub
    (cd "$HERE/../src" && "$PY" -m cg_eval.drift --out ../reports/b3b/pipeshub-drift)
    cd "$HERE/b3b" && "$PY" incremental.py "$@" && "$PY" rescore.py ;;
  b5a)      use b5a; "$HERE/run_b5a.sh" && exec "$HERE/run_b5g1.sh" ;;
  b5b)      # both arms at the search arm's commit, then the graph arm again at its rerun commit (the published one)
            ( use b5b_search; "$HERE/run_b5b.sh" test ); use b5b_graph; exec "$HERE/run_b5b_graph.sh" ;;
  b5c)
    use b5c; cd "$HERE/contextbench"
    ./clone_repos.sh && ./run_retrieve.sh && ./run_agent.sh search && ./run_agent.sh graph && "$PY" -u score_by_repo.py ;;
  verify)   exec python3 "$HERE/../verify.py" "$@" ;;
  *) sed -n '2,13p' "$0"; exit 1 ;;
esac
