#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# B3 stack: this branch's image and its services, project cg-b3, beside other sessions' stacks.
# usage: stack.sh build|up|down|logs|ps|<any docker compose args>
set -uo pipefail
B3=$(cd "$(dirname "$0")" && pwd)
cd "$PIPESHUB_SRC/deployment/docker-compose"
C=(docker compose -p cg-b3 --env-file "$B3/b3.env" -f docker-compose.build.neo4j.yml -f "$B3/compose.override.yml")
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
case "${1:-}" in
  build) wait_cpu; DOCKER_BUILDKIT=1 "${C[@]}" build pipeshub-ai ;;
  up) "${C[@]}" up -d --wait ;;
  down) "${C[@]}" down "${@:2}" ;;
  *) "${C[@]}" "$@" ;;
esac
