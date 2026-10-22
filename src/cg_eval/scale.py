"""B2: one repo through PipesHub's code-graph write path into a real Neo4j.

The stages production runs, timed separately: code file records
(``batch_upsert_records``), parse and block projection
(``write_code_file_blocks_to_graph``), the edge builder, then the agent tools
asked as a user with access. File policy is the same as B1 (``offline``). The
embedding text is built by the indexer's own ``_build_code_documents`` and
counted with its tokenizer; nothing is embedded or sent anywhere. The edges are
read back from Neo4j and scored against the B1 truth, so the database path is
checked to give the same graph as the in-memory one.

usage: python -m cg_eval.scale --repo DIR --key TRUTH_DIR --out DIR
env: NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import resource
import statistics
import time
from pathlib import Path
from unittest.mock import MagicMock

from .run import CONNECTOR  # noqa: F401  (sets PIPESHUB_PY on sys.path)
from .offline import UNNAMED_KINDS, _path_kwarg, read_tree, record_id_for
from .real import score_against_truth

ORG = "org-bench"
RECORD_BATCH = 500
# Classification input is cut at CONTENT_TOKEN_RATIO of the model's context; assume a 128k model.
LLM_CONTEXT = 128_000
TOOL_SAMPLES = 50
log = logging.getLogger("cg-bench-scale")


async def connect():
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    provider = Neo4jProvider(log, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    await provider.ensure_schema()
    if os.environ.get("CG_PROFILE_QUERIES"):
        _profile_queries(provider)
    return provider


QUERY_PROFILE: dict[str, list[float]] = {}


def _profile_queries(provider) -> None:
    """Time every Cypher statement by its shape (whitespace-normalized head), for the speed work."""
    client, inner = provider.client, provider.client.execute_query

    async def timed(query, *args, **kwargs):
        shape = " ".join(query.split())[:150]
        started = time.perf_counter()
        try:
            return await inner(query, *args, **kwargs)
        finally:
            QUERY_PROFILE.setdefault(shape, []).append(time.perf_counter() - started)

    client.execute_query = timed


class Blobs:
    """The parsed output the indexer uploads, keyed by record, for the edge builder.

    Held as JSON bytes, as blob storage holds it: kept as live objects, the whole
    repo's parse output made the garbage collector rescan gigabytes, inflating
    parse time (B2-G4: 29 s became 50 s on pipeshub-ai's backend).
    """

    def __init__(self) -> None:
        self.records: dict[str, bytes] = {}

    def put(self, rid: str, container) -> None:
        self.records[rid] = container.model_dump_json(exclude_none=True).encode()

    async def load(self, org_id: str, record_ids: list[str]) -> dict:
        from app.modules.code_graph.facts_source import facts_from_blob_record

        return {rid: facts_from_blob_record(rid, {"block_containers": json.loads(self.records[rid])})
                for rid in record_ids if rid in self.records}


def plan_files(name: str, files: dict[str, bytes]) -> tuple[list[dict], dict[str, int]]:
    """Which files get a record and how each is routed: B1's policy, without the graph."""
    from app.modules.parsers.code_parser.file_role import classify_file_role, should_index_code_file
    from app.modules.parsers.code_parser.gitattributes import parse_linguist_exclusions
    from app.modules.parsers.code_parser.routing import CodeFileRoute, plan_code_file
    from app.modules.parsers.code_parser.secret_scan import redact_secrets_bytes

    linguist = parse_linguist_exclusions(files.get(".gitattributes", b"").decode("utf-8", "replace"))
    planned, routes = [], {}
    for path in sorted(files):
        if not should_index_code_file(path)[0] or linguist.excludes(path):
            routes["no-record"] = routes.get("no-record", 0) + 1
            continue
        content, _ = redact_secrets_bytes(files[path])
        base = path.rsplit("/", 1)[-1]
        ext = base.rsplit(".", 1)[-1] if "." in base else None
        plan = plan_code_file(base, path, ext, content, repository_file=True)
        route = f"skip:{plan.skip_cause.value}" if plan.route is CodeFileRoute.SKIP else plan.route.value
        routes[route] = routes.get(route, 0) + 1
        planned.append({"path": path, "rid": record_id_for(name, path), "content": content, "ext": ext, "route": route,
                        "language": plan.parser if plan.route is CodeFileRoute.CODE else None,
                        "role": classify_file_role(path).value})
    return planned, routes


async def write_records(graph, planned: list[dict], connector_id: str, group_id: str) -> None:
    import hashlib

    from app.config.constants.arangodb import Connectors, OriginTypes, ProgressStatus
    from app.models.entities import CodeFileRecord, RecordType

    rows = [CodeFileRecord(
        id=f["rid"], org_id=ORG, record_name=f["path"].rsplit("/", 1)[-1], record_type=RecordType.CODE_FILE,
        external_record_id=f"ext-{f['rid']}", version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GITHUB, connector_id=connector_id, record_group_id=group_id,
        mime_type="text/plain", extension=f["ext"], file_path=f["path"], virtual_record_id=f["rid"],
        file_hash=hashlib.sha1(f["content"]).hexdigest(), language=f["language"], file_role=f["role"],
        indexing_status=ProgressStatus.COMPLETED.value,
    ) for f in planned]
    for i in range(0, len(rows), RECORD_BATCH):
        await graph.batch_upsert_records(rows[i:i + RECORD_BATCH])


def embedding_tokens(container, path: str, rid: str) -> tuple[int, int]:
    """(documents, tokens) the indexer would embed for this file's code blocks."""
    from app.modules.transformers.vectorstore import _build_code_documents, _token_len

    blocks = [b for b in container.blocks if str(getattr(b.type, "value", b.type)).lower() == "code"]
    docs = _build_code_documents(blocks, rid, ORG, f"{path.rsplit('/', 1)[-1]} — {path}")
    return len(docs), sum(_token_len(d.page_content) for d in docs)


async def project(graph, planned: list[dict], connector_id: str, group_id: str, blobs: Blobs,
                  concurrency: int = 1) -> dict:
    from app.modules.code_graph.block_projection import BlockProjectionContext, write_code_file_blocks_to_graph
    from app.modules.parsers.code_parser import CodeFileParser

    parser = CodeFileParser()
    from app.modules.parsers.code_parser.classification_policy import code_classification_skip_reason
    from app.modules.transformers.document_extraction import CONTENT_TOKEN_RATIO, DocumentExtraction
    from app.modules.transformers.vectorstore import _token_len

    llm_cap = int(LLM_CONTEXT * CONTENT_TOKEN_RATIO)
    stats = {"parse_seconds": 0.0, "project_seconds": 0.0, "embed_seconds": 0.0, "code_files": 0,
             "embed_documents": 0, "embed_tokens": 0, "llm_calls": 0, "llm_input_tokens": 0,
             "llm_skipped": {}, "symbols": []}
    classified: set[bytes] = set()
    # concurrency > 1: files in flight at once, as the indexing service runs them. Parsing stays on
    # this thread; what overlaps is the projection's waits on Neo4j.
    gate = asyncio.Semaphore(concurrency)

    async def one(f: dict) -> None:
        async with gate:
            await _project_one(f)

    async def _project_one(f: dict) -> None:
        base = f["path"].rsplit("/", 1)[-1]
        started = time.perf_counter()
        if concurrency > 1 and hasattr(parser, "parse_to_blocks_off_loop"):
            # As the indexer parses: off the event loop, in a worker thread.
            container = await parser.parse_to_blocks_off_loop(f["content"], base, f["path"], f["language"])
        else:
            container = parser.parse_to_blocks(f["content"], base, f["path"], f["language"])
        stats["parse_seconds"] += time.perf_counter() - started
        if container is None or (not container.blocks and not container.block_groups):
            return
        stats["code_files"] += 1
        blobs.put(f["rid"], container)
        started = time.perf_counter()
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG, record_id=f["rid"], record_group_id=group_id,
                                           connector_id=connector_id, language=f["language"],
                                           **_path_kwarg(f["path"])),
            block_containers=container,
        )
        stats["project_seconds"] += time.perf_counter() - started
        started = time.perf_counter()
        docs, tokens = embedding_tokens(container, f["path"], f["rid"])
        stats["embed_seconds"] += time.perf_counter() - started
        stats["embed_documents"] += docs
        stats["embed_tokens"] += tokens
        started = time.perf_counter()  # the LLM-cost estimate is reported apart from the graph, like the embed text
        # One classification call per code file the policy keeps; identical content is classified once.
        skip = code_classification_skip_reason(f["path"], container)
        if skip:
            reason = skip.split(" ", 1)[1] if skip[0].isdigit() else skip
            stats["llm_skipped"][reason] = stats["llm_skipped"].get(reason, 0) + 1
        elif f["content"] not in classified:
            classified.add(f["content"])
            body = sum(_token_len(b.data.get("text", "") if isinstance(b.data, dict) else str(b.data or ""))
                       for b in container.blocks if str(getattr(b.type, "value", b.type)).lower() == "code")
            try:
                prompt = _token_len(DocumentExtraction._fill_prompt("", base, "CODE_FILE"))
            except TypeError:  # after main's merge the record header is a template of its own
                prompt = _token_len(DocumentExtraction._fill_prompt("")
                                    + DocumentExtraction._record_header(base, "CODE_FILE"))
            stats["llm_calls"] += 1
            stats["llm_input_tokens"] += min(body, llm_cap) + prompt
        stats["embed_seconds"] += time.perf_counter() - started
        for b in container.blocks:
            q = getattr(getattr(b, "code_metadata", None), "qualified_name", None) or ""
            if q.split(":", 1)[0] in ("function", "method"):
                stats["symbols"].append((f["path"], q))

    await asyncio.gather(*(one(f) for f in planned if f["language"]))
    stats["symbols"].sort()  # completion order varies with concurrency; the tool sample must not
    return stats


async def grant(graph, user_id: str, connector_id: str, group_id: str, record_ids: list[str]) -> None:
    """Access as the GitHub connector grants it: the user is in the connector's group, the
    group reads the repo's record group, and every file inherits from that record group."""
    from app.config.constants.arangodb import CollectionNames
    from app.utils.time_conversion import get_epoch_timestamp_in_ms

    C, now, user_key, cgroup = CollectionNames, get_epoch_timestamp_in_ms(), f"u-{user_id}", f"cg-{connector_id}"
    stamp = {"createdAtTimestamp": now, "updatedAtTimestamp": now}
    await graph.batch_upsert_nodes([{"id": user_key, "userId": user_id, "orgId": ORG, "email": f"{user_id}@example.com",
                                     "fullName": "Bench", "isActive": True, **stamp}], collection=C.USERS.value)
    await graph.batch_upsert_nodes([{"id": connector_id, "name": "GitHub", "type": "GitHub", "appGroup": "GitHub",
                                     "scope": "team", "isActive": True, **stamp}], collection=C.APPS.value)
    await graph.batch_upsert_nodes([{"id": cgroup, "name": "ConnectorGroup", "orgId": ORG, "connectorId": connector_id,
                                     "connectorName": "GITHUB", "externalGroupId": cgroup, **stamp}],
                                   collection=C.GROUPS.value)
    await graph.batch_upsert_nodes([{"id": group_id, "groupName": group_id, "orgId": ORG, "connectorId": connector_id,
                                     "connectorName": "GITHUB", "groupType": "REPOSITORY",
                                     "externalGroupId": group_id, **stamp}], collection=C.RECORD_GROUPS.value)

    def edge(from_id: str, from_c: str, to_id: str, to_c: str, **extra: object) -> dict:
        return {"from_id": from_id, "from_collection": from_c, "to_id": to_id, "to_collection": to_c, **stamp, **extra}

    await graph.batch_create_edges([edge(user_key, C.USERS.value, cgroup, C.GROUPS.value, type="USER", role="READER"),
                                    edge(cgroup, C.GROUPS.value, group_id, C.RECORD_GROUPS.value, type="GROUP", role="READER")],
                                   collection=C.PERMISSION.value)
    for i in range(0, len(record_ids), RECORD_BATCH):
        await graph.batch_create_edges([edge(r, C.RECORDS.value, group_id, C.RECORD_GROUPS.value)
                                        for r in record_ids[i:i + RECORD_BATCH]], collection=C.INHERIT_PERMISSIONS.value)
    await graph.batch_create_edges([edge(user_key, C.USERS.value, connector_id, C.APPS.value, syncState="COMPLETED",
                                         lastSyncUpdate=now)], collection=C.USER_APP_RELATION.value)


def percentiles(samples: list[float]) -> dict:
    if not samples:
        return {}
    q = statistics.quantiles(samples, n=20, method="inclusive") if len(samples) > 1 else samples * 19
    return {"n": len(samples), "p50_ms": round(1000 * statistics.median(samples), 1),
            "p95_ms": round(1000 * q[18], 1), "max_ms": round(1000 * max(samples), 1)}


async def tool_latency(graph, connector_id: str, user_id: str, symbols: list[tuple[str, str]]) -> dict:
    from app.agents.actions.code_graph.neighbours import get_neighbour_impl, impact_impl
    from app.agents.actions.code_graph.path import find_symbol_path_impl

    rng = random.Random(7)
    picks = rng.sample(symbols, min(TOOL_SAMPLES, len(symbols)))
    common = {"graph_provider": graph, "connector_id": connector_id, "org_id": ORG, "user_id": user_id}
    calls = {
        "get_neighbour": lambda p, q, _: get_neighbour_impl(**common, file_path=p, qualified_name=q, direction="inbound"),
        "impact": lambda p, q, _: impact_impl(**common, file_path=p, qualified_name=q),
        "find_symbol_path": lambda p, q, o: find_symbol_path_impl(**common, file_path_a=p, qualified_name_a=q,
                                                                   file_path_b=o[0], qualified_name_b=o[1]),
    }
    out = {}
    for name, call in calls.items():
        times, errors, empty = [], 0, 0
        for i, (path, q) in enumerate(picks):
            started = time.perf_counter()
            result = await call(path, q, picks[(i + 1) % len(picks)])
            times.append(time.perf_counter() - started)
            errors += bool(result.get("error"))
            empty += not any(v for k, v in result.items() if isinstance(v, list))
        out[name] = {**percentiles(times), "errors": errors, "empty_answers": empty}
    return out


def read_back(group_id: str) -> tuple[list[dict], dict]:
    """The builder's edges from Neo4j in answer-key notation, plus graph counts."""
    from neo4j import GraphDatabase

    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
    with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as s:
        paths = {r["k"]: r["p"] for r in s.run(
            "MATCH (r:Record {recordGroupId: $g}) RETURN r.id AS k, r.filePath AS p", g=group_id)}
        if not any(paths.values()):  # filePath lives on the file node, not the record
            paths = {r["k"]: r["p"] for r in s.run(
                "MATCH (r:Record {recordGroupId: $g})-[]-(f) WHERE f.filePath IS NOT NULL "
                "RETURN r.id AS k, f.filePath AS p", g=group_id)}

        def endpoint(labels: list[str], key: str, rec: str | None, q: str | None) -> str | None:
            if "Record" in labels:
                return paths.get(key)
            path = paths.get(rec)
            kind, _, tail = (q or "").partition(":")
            if not path:
                return None
            if not tail or kind in UNNAMED_KINDS or (tail.startswith("L") and tail[1:2].isdigit()) or tail == "anonymous":
                return path
            return f"{path}::{q}"

        edges = []
        for r in s.run("MATCH (a)-[e]->(b) WHERE e.source = 'code_graph' AND (a.recordId IN $ids OR a.id IN $ids) "
                       "RETURN labels(a) AS la, a.id AS ka, a.recordId AS ra, a.qualifiedName AS qa, "
                       "labels(b) AS lb, b.id AS kb, b.recordId AS rb, b.qualifiedName AS qb, "
                       "e.relationshipType AS rel, type(e) AS t, e.confidence AS c", ids=list(paths)):
            src, dst = endpoint(r["la"], r["ka"], r["ra"], r["qa"]), endpoint(r["lb"], r["kb"], r["rb"], r["qb"])
            if src and dst:
                edges.append({"rel": r["rel"] or r["t"], "from": src, "to": dst, "confidence": r["c"]})
        counts = {"nodes": s.run("MATCH (n) RETURN count(n) AS c").single()["c"],
                  "relationships": s.run("MATCH ()-[e]->() RETURN count(e) AS c").single()["c"],
                  "code_graph_edges": len(edges)}
    driver.close()
    return edges, counts


_WARM_FILES = {
    "warm/__init__.py": b"",
    "warm/base.py": b"class Base:\n    def run(self, x: int) -> int:\n        return x\n",
    "warm/child.py": b"from warm.base import Base\n\nclass Child(Base):\n    def run(self, x: int) -> int:\n"
                     b"        return super().run(x) + helper(x)\n\ndef helper(y):\n    return Child().run(y)\n",
    "web/a.ts": b"import { b } from './b';\nexport class A { go(): number { return b(); } }\n",
    "web/b.ts": b"export function b(): number { return 1; }\n",
}


async def warm_up(graph, concurrency: int) -> None:
    """Index a five-file repo and delete it, untimed: a fresh Neo4j compiles a plan for
    every statement shape on first use, which a running deployment did long ago."""
    from neo4j import GraphDatabase

    name, connector_id, group_id = "warm", "conn-warm", "group-warm"
    planned, _ = plan_files(name, dict(_WARM_FILES))
    await write_records(graph, planned, connector_id, group_id)
    blobs = Blobs()
    await project(graph, planned, connector_id, group_id, blobs, concurrency)
    from app.modules.code_graph.edge_builder import build_code_graph_edges

    await build_code_graph_edges(graph_provider=graph, facts_source=blobs, org_id=ORG, record_group_id=group_id)
    ids = [f["rid"] for f in planned]
    driver = GraphDatabase.driver(os.environ["NEO4J_URI"], auth=(os.environ["NEO4J_USERNAME"], os.environ["NEO4J_PASSWORD"]))
    with driver.session(database=os.environ.get("NEO4J_DATABASE", "neo4j")) as s:
        s.run("MATCH (n) WHERE n.recordGroupId = $g OR n.id IN $ids OR n.recordId IN $ids DETACH DELETE n",
              g=group_id, ids=ids).consume()
        left = s.run("MATCH (n) WHERE n.recordGroupId = $g OR n.id IN $ids OR n.recordId IN $ids RETURN count(n) AS c",
                     g=group_id, ids=ids).single()["c"]
    driver.close()
    assert left == 0, f"warm-up left {left} nodes"


async def run(repo_dir: Path, key_dir: Path | None, concurrency: int = 1, out: Path | None = None,
              warm: bool = False) -> dict:
    name = repo_dir.name
    connector_id, group_id, user_id = f"conn-{name}", f"group-{name}", f"bench-{name}"
    graph = await connect()
    files = read_tree(repo_dir)
    timings = {}
    # Modules the stages below import lazily, imported before any timer starts: the
    # indexer has them loaded long before a repo arrives, and document_extraction
    # (imported only for the LLM-cost estimate) pulls in the chat-model stack, ~4 s.
    import app.modules.code_graph.block_projection  # noqa: F401
    import app.modules.code_graph.edge_builder  # noqa: F401
    import app.modules.parsers.code_parser  # noqa: F401
    import app.modules.parsers.code_parser.classification_policy  # noqa: F401
    import app.modules.transformers.document_extraction  # noqa: F401
    import app.modules.transformers.vectorstore  # noqa: F401
    if warm:
        await warm_up(graph, concurrency)

    started = time.perf_counter()
    planned, routes = plan_files(name, files)
    timings["plan_seconds"] = time.perf_counter() - started

    started = time.perf_counter()
    await write_records(graph, planned, connector_id, group_id)
    timings["records_seconds"] = time.perf_counter() - started

    blobs = Blobs()
    started = time.perf_counter()
    stats = await project(graph, planned, connector_id, group_id, blobs, concurrency)
    timings["parse_project_embedtext_seconds"] = time.perf_counter() - started
    timings["parse_project_wall_seconds"] = timings["parse_project_embedtext_seconds"] - stats["embed_seconds"]
    for k in ("parse_seconds", "project_seconds", "embed_seconds"):
        timings[k] = stats.pop(k)

    from app.modules.code_graph.edge_builder import build_code_graph_edges

    started = time.perf_counter()
    result = await build_code_graph_edges(graph_provider=graph, facts_source=blobs, org_id=ORG,
                                          record_group_id=group_id)
    timings["edge_build_seconds"] = time.perf_counter() - started

    await grant(graph, user_id, connector_id, group_id, [f["rid"] for f in planned])
    tools = await tool_latency(graph, connector_id, user_id, stats.pop("symbols"))
    edges, counts = read_back(group_id)
    if out is not None:  # for exact before/after comparisons of the edge set
        out.mkdir(parents=True, exist_ok=True)
        (out / "edges.jsonl").write_text("".join(sorted(json.dumps(e, sort_keys=True) + "\n" for e in edges)))
    # B1's rule: files with no record, and generated files, are left out by policy.
    recorded = {f["path"] for f in planned}
    by_policy = {p for p in files if p not in recorded} | {
        f["path"] for f in planned if f["route"].startswith("skip:generated")}
    accuracy = None
    if key_dir is not None:  # the large tier has no truth: scale only
        scores, handwritten = score_against_truth(key_dir, edges, by_policy)
        accuracy = {"overall": scores["overall"], "handwritten": handwritten["overall"]}
    if hasattr(graph, "disconnect"):
        await graph.disconnect()
    return {
        "repo": name, "files_in_tree": len(files), "records": len(planned), "file_routes": routes,
        **stats, "timings": {k: round(v, 2) for k, v in timings.items()},
        # The graph's own cost; building and tokenizing the embedding text is reported apart.
        # With files in flight together, parse and projection overlap: their wall clock counts, not their sum.
        "graph_seconds": round(sum(timings[k] for k in ("plan_seconds", "records_seconds", "edge_build_seconds"))
                               + (timings["parse_seconds"] + timings["project_seconds"] if concurrency == 1
                                  else timings["parse_project_wall_seconds"]), 2),
        "concurrency": concurrency, "warm": warm,
        "driver_peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "builder": {k: v for k, v in vars(result).items() if isinstance(v, (int, float, str))},
        "graph": counts, "tools": tools,
        "accuracy": accuracy,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--key", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--concurrency", type=int, default=1, help="code files in flight at once")
    ap.add_argument("--warm", action="store_true", help="index and delete a tiny repo first, untimed")
    args = ap.parse_args()
    logging.basicConfig(level=logging.WARNING)
    report = asyncio.run(run(args.repo, args.key, args.concurrency, args.out, args.warm))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    if QUERY_PROFILE:
        rows = sorted(((sum(v), len(v), k) for k, v in QUERY_PROFILE.items()), reverse=True)
        (args.out / "query_profile.txt").write_text("".join(f"{t:9.2f}s {n:7d}x  {k}\n" for t, n, k in rows))
    print(json.dumps({k: report[k] for k in ("repo", "records", "code_files", "graph_seconds", "timings",
                                              "graph", "embed_tokens", "llm_calls", "llm_input_tokens", "tools", "accuracy")}, indent=1))


if __name__ == "__main__":
    main()
