#!/usr/bin/env bash
# B7 phase B: records per connector and indexing status.
cd "$(dirname "$0")"
PW=$(grep '^NEO4J_PASSWORD=' b7.env | cut -d= -f2-)
docker exec cg-b7-neo4j-1 cypher-shell -u neo4j -p "$PW" --format plain \
  "MATCH (r:Record) WHERE r.connectorName <> 'GITHUB TEAMS' WITH r.connectorName AS c, r.indexingStatus AS s, count(*) AS n RETURN c + ':' + s + '=' + toString(n)" 2>/dev/null | tail -n +2 | tr -d '"' | tr '\n' ' '
echo
