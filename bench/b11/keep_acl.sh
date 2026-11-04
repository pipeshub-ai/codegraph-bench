#!/bin/sh
. "$(cd "$(dirname "$0")" && pwd)/../env.sh"
# Workaround, needed on the measured stack: after a connector sync the org READER edge on the snapshot
# repos' record groups (and the "All" team's relation to the GitHub Teams app) could go missing, and
# every code tool then answered "not found" for an indexed repo. Re-adds both (idempotent) every
# 5 minutes while a run is going. run.sh starts it; harmless when nothing is missing.
OWNER=${B11_VSCODE_REPO%%/*}
while :; do
  docker exec -i cg-b11-neo4j-1 sh -c 'cypher-shell -u neo4j --format plain -p "${NEO4J_AUTH#*/}"' >/dev/null 2>&1 <<Q
MATCH (o:Organization)<-[:BELONGS_TO]-(g:RecordGroup)
WHERE g.groupName IN ["$OWNER", "$B11_VSCODE_REPO", "$B11_DJANGO_REPO", "Code repository"]
MERGE (o)-[p:PERMISSION]->(g) ON CREATE SET p.role = "READER", p.type = "ORG", p.createdAtTimestamp = timestamp(), p.updatedAtTimestamp = timestamp();
MATCH (t:Teams {name: "All"}), (a:App) WHERE a.type = "GitHub Teams"
MERGE (t)-[r:USER_APP_RELATION]->(a) ON CREATE SET r.isExternalUser = false, r.syncState = "NOT_STARTED", r.createdAtTimestamp = timestamp(), r.updatedAtTimestamp = timestamp();
Q
  sleep 300
done
