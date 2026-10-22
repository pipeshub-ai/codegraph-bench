"""Where the live B3 sync stands, read from the stack's Neo4j: records by status, blocks, code-graph edges."""
import json, sys
from pathlib import Path
from neo4j import GraphDatabase

HERE = Path(__file__).resolve().parent
pw = next(l.split("=", 1)[1] for l in (HERE / "b3.env").read_text().splitlines() if l.startswith("NEO4J_PASSWORD="))
cid = json.loads((HERE / "state.json").read_text())["connector_id"]
d = GraphDatabase.driver("bolt://127.0.0.1:37687", auth=("neo4j", pw))
with d.session() as s:
    rows = s.run("MATCH (r:Record {connectorId: $c}) RETURN r.recordType AS t, r.indexingStatus AS st, count(*) AS n", c=cid).data()
    blocks = s.run("MATCH (b:Block {connectorId: $c}) RETURN count(b) AS n", c=cid).single()["n"]
    edges = s.run("MATCH (a:Block {connectorId: $c})-[e:RECORD_RELATION {source: 'code_graph'}]->() RETURN count(e) AS n", c=cid).single()["n"]
print(json.dumps({"records": rows, "blocks": blocks, "code_graph_edges": edges}))
