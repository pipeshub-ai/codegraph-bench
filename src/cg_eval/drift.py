"""Incremental vs full builds across the mutation steps (R-3, R-4, R-9).

    python -m cg_eval.drift

For each step, the incremental graph is updated the way production updates it:
deleted files lose their record and blocks, added and changed files are
re-projected, and the edge builder runs with ``touched_record_ids`` set to the
added and changed records (a deletion leaves nothing to touch). The result is
compared with a fresh full build of the same tree and with the answer key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get("PIPESHUB_PY", str(ROOT.parent / "pipeshub-code-graph/backend/python")))
sys.path.insert(0, str(ROOT))

from fixture_gen.domains import STEPS  # noqa: E402
from fixture_gen.generate import MAIN, build_main, write  # noqa: E402

from .memgraph import MemGraph  # noqa: E402
from .offline import (  # noqa: E402
    ORG, IndexedRepo, build_edges, code_edges, deletion_asks_for_full_build, index_files, read_tree,
    record_id_for, remove_files,
)
from .score import load_key, score_edges  # noqa: E402

CONNECTOR = "conn-github"


def _edge_set(graph: MemGraph, group: str) -> set[tuple]:
    return {(e["rel"], e["from"], e["to"]) for e in code_edges(graph, group)}


async def full_build(files: dict[str, bytes]) -> tuple[MemGraph, IndexedRepo]:
    graph = MemGraph(ORG)
    repo = IndexedRepo(MAIN, CONNECTOR, f"group-{MAIN}")
    await index_files(graph, repo, files)
    await build_edges(graph, repo)
    return graph, repo


async def run(args) -> dict:
    trees = {}
    for step in STEPS:
        tree = args.work / f"step-{step}" / MAIN
        write(build_main(step), tree, args.key / f"step-{step}" / MAIN)
        trees[step] = read_tree(tree)

    inc_graph, inc_repo = await full_build(trees[0])
    rows = []
    for step in sorted(STEPS)[1:]:
        before, after = trees[step - 1], trees[step]
        removed = set(before) - set(after)
        added = set(after) - set(before)
        changed = {p for p in set(before) & set(after) if before[p] != after[p]}
        await remove_files(inc_graph, inc_repo, removed)
        await index_files(inc_graph, inc_repo, after, only=added | changed)
        touched = {record_id_for(MAIN, p) for p in added | changed}
        # As the runner decides: a deletion asks for a full build (R-3).
        if removed and deletion_asks_for_full_build():
            touched = None
        visible = _watch_edge_count(inc_graph, inc_repo.group_id)
        started = time.perf_counter()
        result, _ = await build_edges(inc_graph, inc_repo, touched=touched)
        inc_seconds = time.perf_counter() - started

        full_graph, full_repo = await full_build(after)
        inc = _edge_set(inc_graph, inc_repo.group_id)
        full = _edge_set(full_graph, full_repo.group_id)
        key = load_key(args.key / f"step-{step}" / MAIN)
        inc_score = score_edges(key, code_edges(inc_graph, inc_repo.group_id))["overall"]
        full_score = score_edges(key, code_edges(full_graph, full_repo.group_id))["overall"]
        # The scorer B3b uses for every other tool, so the tables compare.
        from .real import score_against_truth

        def b3b(graph, group):
            key_dir = args.key / f"step-{step}" / MAIN
            left_out = {f["path"] for f in map(json.loads, (key_dir / "files.jsonl").read_text().splitlines())
                        if not f.get("expect_index", True)}
            s, h = score_against_truth(key_dir, code_edges(graph, group), left_out,
                                       {"CALLS", "IMPORTS", "IMPORTS_FROM", "HERITAGE"})
            return {"overall": s["overall"], "handwritten": h["overall"],
                    "without_tests": h["without_tests"]["overall"]}
        stale_blocks = sum(1 for b in inc_graph.blocks.values()
                           if inc_graph.path_of(b.get("recordId")) in {p for p in after if not after[p]})
        rows.append({
            "step": step, "change": STEPS[step],
            "files": {"added": len(added), "changed": len(changed), "removed": len(removed)},
            "only_in_incremental": sorted(map(list, inc - full))[:25],
            "only_in_full": sorted(map(list, full - inc))[:25],
            "drift": {"only_incremental": len(inc - full), "only_full": len(full - inc)},
            "incremental_vs_key": inc_score, "full_vs_key": full_score,
            "b3b": {"incremental": b3b(inc_graph, inc_repo.group_id), "fresh": b3b(full_graph, full_repo.group_id)},
            "stale_blocks_in_emptied_files": stale_blocks,
            "incremental_build_seconds": round(inc_seconds, 3),
            "build": "full" if touched is None else "incremental",
            "files_reresolved": result.files_reresolved, "files_total": result.files_total,
            # What a reader could see between the build's writes (R-8).
            "edges_before_build": visible[0], "min_edges_visible_during_build": min(visible),
        })
    return {"steps": rows}


def _watch_edge_count(graph, group_id: str) -> list[int]:
    """Code edge counts before the build and after each of its edge writes."""
    counts = [len(_edge_set(graph, group_id))]
    for name in ("batch_upsert_record_relations", "delete_edges_by_source_keys"):
        original = getattr(graph, name)

        async def watched(*a, _original=original, **k):
            out = await _original(*a, **k)
            counts.append(len(_edge_set(graph, group_id)))
            return out
        setattr(graph, name, watched)
    return counts


def summarise(report: dict) -> str:
    lines = ["# Incremental vs full builds", "",
             "| Step | Change | Files +/~/- | Build | Re-resolved | Only incremental | Only full | Inc. P / R vs key | Full P / R vs key | Stale blocks | Edges visible mid-build (min / before) |",
             "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
    for r in report["steps"]:
        f, i, fu = r["files"], r["incremental_vs_key"], r["full_vs_key"]
        lines.append(f"| {r['step']} | {r['change']} | {f['added']}/{f['changed']}/{f['removed']} | "
                     f"{r['build']} | {r['files_reresolved']}/{r['files_total']} | "
                     f"{r['drift']['only_incremental']} | {r['drift']['only_full']} | "
                     f"{i['precision']:.1%} / {i['recall']:.1%} | {fu['precision']:.1%} / {fu['recall']:.1%} | "
                     f"{r['stale_blocks_in_emptied_files']} | "
                     f"{r.get('min_edges_visible_during_build', '-')} / {r.get('edges_before_build', '-')} |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--work", type=Path, default=ROOT / "outputs" / "steps")
    ap.add_argument("--key", type=Path, default=ROOT / "gold")
    ap.add_argument("--out", type=Path, default=ROOT / "reports" / time.strftime("drift-%Y%m%d-%H%M%S"))
    args = ap.parse_args()
    report = asyncio.run(run(args))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "drift.json").write_text(json.dumps(report, indent=2))
    text = summarise(report)
    (args.out / "summary.md").write_text(text)
    print(text)
    print(f"full report: {args.out}/drift.json")


if __name__ == "__main__":
    main()
