"""Offline eval: index the fixture with PipesHub's real pipeline, then score it.

    python -m cg_eval.run --step 0
    python -m cg_eval.run --step 0 --connector gitlab --search arango

Needs PipesHub's backend on the path: set PIPESHUB_PY to ``backend/python`` of the
checkout under test, and run with that checkout's virtualenv.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, os.environ.get("PIPESHUB_PY", str(ROOT.parent / "pipeshub-code-graph/backend/python")))

from .memgraph import MemGraph  # noqa: E402
from .offline import ORG, IndexedRepo, build_edges, code_edges, index_files, read_tree  # noqa: E402
from .score import load_key, score_canaries, score_edges, score_files, score_symbols  # noqa: E402

MAIN = "pipeshub-codegraph-fixture"
ADMIN = "pipeshub-codegraph-fixture-admin"
CONNECTOR = "conn-github"


async def evaluate(args) -> dict:
    graph = MemGraph(ORG, search_backend=args.search)
    repos = {}
    timings = {}
    for name in (MAIN, ADMIN):
        tree = args.fixtures / name
        if not tree.exists():
            continue
        repo = IndexedRepo(name, CONNECTOR, f"group-{name}")
        files = read_tree(tree)
        started = time.perf_counter()
        await index_files(graph, repo, files, connector=args.connector)
        timings[f"{name}.index_seconds"] = round(time.perf_counter() - started, 2)
        result, seconds = await build_edges(graph, repo)
        timings[f"{name}.edge_build_seconds"] = round(seconds, 2)
        repos[name] = (repo, files, result)

    report: dict = {"step": args.step, "connector": args.connector, "search_backend": args.search,
                    "timings": timings, "repos": {}}
    for name, (repo, files, result) in repos.items():
        key = load_key(args.key / f"step-{args.step}" / name)
        produced = code_edges(graph, repo.group_id)
        blocks_by_path: dict[str, set[str]] = defaultdict(set)
        for b in graph.blocks.values():
            rec = graph.records.get(b.get("recordId"))
            if rec and rec["recordGroupId"] == repo.group_id and b.get("qualifiedName"):
                blocks_by_path[graph.path_of(b["recordId"])].add(b["qualifiedName"].partition(":")[2])
        roles = {graph.path_of(rid): doc.get("fileRole") for rid, doc in graph.code_files.items()
                 if graph.records[rid]["recordGroupId"] == repo.group_id}
        indexed_text = {p: repo.indexed.get(p, files[p]).decode("utf-8", "replace") for p, o in repo.outcomes.items()
                        if o.route in ("code", "text", "delimited", "structured")}
        heavy = sorted(repo.outcomes.values(), key=lambda o: -o.blocks)[:5]
        report["repos"][name] = {
            "builder": {k: v for k, v in vars(result).items() if isinstance(v, (int, float, str))},
            "edges": score_edges(key, produced),
            "symbols": score_symbols(key, blocks_by_path),
            "files": score_files(key, repo.outcomes, roles),
            "canaries": score_canaries(key, indexed_text),
            "heaviest_files": [{"path": o.path, "blocks": o.blocks,
                                "parse_seconds": round(o.parse_seconds, 3)} for o in heavy],
        }
    if args.tools:
        from .tools import run_tool_cases

        from .external import run_external_cases

        report["tools"] = await run_tool_cases(graph, repos, args)
        report["tools"] += await run_external_cases(graph, repos, args)
    return report


def summarise(report: dict) -> str:
    def pct(x):
        return "—" if x is None else f"{100 * x:.1f}%"

    lines = [f"# Offline eval, step {report['step']} ({report['connector']} connector, "
             f"{report['search_backend']} search)", ""]
    for name, r in report["repos"].items():
        e = r["edges"]
        lines += [f"## {name}", "",
                  f"Edges: precision {pct(e['overall']['precision'])}, recall {pct(e['overall']['recall'])} "
                  f"(tp {e['overall']['tp']}, fp {e['overall']['fp']}, fn {e['overall']['fn']})", "",
                  "| Relation | Precision | Recall | TP | FP | FN |", "| --- | --- | --- | --- | --- | --- |"]
        for rel, m in e["by_relation"].items():
            lines.append(f"| {rel} | {pct(m['precision'])} | {pct(m['recall'])} | {m['tp']} | {m['fp']} | {m['fn']} |")
        lines += ["", "| Language | Precision | Recall | TP | FP | FN |", "| --- | --- | --- | --- | --- | --- |"]
        for lang, m in e["by_language"].items():
            lines.append(f"| {lang} | {pct(m['precision'])} | {pct(m['recall'])} | {m['tp']} | {m['fp']} | {m['fn']} |")
        lines += ["", "Recall by category: " + ", ".join(
            f"{c} {pct(v)}" for c, v in e["recall_by_category"].items()), "",
            "Precision by confidence: " + ", ".join(
                f"{c} {pct(v)}" for c, v in e["precision_by_confidence"].items()), "",
            "Wrong edges by reason: " + ", ".join(f"{k} {v}" for k, v in e["false_positive_reasons"].items()), "",
            "Symbol recall: " + ", ".join(f"{k} {pct(v)}" for k, v in r["symbols"]["recall_by_language"].items()), ""]
        f = r["files"]
        lines += [f"Files indexed that should be skipped: {len(f['indexed_but_should_skip'])}; "
                  f"skipped that should be indexed: {len(f['skipped_but_should_index'])}; "
                  f"role accuracy {pct(f['role_accuracy'])}", ""]
        c = r["canaries"]
        lines += [f"Secret canaries reaching the index: {len(c['secret_leaks'])}; "
                  f"injection texts indexed: {len(c['injection_text_indexed'])}", ""]
    if "tools" in report:
        lines += ["## Tool cases", "", "| Case | Result | Detail |", "| --- | --- | --- |"]
        for case in report["tools"]:
            lines.append(f"| {case['id']} | {'PASS' if case['passed'] else 'FAIL'} | {case['detail']} |")
    lines.append("")
    lines.append("Timings: " + ", ".join(f"{k} {v}s" for k, v in report["timings"].items()))
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--step", type=int, default=0)
    ap.add_argument("--fixtures", type=Path, default=ROOT / "outputs" / "fixtures")
    ap.add_argument("--key", type=Path, default=ROOT / "gold")
    ap.add_argument("--connector", choices=("github", "gitlab"), default="github")
    ap.add_argument("--search", choices=("neo4j", "arango"), default="neo4j")
    ap.add_argument("--tools", action="store_true", help="also run the agent-tool cases")
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()

    report = asyncio.run(evaluate(args))
    out = args.out or ROOT / "reports" / time.strftime(f"step{args.step}-%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=2, default=str))
    summary = summarise(report)
    (out / "summary.md").write_text(summary)
    print(summary)
    print(f"full report: {out}/report.json")


if __name__ == "__main__":
    main()
