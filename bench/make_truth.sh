#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B1 answer keys: index each subject repo with its language's SCIP indexer (versions in pins.toml), then
# turn the index into an answer key with scip_truth.py.   usage: make_truth.sh [name ...]  (default: all nine)
# Skip this and use the published keys instead with `./bootstrap.sh data` (bench/truth/<name>).
set -euo pipefail
cd "$(dirname "$0")"; B=$PWD
mkdir -p scip truth .gocache .m2cache
[ -x .venv/bin/python ] || python3.12 -m venv .venv   # an empty interpreter on PATH for scip-python, as measured
ALL="requests:requests:python django:django:python pipeshub-py:pipeshub-ai/backend/python:python
     excalidraw:excalidraw:typescript pipeshub-ts:pipeshub-ai/frontend:typescript gin:gin:go client-go:client-go:go
     spring-petclinic:spring-petclinic:java guava:guava:java"
want=" ${*:-requests django pipeshub-py excalidraw pipeshub-ts gin client-go spring-petclinic guava} "
for r in $ALL; do
  IFS=: read -r name dir lang <<< "$r"
  [[ "$want" == *" $name "* ]] || continue
  out=$B/scip/$name.scip
  if [ ! -s "$out" ]; then
    case $lang in
      python) (cd repos/$dir && PATH=$B/.venv/bin:$PATH NODE_OPTIONS=--max-old-space-size=12288 \
                 npx -y @sourcegraph/scip-python@0.6.6 index . --project-name "$name" --output "$out" > "$B/scip/$name.log" 2>&1) ;;
      typescript) (cd repos/$dir && NODE_OPTIONS=--max-old-space-size=12288 \
                 npx -y @sourcegraph/scip-typescript@0.4.0 index --output "$out" > "$B/scip/$name.log" 2>&1) ;;
      go) docker run --rm -u "$(id -u):$(id -g)" -e HOME=/cache -e GOPATH=/cache/go -e GOCACHE=/cache/build -e GOTOOLCHAIN=auto \
                 -v "$B/.gocache:/cache" -v "$B/bin:/tools:ro" -v "$B/repos/$dir:/src" -v "$B/scip:/out" -w /src \
                 sourcegraph/scip-go:latest /tools/scip-go --output "/out/$name.scip" > "$B/scip/$name.log" 2>&1 ;;
      java) extra=(); [ "$name" = guava ] && extra=(-- -DskipTests -Dmaven.javadoc.skip=true -pl guava -am)
            docker run --rm -u "$(id -u):$(id -g)" -e HOME=/cache -e JAVA_TOOL_OPTIONS="-Duser.home=/cache" \
                 -e MAVEN_OPTS="-Dmaven.repo.local=/cache/m2" -v "$B/.m2cache:/cache" -v "$B/repos/$dir:/src" -v "$B/scip:/out" -w /src \
                 sourcegraph/scip-java:latest scip-java index --build-tool=maven --output "/out/$name.scip" "${extra[@]}" > "$B/scip/$name.log" 2>&1
            find "repos/$dir" -name target -type d -prune -exec rm -rf {} + ;;  # keep the tree as measured
    esac
  fi
  bin/scip print --json "scip/$name.scip" > "scip/$name.json"
  "$PY" -I scip_truth.py "scip/$name.json" "repos/$dir" "truth/$name" "$lang" | head -c 300; echo
done
