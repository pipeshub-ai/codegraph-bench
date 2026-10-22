#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B2 speed work: run_b2_speed.sh <tag> <concurrency> <name:dir ...>, against PIPESHUB_PY (default: the PR branch).
# Fresh Neo4j 5.26 with B2's production settings per repo; reports land in reports/b2-speed/<tag>/<name>.
set -uo pipefail
cd "$(dirname "$0")"
TAG=$1; CONC=$2; shift 2
export PYTHONPATH=${EXTRA_PYTHONPATH:-}
export NEO4J_URI=bolt://127.0.0.1:27688 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=cg-bench-pass NEO4J_DATABASE=neo4j
C=cg-speed-neo4j
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 85 ]; do sleep 30; done; }
fresh_neo4j() {
  docker rm -f -v $C > /dev/null 2>&1
  docker run -d --name $C -p 127.0.0.1:27688:7687 -p 127.0.0.1:27475:7474 \
    -e NEO4J_AUTH=neo4j/cg-bench-pass -e 'NEO4J_PLUGINS=["apoc"]' -e 'NEO4J_dbms_security_procedures_unrestricted=apoc.*' \
    -e NEO4J_dbms_memory_heap_initial__size=1G -e NEO4J_dbms_memory_heap_max__size=2G -e NEO4J_dbms_memory_pagecache_size=1G \
    --cpus 4 --memory 3g neo4j:5.26.0 > /dev/null
  for _ in $(seq 1 60); do curl -sf http://127.0.0.1:27475 > /dev/null && sleep 5 && return 0; sleep 3; done
  return 1
}
for r in "$@"; do
  name=${r%%:*}; dir=${r#*:}; out=../reports/b2-speed/$TAG/$name
  mkdir -p $out
  wait_cpu
  fresh_neo4j || { echo "neo4j failed $name"; continue; }
  (cd ../src && /usr/bin/time -f "%e %M" -o ../reports/b2-speed/$TAG/$name/time.txt \
     $PY -m cg_eval.scale --repo ../bench/repos/$dir $( [ -d ../bench/truth/$name ] && echo --key ../bench/truth/$name ) \
     --out ../reports/b2-speed/$TAG/$name --concurrency $CONC ${WARM:+--warm} > ../reports/b2-speed/$TAG/$name/run.log 2>&1) || echo "scale failed $name"
  echo "done $name $(python3 -c "import json;r=json.load(open('$out/report.json'));print(r['graph_seconds'], r['timings'], (r['accuracy'] or {}).get('handwritten'))" 2>&1 | cut -c1-400)"
done
docker rm -f -v $C > /dev/null 2>&1
