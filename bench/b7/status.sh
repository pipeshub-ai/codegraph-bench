#!/usr/bin/env bash
# B7 indexing status: records by indexing status and code-graph edges, from the stack's Neo4j.
cd "$(dirname "$0")"
PW=$(grep '^NEO4J_PASSWORD=' b7.env | cut -d= -f2-)
docker exec cg-b7-neo4j-1 cypher-shell -u neo4j -p "$PW" --format plain \
  "MATCH (r:Record) RETURN r.connectorName AS connector, r.indexingStatus AS status, count(*) AS n ORDER BY connector, n DESC" 2>/dev/null
docker exec cg-b7-neo4j-1 cypher-shell -u neo4j -p "$PW" --format plain \
  "MATCH ()-[e:RECORD_LINK]->() WHERE e.source = 'code_graph' RETURN count(e) AS code_graph_edges" 2>/dev/null
