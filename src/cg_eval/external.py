"""X-1: the code graph as an outside coding agent reaches it.

Calls the query service's ``/api/v1/code-graph`` routes (what the MCP tools
forward to) over the offline graph and checks that they answer like the
in-product tools, stay inside an MCP client's result budget, and never name a
repo the caller cannot read.
"""
from __future__ import annotations

import json
from collections import defaultdict

from .memgraph import MemBlobStore, MemGraph
from .offline import ORG
from .score import load_key
from .tools import EDGE_LANGS, USER, _case, _ep, _payload

# Claude Code warns past 10k tokens a result and cuts at 25k (notes/market-02 §5).
TOKEN_BUDGET = 8_000


class _Container:
    def __init__(self, graph: MemGraph) -> None:
        self._graph = graph

    async def graph_provider(self) -> MemGraph:
        return self._graph

    def logger(self):  # noqa: ANN201
        import logging
        return logging.getLogger("cg_eval.external")

    def config_service(self) -> None:
        return None


def _client(graph: MemGraph):  # noqa: ANN202
    from fastapi import FastAPI, Request
    from fastapi.testclient import TestClient

    from app.api.routes import code_graph as routes

    routes.BlobStorage = lambda **_: MemBlobStore(graph)
    app = FastAPI()
    app.container = _Container(graph)

    @app.middleware("http")
    async def _auth(request: Request, call_next):  # noqa: ANN001, ANN202
        request.state.user = {"orgId": ORG, "userId": USER}
        return await call_next(request)

    app.include_router(routes.router, prefix="/api/v1")
    return TestClient(app)


async def run_external_cases(graph: MemGraph, repos: dict, args) -> list[dict]:  # noqa: ANN001
    try:
        from app.api.routes import code_graph  # noqa: F401
    except ImportError:  # added with X-1
        return []
    from app.agents.actions.code_graph.code_graph import CodeGraph

    from .run import ADMIN, CONNECTOR, MAIN

    client = _client(graph)
    cases: list[dict] = []

    listed = client.get("/api/v1/code-graph/repos").json().get("repos", [])
    names = sorted(r["repo"] for r in listed)
    cases.append(_case("X-1-repos", names == sorted([MAIN, ADMIN]),
                       f"repos listed by name: {names}", ["X-1"]))

    # Parity: the HTTP answer is the in-product tool's answer.
    key = load_key(args.key / f"step-{args.step}" / MAIN)
    langs = {f["path"]: f["lang"] for f in key["files"]}
    degree: dict[str, int] = defaultdict(int)
    for e in key["edges"]:
        if e["rel"] == "CALLS" and "::" in e["to"] and langs.get(e["to"].partition("::")[0]) in EDGE_LANGS:
            degree[e["to"]] += 1
    hubs = sorted(degree, key=lambda ep: (-degree[ep], ep))[:10]
    tool = CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph,
                      "blob_store": MemBlobStore(graph)})
    same, sizes = 0, {}
    for ep in hubs:
        path, _, q = ep.partition("::")
        direct = _payload(await tool.get_neighbour(connector_id=CONNECTOR, file_path=path,
                                                   qualified_name=q, direction="inbound",
                                                   repo=MAIN, limit=200))
        res = client.post("/api/v1/code-graph/neighbours", json={
            "repo": MAIN, "file_path": path, "qualified_name": q, "direction": "inbound", "limit": 200})
        sizes[f"neighbours {ep}"] = len(res.text)
        over_http = res.json()
        same += {_ep(r) for r in direct.get("neighbors", [])} == \
                {_ep(r) for r in over_http.get("neighbors", [])}
    cases.append(_case("X-1-parity", same == len(hubs),
                       f"{same}/{len(hubs)} caller lists over HTTP match the in-product tool",
                       ["X-1"], matched=same, total=len(hubs)))

    # Budget: the largest answers a client can ask for.
    files = repos[MAIN][1]
    biggest = max((p for p in files if langs.get(p) in EDGE_LANGS),
                  key=lambda p: len(files[p]), default=None)
    probes = {
        "symbols **": ("/query", {"repo": MAIN, "select": "**", "limit": 500}),
        "neighbours depth 3": ("/neighbours", {"repo": MAIN, "file_path": hubs[0].partition("::")[0],
                                               "depth": 3, "limit": 200}),
    }
    if biggest:
        probes[f"read {biggest}"] = ("/read", {"repo": MAIN, "file_path": biggest, "max_lines": 2000})
    for label, (route, body) in probes.items():
        sizes[label] = len(client.post(f"/api/v1/code-graph{route}", json=body).text)
    worst = max(sizes, key=sizes.get)
    tokens = sizes[worst] // 4
    cases.append(_case("X-1-budget", tokens <= TOKEN_BUDGET,
                       f"largest result {sizes[worst]:,} chars (~{tokens:,} tokens, budget "
                       f"{TOKEN_BUDGET:,}) from {worst}", ["X-1"], max_chars=sizes[worst]))

    # ACL: a repo the caller cannot read is absent, not named.
    admin_ids = {rid for rid, r in graph.records.items() if r.get("repo") == ADMIN}
    before = set(graph.denied)
    graph.denied |= admin_ids
    try:
        hidden = [r["repo"] for r in client.get("/api/v1/code-graph/repos").json().get("repos", [])]
        probe = client.post("/api/v1/code-graph/query", json={"repo": ADMIN, "select": "**"})
    finally:
        graph.denied = before
    leaked = ADMIN in hidden or ADMIN in json.dumps(probe.json().get("repos", []))
    cases.append(_case("X-1-acl", not leaked and probe.status_code == 404,
                       f"unreadable repo listed: {ADMIN in hidden}; query on it -> {probe.status_code}",
                       ["X-1", "S-1"]))
    return cases
