#!/bin/sh
# Wait until both snapshot connectors (state.json, state-django.json from the setup scripts) have no record
# still queued or indexing, and no code-graph edge build started in the last 3 minutes. Polls every 2 minutes.
HERE=$(cd "$(dirname "$0")" && pwd)
IDS=$(python3 -c 'import json,sys; print(",".join(chr(34)+json.load(open(f))["connector_id"]+chr(34) for f in sys.argv[1:]))' "$HERE/state.json" "$HERE/state-django.json")
q() { docker exec -i cg-b11-neo4j-1 sh -c 'cypher-shell -u neo4j --format plain -p "${NEO4J_AUTH#*/}"'; }
while :; do
  n=$(echo "MATCH (r:Record) WHERE r.connectorId IN [$IDS] AND r.indexingStatus IN ['QUEUED','IN_PROGRESS','NOT_STARTED'] RETURN count(*);" | q | tail -1)
  echo "$(date +%T) records still to index: $n"
  if [ "$n" = 0 ] && ! docker logs --since 3m cg-b11-pipeshub-ai-1 2>&1 | grep -q 'Code edge build task started'; then echo indexed; exit 0; fi
  sleep 120
done
