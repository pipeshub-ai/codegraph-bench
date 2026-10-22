"""B1/B2: index a real repository with PipesHub's pipeline and score it against SCIP truth.

    python -m cg_eval.real --repo ../bench/repos/requests --key ../bench/truth/requests --out ../reports/b1/requests

The key comes from bench/scip_truth.py. Only what the key covers is scored: its
relations (CALLS, IMPORTS, IMPORTS_FROM, heritage) and edges whose source file
the key lists, so PipesHub edges of other kinds or languages count neither way.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import resource
import time
from pathlib import Path

from .run import CONNECTOR  # noqa: F401  (sets PIPESHUB_PY on sys.path)
from .memgraph import MemGraph
from .offline import ORG, IndexedRepo, build_edges, code_edges, index_files, read_tree
from .score import _group, _path, load_key, score_edges

SCORED = {"CALLS", "IMPORTS", "IMPORTS_FROM", "HERITAGE"}
_REPEAT = re.compile(r"#\d+(?=\.|$)")


def _unrepeat(ep: str) -> str:
    """`f#3` -> `f`: PipesHub numbers a repeated definition (an @overload stub, a
    conditional def); SCIP has one symbol for all of them."""
    return _REPEAT.sub("", ep)


def _shadowed(ep: str, names: set[str]) -> bool:
    _, sep, q = ep.partition("::")
    return bool(sep) and q.partition(":")[2].split(".")[-1] in names


def score_against_truth(key_dir: Path, raw_edges: list[dict], by_policy: set[str],
                        relations: set[str] | None = None) -> tuple[dict, dict]:
    """Score any tool's edges (rel/from/to/confidence) against a SCIP key.

    Only the key's relations and source files count; repeated-definition
    suffixes are dropped, builtin-shadowing names are left out on both sides.
    The second result covers hand-written code only: edges touching a file
    PipesHub leaves out by policy (generated, dependency, credential).
    """
    key = load_key(key_dir)
    shadow_file = key_dir / "shadowed.json"
    shadowed = set(json.loads(shadow_file.read_text())) if shadow_file.exists() else set()
    keyed_files = {f["path"] for f in key["files"]}
    # Only the relations the truth covers for this language (Go: calls only).
    scored = SCORED & {_group(e["rel"]) for e in key["edges"]} & (relations or SCORED)
    produced = [{**e, "from": _unrepeat(e["from"]), "to": _unrepeat(e["to"])} for e in raw_edges
                if _group(e["rel"]) in scored and _path(e["from"]) in keyed_files]
    left_out = sum(_shadowed(e["to"], shadowed) for e in key["edges"]) + sum(
        _shadowed(e["to"], shadowed) for e in produced)
    # The key's own edges of an unscored relation (the fixture's EXPORTS, OVERRIDES) are no one's misses.
    key["edges"] = [e for e in key["edges"] if _group(e["rel"]) in scored and not _shadowed(e["to"], shadowed)]
    produced = [e for e in produced if not _shadowed(e["to"], shadowed)]
    scores = score_edges(key, produced)
    scores["left_out_shadowed"] = {"names": sorted(shadowed), "edges": left_out}

    def outside(e: dict) -> bool:
        return _path(e["from"]) not in by_policy and _path(e["to"]) not in by_policy

    kept = {**key, "edges": [e for e in key["edges"] if outside(e)]}
    handwritten = score_edges(kept, [e for e in produced if outside(e)])

    # The same, with test files also left out on both sides, by one path rule for every tool.
    from app.modules.code_graph.guards import is_test_path

    def no_tests(e: dict) -> bool:
        return outside(e) and not is_test_path(_path(e["from"])) and not is_test_path(_path(e["to"]))

    handwritten["without_tests"] = score_edges(
        {**key, "edges": [e for e in key["edges"] if no_tests(e)]}, [e for e in produced if no_tests(e)])
    return scores, handwritten


async def evaluate(repo_dir: Path, key_dir: Path) -> dict:
    graph = MemGraph(ORG, search_backend="neo4j")
    repo = IndexedRepo(repo_dir.name, CONNECTOR, f"group-{repo_dir.name}")
    files = read_tree(repo_dir)
    started = time.perf_counter()
    await index_files(graph, repo, files)
    index_seconds = time.perf_counter() - started
    result, build_seconds = await build_edges(graph, repo)

    by_policy = {o.path for o in repo.outcomes.values()
                 if o.route.startswith("skip:generated") or o.route == "no-record"}
    scores, handwritten = score_against_truth(key_dir, code_edges(graph, repo.group_id), by_policy)
    outcomes = {}
    for o in repo.outcomes.values():
        outcomes[o.route] = outcomes.get(o.route, 0) + 1
    return {
        "repo": repo_dir.name,
        "files_in_tree": len(files),
        "file_outcomes": outcomes,
        "blocks": len(graph.blocks),
        "timings": {"index_seconds": round(index_seconds, 2), "edge_build_seconds": round(build_seconds, 2)},
        "peak_rss_mb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024),
        "builder": {k: v for k, v in vars(result).items() if isinstance(v, (int, float, str))},
        "edges": scores,
        "files_left_out_by_policy": len(by_policy),
        "policy_files": sorted(by_policy),
        "handwritten": {"overall": handwritten["overall"], "by_relation": handwritten["by_relation"],
                        "precision_by_confidence": handwritten["precision_by_confidence"]},
        "without_tests": {"overall": handwritten["without_tests"]["overall"],
                          "by_relation": handwritten["without_tests"]["by_relation"]},
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    report = asyncio.run(evaluate(args.repo, args.key))
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    e = report["edges"]
    print(json.dumps({"repo": report["repo"], "overall": e["overall"], "by_relation": e["by_relation"],
                      "handwritten": report["handwritten"]["overall"],
                      "files_left_out_by_policy": report["files_left_out_by_policy"],
                      "timings": report["timings"], "peak_rss_mb": report["peak_rss_mb"],
                      "blocks": report["blocks"]}, indent=1))


if __name__ == "__main__":
    main()
