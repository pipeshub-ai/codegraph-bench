#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# The Kafka checkout the Claude Code arms read: apache/kafka at kafka_trunk.sha (the commit PipesHub's GitHub
# connector synced in the published run), docs/ removed as the code-only stage held it back.
#   checkout.sh            bench/work/b7g/kafka-nograph (Claude Code alone, + PipesHub arms)
#   checkout.sh graphify   + bench/work/b7g/kafka with Graphify's graph (graphify update . --no-cluster)
# The dump arm's copy (kafka-dump, + _company/) is written by export_dump.py from a synced stack.
set -euo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
SHA=$(cat "$HERE/kafka_trunk.sha"); S=${B7G_SCRATCH:-$ROOT/bench/work/b7g}
mkdir -p "$S"
[ -d "$S/src/.git" ] || git init -q "$S/src"
git -C "$S/src" fetch -q --depth 1 https://github.com/apache/kafka "$SHA"
rm -rf "$S/kafka-nograph"; mkdir -p "$S/kafka-nograph"
git -C "$S/src" archive "$SHA" | tar -x -C "$S/kafka-nograph"
rm -rf "$S/kafka-nograph/docs"
echo "kafka-nograph at $SHA, docs/ removed"
if [ "${1:-}" = graphify ]; then
  rm -rf "$S/kafka"; cp -al "$S/kafka-nograph" "$S/kafka"
  (cd "$S/kafka" && "$ROOT/bench/tools/graphify/bin/graphify" update . --no-cluster > "$S/kafka-graphify.log" 2>&1)
  echo "kafka with graphify-out/"
fi
