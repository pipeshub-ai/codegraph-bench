#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B2: each project through PipesHub's write path into a fresh Neo4j 5.26 with the
# production settings (2 GB heap, 1 GB page cache, 3 GB / 4 CPU limit, APOC).
# Neo4j's memory is sampled every 2 s; its store size is read after the run.
set -uo pipefail
cd "$(dirname "$0")"
export NEO4J_URI=bolt://127.0.0.1:27687 NEO4J_USERNAME=neo4j NEO4J_PASSWORD=cg-bench-pass NEO4J_DATABASE=neo4j
C=cg-bench-neo4j
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
fresh_neo4j() {
  docker rm -f -v $C > /dev/null 2>&1
  docker run -d --name $C -p 127.0.0.1:27687:7687 -p 127.0.0.1:27474:7474 \
    -e NEO4J_AUTH=neo4j/cg-bench-pass -e 'NEO4J_PLUGINS=["apoc"]' -e 'NEO4J_dbms_security_procedures_unrestricted=apoc.*' \
    -e NEO4J_dbms_memory_heap_initial__size=1G -e NEO4J_dbms_memory_heap_max__size=2G -e NEO4J_dbms_memory_pagecache_size=1G \
    --cpus 4 --memory 3g neo4j:5.26.0 > /dev/null
  for _ in $(seq 1 60); do curl -sf http://127.0.0.1:27474 > /dev/null && return 0; sleep 3; done
  return 1
}
PROJECTS="${PROJECTS:-requests:requests django:django pipeshub-py:pipeshub-ai/backend/python excalidraw:excalidraw
          pipeshub-ts:pipeshub-ai/frontend gin:gin client-go:client-go spring-petclinic:spring-petclinic guava:guava home-assistant:home-assistant}"
for r in $PROJECTS; do
  name=${r%%:*}; dir=${r#*:}; out=../reports/b2/$name
  mkdir -p $out
  wait_cpu
  fresh_neo4j || { echo "neo4j failed $name"; continue; }
  (while sleep 2; do docker stats --no-stream --format '{{.MemUsage}}' $C 2>/dev/null; done) > $out/neo4j_mem.txt &
  sampler=$!
  (cd ../src && /usr/bin/time -f "%e %M" -o ../reports/b2/$name/time.txt \
     $PY ${WITH_RUST:-} -m cg_eval.scale --repo ../bench/repos/$dir $( [ -d ../bench/truth/$name ] && echo --key ../bench/truth/$name ) --out ../reports/b2/$name \
     > ../reports/b2/$name/run.log 2>&1) || echo "scale failed $name"
  kill $sampler 2>/dev/null
  docker exec $C cypher-shell -u neo4j -p cg-bench-pass "CALL db.checkpoint()" > /dev/null 2>&1  # flush so the store size is real
  docker exec $C du -sk /data/databases/neo4j /data/transactions/neo4j > $out/store_kb.txt 2>&1
  echo "done $name $(cat $out/time.txt 2>/dev/null | tail -1)"
done
docker rm -f -v $C > /dev/null 2>&1
