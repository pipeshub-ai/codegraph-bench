#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# B11 stack (VS Code and Django snapshots): this branch's image and its services, project cg-b11, beside other sessions' stacks.
# usage: stack.sh build|up|down|logs|ps|<any docker compose args>
set -uo pipefail
B11=$(cd "$(dirname "$0")" && pwd)
cd "$PIPESHUB_SRC/deployment/docker-compose"
C=(docker compose -p cg-b11 --env-file "$B11/b11.env" -f docker-compose.build.neo4j.yml -f "$B11/compose.override.yml")
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
case "${1:-}" in
  build) wait_cpu; DOCKER_BUILDKIT=1 "${C[@]}" build pipeshub-ai ;;
  up) "${C[@]}" up -d --wait ;;
  down) "${C[@]}" down "${@:2}" ;;
  *) "${C[@]}" "$@" ;;
esac
