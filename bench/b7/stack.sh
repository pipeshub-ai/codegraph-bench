#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# B7 stack (Kafka): the pinned image and its services, project cg-b7, beside other sessions' stacks.
# usage: stack.sh build|up|down|logs|ps|<any docker compose args>
set -uo pipefail
B7_DIR=$(cd "$(dirname "$0")" && pwd)
export B7_DIR
cd "$PIPESHUB_SRC/deployment/docker-compose"
C=(docker compose -p cg-b7 --env-file "$B7_DIR/b7.env" -f docker-compose.build.neo4j.yml -f "$B7_DIR/compose.override.yml")
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
case "${1:-}" in
  build) wait_cpu; DOCKER_BUILDKIT=1 "${C[@]}" build pipeshub-ai ;;
  up) "${C[@]}" up -d --wait ;;
  down) "${C[@]}" down "${@:2}" ;;
  *) "${C[@]}" "$@" ;;
esac
