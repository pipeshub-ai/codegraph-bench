"""The code-graph build state rows of the live B3 stack, one JSON line each."""
import json, time
from pathlib import Path
from neo4j import GraphDatabase
import logging
logging.getLogger("neo4j").setLevel(logging.ERROR)
HERE = Path(__file__).resolve().parent
pw = next(l.split("=", 1)[1] for l in (HERE / "b3.env").read_text().splitlines() if l.startswith("NEO4J_PASSWORD="))
d = GraphDatabase.driver("bolt://127.0.0.1:37687", auth=("neo4j", pw))
with d.session() as s:
    rows = s.run("MATCH (p:SyncPoint) WHERE p.edgeBuildStatus IS NOT NULL RETURN p.edgeBuildStatus AS st, "
                 "p.edgeBuildPending AS pend, p.edgeBuildAttempts AS att, p.edgeBuildLastError AS err, "
                 "p.lastEdgeBuildAt AS at ORDER BY st").data()
print(json.dumps(rows))
