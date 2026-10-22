"""B3b: edge accuracy after incremental updates, for the tools that update incrementally.

  python incremental.py [graphify cbm cgc]   # default: all three

For each tool, a clone of the fixture copy at step 0 gets a full build. Then for steps
1..7 the clone is checked out to the step and the tool updates incrementally, the way
it does in use:
  graphify  the post-commit hook's rebuild (`_rebuild_code` with the changed paths),
            with --no-cluster's export, its most exact one
  cbm       `index_repository` again on the same folder (mtime/size change detection)
  cgc       the watcher's per-file handlers (`cgc watch` without the filesystem thread)
Each step's incremental graph is scored against that step's answer key, and so is a fresh
full build of the same tree; `drift` counts the edges where the two disagree.
Results append to reports/b3b/<tool>.jsonl.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
BENCH = HERE.parent
EVAL = BENCH.parent
sys.path.insert(0, str(EVAL / "src"))
from cg_eval.real import score_against_truth  # noqa: E402

FIXTURE = Path(os.environ["FIXTURE_DIR"])  # set by bench/env.sh
GOLD = EVAL / "gold"
WORK = BENCH / "work-b3b"
OUT = EVAL / "reports" / "b3b"
TOOLS = BENCH / "tools"
CGC_ENV = {"HOME": str(TOOLS / "cgc-home"), "CGC_RUNTIME_DB_TYPE": "kuzudb"}

_CGC_UPDATE = r"""
import sys
from pathlib import Path
from codegraphcontext.cli.cli_helpers import _initialize_services
from codegraphcontext.core.watcher import RepositoryEventHandler
repo, changed, removed = Path(sys.argv[1]), sys.argv[2].split("\n"), sys.argv[3].split("\n")
db, builder, _, _ = _initialize_services(None)
handler = RepositoryEventHandler(builder, repo, perform_initial_scan=False)
for p in filter(None, removed):
    handler._handle_removal(str(repo / p))
for p in filter(None, changed):
    handler._handle_modification(str(repo / p))
db.close_driver()
"""

_GRAPHIFY_UPDATE = r"""
import sys
from pathlib import Path
from graphify.watch import _rebuild_code
ok = _rebuild_code(Path("."), changed_paths=[Path(p) for p in sys.argv[1].split("\n") if p], no_cluster=True)
sys.exit(0 if ok else 3)
"""


def run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    subprocess.run(cmd, cwd=cwd, env={**os.environ, **(env or {})}, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)


def checkout(clone: Path, step: int) -> None:
    run(["git", "-C", str(clone), "checkout", "-q", f"step-{step}"])


def clone_at(dest: Path, step: int) -> Path:
    shutil.rmtree(dest, ignore_errors=True)
    run(["git", "clone", "-q", "--no-hardlinks", str(FIXTURE), str(dest)])
    checkout(dest, step)
    return dest


def changes(clone: Path, old: int, new: int) -> tuple[list[str], list[str]]:
    """(added or modified, deleted) paths between two steps; a rename is both."""
    out = subprocess.run(["git", "-C", str(clone), "diff", "--name-status", "-M", f"step-{old}", f"step-{new}"],
                         capture_output=True, text=True, check=True).stdout
    changed, removed = [], []
    for line in out.splitlines():
        status, *paths = line.split("\t")
        if status.startswith("R"):
            removed.append(paths[0])
            changed.append(paths[1])
        elif status == "D":
            removed.append(paths[0])
        else:
            changed.append(paths[0])
    return changed, removed


# -- per tool: full build, incremental update, edges ----------------------------

def full(tool: str, clone: Path) -> None:
    if tool == "graphify":
        shutil.rmtree(clone / "graphify-out", ignore_errors=True)
        run([str(TOOLS / "graphify/bin/graphify"), "update", ".", "--no-cluster"], cwd=clone)
    elif tool == "cbm":
        export("cbm", clone)  # its export indexes first; a new folder is a new project
    elif tool == "cgc":
        db = clone.with_suffix(".kuzu")
        shutil.rmtree(db, ignore_errors=True) if db.is_dir() else db.unlink(missing_ok=True)
        run([str(TOOLS / "cgc/bin/cgc"), "--db", "kuzudb", "--db-path", str(db), "index", str(clone),
             "--no-progress"], env=CGC_ENV)


def incremental(tool: str, clone: Path, changed: list[str], removed: list[str]) -> None:
    if tool == "graphify":
        run([str(TOOLS / "graphify/bin/python"), "-c", _GRAPHIFY_UPDATE, "\n".join(changed + removed)], cwd=clone)
    elif tool == "cbm":
        export("cbm", clone)  # index_repository on an indexed folder is its incremental path
    elif tool == "cgc":
        run([str(TOOLS / "cgc/bin/python"), "-c", _CGC_UPDATE, str(clone), "\n".join(changed), "\n".join(removed)],
            env={**CGC_ENV, "CGC_RUNTIME_DB_PATH": str(clone.with_suffix(".kuzu"))})


def export(tool: str, clone: Path) -> list[dict]:
    out = clone.with_suffix(".edges.jsonl")
    if tool == "graphify":
        run(["python3", str(BENCH / "adapters/graphify_edges.py"), str(clone / "graphify-out/graph.json"), str(out)])
    elif tool == "cbm":
        run(["python3", str(BENCH / "adapters/cbm_edges.py"), str(TOOLS / "cbm-012/codebase-memory-mcp"),
             str(clone), str(out)], env={"HOME": str(TOOLS / "cbm-012/home"), "CBM_SHARED_METHODS": "drop"})
    elif tool == "cgc":
        run([str(TOOLS / "cgc/bin/python"), str(BENCH / "adapters/cgc_edges.py"), str(clone.with_suffix(".kuzu")),
             str(clone), str(out)])
    return [json.loads(line) for line in out.read_text().splitlines() if line.strip()]


# B1's relations, the ones every tool models; the fixture key also has EXPORTS, RE_EXPORTS, OVERRIDES.
RELATIONS = {"CALLS", "IMPORTS", "IMPORTS_FROM", "HERITAGE"}


def score(step: int, edges: list[dict], tag: str) -> dict:
    key = GOLD / f"step-{step}" / "pipeshub-codegraph-fixture"
    (OUT / "edges").mkdir(parents=True, exist_ok=True)
    (OUT / "edges" / f"{tag}-step{step}.jsonl").write_text("".join(json.dumps(e) + "\n" for e in edges))
    # The key's own verdict on what a code index should leave out (minified, generated, vendored, ...).
    left_out = {f["path"] for f in map(json.loads, (key / "files.jsonl").read_text().splitlines())
                if not f.get("expect_index", True)}
    scores, handwritten = score_against_truth(key, edges, left_out, RELATIONS)
    return {"overall": scores["overall"], "handwritten": handwritten["overall"],
            "without_tests": handwritten["without_tests"]["overall"]}


def edge_set(edges: list[dict]) -> set[tuple]:
    return {(e["rel"], e["from"], e["to"]) for e in edges}


def main() -> None:
    tools = sys.argv[1:] or ["graphify", "cbm", "cgc"]
    OUT.mkdir(parents=True, exist_ok=True)
    for tool in tools:
        inc = clone_at(WORK / tool / "incremental", 0)
        started = time.time()
        full(tool, inc)
        edges = export(tool, inc)
        rows = [{"tool": tool, "step": 0, "mode": "full", "seconds": round(time.time() - started, 1),
                 "edges": len(edges), "score": score(0, edges, f"{tool}-full")}]
        for step in range(1, 8):
            changed, removed = changes(inc, step - 1, step)
            checkout(inc, step)
            started = time.time()
            error = None
            try:
                incremental(tool, inc, changed, removed)
            except subprocess.CalledProcessError as exc:
                error = (exc.stderr or "")[-400:]
            seconds = round(time.time() - started, 1)
            inc_edges = export(tool, inc)
            fresh = clone_at(WORK / tool / f"fresh-{step}", step)
            full(tool, fresh)
            fresh_edges = export(tool, fresh)
            a, b = edge_set(inc_edges), edge_set(fresh_edges)
            rows.append({"tool": tool, "step": step, "changed": len(changed), "removed": len(removed),
                         "incremental_seconds": seconds, "error": error,
                         "incremental": score(step, inc_edges, f"{tool}-incremental"),
                         "fresh": score(step, fresh_edges, f"{tool}-fresh"),
                         "drift": {"only_incremental": len(a - b), "only_fresh": len(b - a)}})
            print(json.dumps({k: rows[-1][k] for k in ("tool", "step", "incremental_seconds", "drift", "error")}),
                  flush=True)
            shutil.rmtree(fresh, ignore_errors=True)
            for leftover in (fresh.with_suffix(".kuzu"), fresh.with_suffix(".edges.jsonl")):
                shutil.rmtree(leftover, ignore_errors=True) if leftover.is_dir() else leftover.unlink(missing_ok=True)
        with (OUT / f"{tool}.jsonl").open("w") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)


if __name__ == "__main__":
    main()
