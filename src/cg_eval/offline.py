"""Run PipesHub's code-graph pipeline over a fixture checkout, in memory.

This mirrors the indexing path file by file: the connector creates a record
(both connectors: ``should_index_code_file``; GitHub also drops files the
repo's ``.gitattributes`` marks linguist-generated/vendored), indexing routes it
with ``plan_code_file``, parses code with the real ``CodeFileParser``, projects
blocks with ``write_code_file_blocks_to_graph``, and the edge builder runs over
the repo. Only the database is replaced, by ``MemGraph``.
"""
from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path

from .memgraph import BLOCKS, RECORDS, FileRow, MemGraph

ORG = "org-eval"
UNNAMED_KINDS = frozenset({"imports", "header", "statements", "file_summary", "comment", "filler"})


@dataclass
class FileOutcome:
    path: str
    record_id: str | None
    route: str            # no-record | code | text | delimited | structured | skip:<cause> | empty
    blocks: int = 0
    parse_seconds: float = 0.0


@dataclass
class IndexedRepo:
    name: str
    connector_id: str
    group_id: str
    outcomes: dict[str, FileOutcome] = field(default_factory=dict)
    # The bytes indexing actually read, after secret redaction (S-2).
    indexed: dict[str, bytes] = field(default_factory=dict)


def _path_kwarg(path: str) -> dict:
    """PipesHub before 2026.10.19 has no file_path on the projection context."""
    import dataclasses  # noqa: PLC0415

    from app.modules.code_graph.block_projection import BlockProjectionContext  # noqa: PLC0415

    names = {f.name for f in dataclasses.fields(BlockProjectionContext)}
    return {"file_path": path} if "file_path" in names else {}


def read_tree(root: Path) -> dict[str, bytes]:
    """Every file under *root* except .git; a symlink yields its target, as GitHub's API does."""
    files: dict[str, bytes] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != ".git"]
        for name in filenames:
            full = Path(dirpath) / name
            rel = full.relative_to(root).as_posix()
            files[rel] = os.readlink(full).encode() if full.is_symlink() else full.read_bytes()
    return files


def record_id_for(repo: str, path: str) -> str:
    import hashlib

    return "rec-" + hashlib.sha1(f"{repo}:{path}".encode()).hexdigest()[:16]


async def index_files(graph: MemGraph, repo: IndexedRepo, files: dict[str, bytes], *,
                      connector: str = "github", only: set[str] | None = None) -> None:
    """Create records and project blocks for *files* (or the subset *only*)."""
    from app.modules.code_graph.block_projection import (
        BlockProjectionContext,
        write_code_file_blocks_to_graph,
    )
    from app.modules.parsers.code_parser import CodeFileParser
    from app.modules.parsers.code_parser.file_role import classify_file_role, should_index_code_file
    from app.modules.parsers.code_parser.routing import CodeFileRoute, plan_code_file

    linguist = None
    if connector == "github":
        try:  # added with P-1; older checkouts ingest every GitHub file
            from app.modules.parsers.code_parser.gitattributes import parse_linguist_exclusions
            linguist = parse_linguist_exclusions(files.get(".gitattributes", b"").decode("utf-8", "replace"))
        except ImportError:
            linguist = None

    try:  # added with S-2; older checkouts index the raw bytes
        from app.modules.parsers.code_parser.secret_scan import redact_secrets_bytes
    except ImportError:
        def redact_secrets_bytes(raw):  # noqa: ANN001, ANN202
            return raw, None

    parser = CodeFileParser()
    for path in sorted(only if only is not None else files):
        content = files[path]
        if connector == "gitlab" or linguist is not None:
            keep = should_index_code_file(path)[0] and not (linguist and linguist.excludes(path))
            if not keep:
                repo.outcomes[path] = FileOutcome(path, None, "no-record")
                continue
        content, _ = redact_secrets_bytes(content)
        repo.indexed[path] = content
        rid = record_id_for(repo.name, path)
        name = path.rsplit("/", 1)[-1]
        ext = name.rsplit(".", 1)[-1] if "." in name else None
        graph.add_record(FileRow(rid, path, repo.name, repo.connector_id, repo.group_id, content,
                                 None, classify_file_role(path).value))
        plan = plan_code_file(name, path, ext, content, repository_file=True)
        if plan.route is CodeFileRoute.SKIP:
            await graph.delete_blocks_for_records([rid])
            repo.outcomes[path] = FileOutcome(path, rid, f"skip:{plan.skip_cause.value}")
            continue
        if plan.route is not CodeFileRoute.CODE:
            repo.outcomes[path] = FileOutcome(path, rid, plan.route.value)
            continue
        started = time.perf_counter()
        container = parser.parse_to_blocks(content, name, path, plan.parser)
        elapsed = time.perf_counter() - started
        if container is None:
            # Production reads a file past the block cap as text (P-4).
            await _drop_blocks(graph, rid)
            repo.outcomes[path] = FileOutcome(path, rid, "text", parse_seconds=elapsed)
            continue
        if not container.blocks and not container.block_groups:
            # As production does since the R-9 fix: an emptied file loses its blocks.
            await _drop_blocks(graph, rid)
            repo.outcomes[path] = FileOutcome(path, rid, "empty", parse_seconds=elapsed)
            continue
        virtual = graph.records[rid]["virtualRecordId"]
        graph.blobs[virtual] = {"block_containers": container.model_dump(mode="json", exclude_none=True)}
        await write_code_file_blocks_to_graph(
            graph_provider=graph,
            context=BlockProjectionContext(org_id=ORG, record_id=rid, record_group_id=repo.group_id,
                                           connector_id=repo.connector_id, language=plan.parser,
                                           **_path_kwarg(path)),
            block_containers=container,
        )
        n = sum(1 for b in graph.blocks.values() if b.get("recordId") == rid)
        repo.outcomes[path] = FileOutcome(path, rid, "code", blocks=n, parse_seconds=elapsed)


async def _drop_blocks(graph: MemGraph, rid: str) -> None:
    try:  # added with R-3: also records the names the file stopped defining
        from app.modules.code_graph.block_projection import drop_code_file_blocks
    except ImportError:
        await graph.delete_blocks_for_records([rid])
        return
    await drop_code_file_blocks(graph, rid)


def deletion_asks_for_full_build() -> bool:
    """Since R-3 a deleted code file asks for a full build; before, nothing."""
    import importlib

    from app.modules.code_graph import edge_build_trigger

    if hasattr(edge_build_trigger, "full_build_still_owed"):
        return True
    try:  # moved there with the build-state store (phase 1)
        return hasattr(importlib.import_module("app.modules.code_graph.build_state"),
                       "full_build_still_owed")
    except ImportError:
        return False


async def remove_files(graph: MemGraph, repo: IndexedRepo, paths: set[str]) -> None:
    for path in paths:
        rid = record_id_for(repo.name, path)
        await graph.delete_blocks_for_records([rid])
        graph.records.pop(rid, None)
        graph.code_files.pop(rid, None)
        repo.outcomes.pop(path, None)


async def build_edges(graph: MemGraph, repo: IndexedRepo, touched: set[str] | None = None):
    from app.modules.code_graph.edge_builder import build_code_graph_edges

    started = time.perf_counter()
    result = await build_code_graph_edges(graph_provider=graph, facts_source=graph, org_id=ORG,
                                          record_group_id=repo.group_id,
                                          touched_record_ids=touched)
    return result, time.perf_counter() - started


def endpoint(graph: MemGraph, node_id: str) -> str | None:
    """``path`` or ``path::kind:Qualified``, in the answer key's notation."""
    collection, _, key = node_id.partition("/")
    if collection == RECORDS:
        return graph.path_of(key)
    if collection != BLOCKS or key not in graph.blocks:
        return None
    block = graph.blocks[key]
    path = graph.path_of(block.get("recordId"))
    q = block.get("qualifiedName") or ""
    kind, _, tail = q.partition(":")
    if not tail or kind in UNNAMED_KINDS or tail.startswith("L") and tail[1:2].isdigit() \
            or tail == "anonymous":
        return path
    return f"{path}::{q}"


def code_edges(graph: MemGraph, group_id: str | None = None) -> list[dict]:
    """The builder's edges (``source == code_graph``) in answer-key notation."""
    out = []
    for e in graph.edges:
        if e.get("source") != "code_graph":
            continue
        src, dst = endpoint(graph, e["_from"]), endpoint(graph, e["_to"])
        if src is None or dst is None:
            continue
        if group_id is not None:
            rec = _record_of(graph, e["_from"])
            if rec is not None and graph.records.get(rec, {}).get("recordGroupId") != group_id:
                continue
        out.append({"rel": e["relationshipType"], "from": src, "to": dst,
                    "confidence": e.get("confidence")})
    return out


def _record_of(graph: MemGraph, node_id: str) -> str | None:
    collection, _, key = node_id.partition("/")
    if collection == RECORDS:
        return key
    block = graph.blocks.get(key)
    return block.get("recordId") if block else None
