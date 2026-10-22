#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B1 v2: every tool at its best settings, on the corrected Java truth (no annotation "calls").
#   PipesHub: this branch.  Graphify 0.9.92 `update --no-cluster` (its most exact export).
#   codebase-memory-mcp 0.12.0, mode full, Go methods shared by several receivers left out
#   (fanning them out cost it more than it gained).  CodeGraphContext 0.6.13 with
#   ENABLE_INHERIT_RESOLVE=true, every type label read, scored even when its index exits non-zero.
# A guard stops a tool when machine memory passes 88% (the run is marked "capped (memory)").
set -uo pipefail
cd "$(dirname "$0")"
B=$PWD
OUT=../reports/b1-v2
W=work-v2
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
mem_pct() { free | awk '/Mem:/{printf "%d", 100*$3/$2}'; }
TOOLS=${TOOLS:-"pipeshub graphify cbm cgc"}
has() { [[ " $TOOLS " == *" $1 "* ]]; }
guard() {  # pid: SIGTERM it if machine memory passes 88% (off under run_b1_v2_par.py, which guards itself)
  [ "${GUARD:-1}" = 1 ] || return 0
  while kill -0 "$1" 2>/dev/null; do
    [ "$(mem_pct)" -ge 88 ] && { echo "memory guard: stopping $1"; pkill -TERM -P "$1"; kill -TERM "$1"; }
    sleep 5
  done
}
timed() {  # timefile cmd...: run under /usr/bin/time with the memory guard
  local tf=$1; shift
  /usr/bin/time -f "%e %M" -o "$tf" "$@" & local pid=$!
  guard $pid & local g=$!
  wait $pid; local rc=$?
  kill $g 2>/dev/null
  return $rc
}
score() {  # tool name
  (cd ../src && $PY -m cg_eval.score_tool --edges ../bench/$W/$1/$2.edges.jsonl --key ../bench/truth/$2 \
      --pipeshub ../reports/b1-v2/pipeshub/$2/report.json --out ../reports/b1-v2/$1/$2 > /dev/null 2>&1)
}
PROJECTS=${PROJECTS:-"requests:requests django:django pipeshub-py:pipeshub-ai/backend/python excalidraw:excalidraw
          pipeshub-ts:pipeshub-ai/frontend gin:gin client-go:client-go spring-petclinic:spring-petclinic guava:guava"}
for r in $PROJECTS; do
  name=${r%%:*}; dir=${r#*:}
  wait_cpu
  has pipeshub && (cd ../src && $PY -m cg_eval.real --repo ../bench/repos/$dir --key ../bench/truth/$name --out $OUT/pipeshub/$name > /dev/null 2>&1) \
    || echo "pipeshub failed $name"
  for tool in graphify cbm cgc; do
    mkdir -p $W/$tool
    # Copied under a temporary name, so a copy cut short is never reused as complete.
    [ -d $W/$tool/$name ] || { rm -rf $W/$tool/$name.part && cp -al repos/$dir $W/$tool/$name.part && mv $W/$tool/$name.part $W/$tool/$name; }
  done
  if has graphify; then
  wait_cpu
  rm -rf $W/graphify/$name/graphify-out
  (cd $W/graphify/$name && timed ../$name.time timeout 5400 $B/tools/graphify/bin/graphify update . --no-cluster > ../$name.log 2>&1) \
    || echo "graphify failed $name"
  python3 adapters/graphify_edges.py $W/graphify/$name/graphify-out/graph.json $W/graphify/$name.edges.jsonl > /dev/null 2>&1 \
    && score graphify $name
  fi
  if has cbm; then
  wait_cpu
  CBM_SHARED_METHODS=drop HOME=$B/tools/cbm-012/home timed $W/cbm/$name.time timeout 5400 \
     python3 adapters/cbm_edges.py tools/cbm-012/codebase-memory-mcp $W/cbm/$name $W/cbm/$name.edges.jsonl \
     > $W/cbm/$name.log 2>&1 && score cbm $name || echo "cbm failed $name"
  fi
  if has cgc; then
  wait_cpu
  rm -rf $W/cgc/$name.kuzu
  HOME=$B/tools/cgc-home timed $W/cgc/$name.time timeout 7200 \
     tools/cgc/bin/cgc --db kuzudb --db-path $B/$W/cgc/$name.kuzu index $W/cgc/$name --no-progress \
     > $W/cgc/$name.log 2>&1 || echo "cgc index exited non-zero on $name (graph scored as written)"
  # A tool error after CALLS/INHERITS are written (spring-petclinic's READS bug) still leaves a usable graph.
  [ -e $W/cgc/$name.kuzu ] && tools/cgc/bin/python adapters/cgc_edges.py $B/$W/cgc/$name.kuzu $W/cgc/$name $W/cgc/$name.edges.jsonl \
     >> $W/cgc/$name.log 2>&1 && score cgc $name || echo "cgc adapter failed $name"
  rm -rf $W/cgc/$name.kuzu  # disk is tight
  fi
  echo "done $name graphify=$(cat $W/graphify/$name.time 2>/dev/null | tail -1) cbm=$(cat $W/cbm/$name.time 2>/dev/null | tail -1) cgc=$(cat $W/cgc/$name.time 2>/dev/null | tail -1) disk=$(df -h / | awk 'NR==2{print $4}')"
done
[ -n "${NO_COMPILE:-}" ] || python3 compile_b1.py > $OUT/COMPARISON.md
echo "b1 v2 done"
