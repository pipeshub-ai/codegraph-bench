#!/usr/bin/env bash
# Waits until phase B's sources are fully indexed (nothing queued or running; folder placeholders stay NOT_STARTED), then asks
# the same 90 questions and grades both phases.
cd "$(dirname "$0")"
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"   # PY, MODELS_JSON, PIPESHUB_SRC
PW=$(grep '^NEO4J_PASSWORD=' b7.env | cut -d= -f2-)
# Jira and the docs must be fully indexed; Confluence may leave a handful of records that never
# leave QUEUED (comments and pages with no recognised content type), so up to 10 are tolerated.
# Placeholders (Jira's stubs for linked issues outside the sync window) are never indexed.
pending() { docker exec cg-b7-neo4j-1 cypher-shell -u neo4j -p "$PW" --format plain \
  "MATCH (r:Record) WHERE r.connectorName IN ['JIRA DATA CENTER PERSONAL','CONFLUENCE DATA CENTER','WEB'] AND r.indexingStatus IN ['QUEUED','IN_PROGRESS'] AND coalesce(r.isPlaceholder, false) = false WITH r.connectorName = 'CONFLUENCE DATA CENTER' AS conf, count(r) AS n RETURN sum(CASE WHEN conf THEN CASE WHEN n > 10 THEN n ELSE 0 END ELSE n END)" 2>/dev/null | tail -n 1; }
stable=0
while [ $stable -lt 3 ]; do n=$(pending); if [ "${n:-1}" = "0" ]; then stable=$((stable+1)); else stable=0; fi; sleep 120; done
echo "$(date +%T) phase B indexed; asking"
$PY -u ask.py with-company --jobs 3
echo "$(date +%T) phase B answered; grading"
$PY -u grade.py > ../../reports/b7/grade-final.log 2>&1
echo "$(date +%T) done"
