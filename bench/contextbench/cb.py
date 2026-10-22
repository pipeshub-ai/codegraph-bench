"""ContextBench (verified, 500 tasks): does PipesHub retrieve the context an issue's fix needs?

  python cb.py retrieve [--only ID ...]           # no LLM: BM25, and BM25 + PipesHub graph signals
  python cb.py agent --arm search|graph [--shard i/n]   # llm_1 gathers context, as B5b localizes
  python cb.py score                              # the official scorer over every prediction

Each task's repo is read at its base commit from a bare clone (clone_repos.sh). Predictions are
written in ContextBench's unified format (traj_data: pred_steps, pred_files, pred_spans) and scored
by its own `contextbench.evaluate` (file, symbol, span and line coverage and precision).

Retrieval tier: the files PipesHub indexes as code (tests left out, as in B5a), ranked by BM25 and
by BM25 fused with the graph seeds B5a uses (weights as tuned there: alpha 0.2, beta 0); the top 5
files are the final context. Agent tier: the B5b loop (grep, read_file, list_dir, + PipesHub's
code-graph tools in the graph arm), with a prompt asking for the context, not a fix; every
read_file and read_code is a trajectory step, and `submit` gives the final line ranges.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
import time
from pathlib import Path

import pyarrow.parquet as pq

HERE = Path(__file__).resolve().parent
EVAL = HERE.parents[1]
sys.path.insert(0, str(EVAL / "src"))
from cg_eval.agentloc import BASE_TOOLS, LLM, MAX_TOOL_CHARS, USER, Workspace, _fn, graph_tool_schemas  # noqa: E402
from cg_eval.agentqa import settle_as_built  # noqa: E402
from cg_eval.memgraph import MemBlobStore, MemGraph  # noqa: E402
from cg_eval.offline import ORG, IndexedRepo, build_edges, index_files  # noqa: E402
from cg_eval.run import CONNECTOR  # noqa: E402
from cg_eval.swebench import bm25_scores, graph_features, ranking, seeds_v2  # noqa: E402

DATA = HERE / "ContextBench" / "Context-Bench" / "data" / "contextbench_verified.parquet"
OUT = EVAL / "reports" / "contextbench"
MAX_TURNS = 30
TOP_FILES = 5
SYSTEM = (
    "You gather the code context needed to resolve a GitHub issue. Investigate the repository "
    "with the tools, then call submit with the line ranges a developer must read to write the "
    "fix: the code to change and the code it depends on. Include what is needed and little "
    "else. Do not write a fix. Be efficient: stop once you are confident."
)
SUBMIT = _fn("submit", "Finish with the context: line ranges of repository files.",
             {"context": {"type": "array", "items": {"type": "object", "properties": {
                 "file": {"type": "string"}, "start_line": {"type": "integer"},
                 "end_line": {"type": "integer"}}, "required": ["file", "start_line", "end_line"]}}},
             ["context"])


def tasks() -> list[dict]:
    return pq.read_table(DATA).to_pylist()


def tree_at(repo: str, commit: str) -> dict[str, bytes]:
    git = HERE / "git" / f"{repo.replace('/', '__')}.git"
    listing = subprocess.run(["git", "-C", str(git), "ls-tree", "-r", "-z", commit],
                             capture_output=True, check=True).stdout.split(b"\0")
    entries = [(e.split(b"\t", 1)[1].decode(), e.split()[2].decode()) for e in listing
               if e and e.split()[1] == b"blob"]
    proc = subprocess.run(["git", "-C", str(git), "cat-file", "--batch"],
                          input="".join(sha + "\n" for _, sha in entries).encode(),
                          capture_output=True, check=True).stdout
    files, pos = {}, 0
    for path, _ in entries:
        header_end = proc.index(b"\n", pos)
        size = int(proc[pos:header_end].split()[2])
        files[path] = proc[header_end + 1:header_end + 1 + size]
        pos = header_end + 1 + size + 1
    return files


async def graph_for(task: dict, files: dict[str, bytes]) -> tuple[MemGraph, IndexedRepo]:
    graph = MemGraph(ORG, search_backend="neo4j")
    repo = IndexedRepo(task["repo"].split("/")[-1], CONNECTOR, f"group-{task['instance_id']}")
    await index_files(graph, repo, files)
    await build_edges(graph, repo)
    await settle_as_built(graph, repo.group_id)
    return graph, repo


def pred(task: dict, steps: list[dict], files: list[str], spans: dict[str, list[dict]]) -> dict:
    return {"instance_id": task["instance_id"], "repo_url": task["repo_url"], "commit": task["base_commit"],
            "traj_data": {"pred_steps": steps, "pred_files": files, "pred_spans": spans}, "model_patch": ""}


# -- retrieval tier -----------------------------------------------------------

async def retrieve_one(task: dict) -> dict:
    from app.modules.parsers.code_parser.file_role import FileRole, classify_file_role

    started = time.perf_counter()
    files = tree_at(task["repo"], task["base_commit"])
    graph, repo = await graph_for(task, files)
    code = {o.path for o in repo.outcomes.values()
            if o.route == "code" and classify_file_role(o.path) is not FileRole.TEST}
    query = task["problem_statement"]
    feats = graph_features(graph, repo.group_id, code)
    graph_signals = seeds_v2(query, feats, code)
    # ranking() reads "seed"/"hop"; seeds_v2 names them seed2/hop2 (B5a's fuse2 renames them the same way).
    signals = {"bm25": bm25_scores(query, {p: repo.indexed[p].decode("utf-8", "replace") for p in code}),
               "seed": graph_signals["seed2"], "hop": graph_signals["hop2"]}
    out = {}
    # alpha/beta as B5a chose them on SWE-bench Lite for bm25 + graph v2, not tuned on ContextBench.
    for name, (alpha, beta) in {"bm25": (0.0, 0.0), "bm25+graph": (0.25, 0.0)}.items():
        top = ranking(signals, ("bm25",), alpha, beta)[:TOP_FILES]
        out[name] = pred(task, [{"files": top, "spans": {}}], top, {})
    return {"preds": out, "signals": signals, "code_files": len(code),
            "seconds": round(time.perf_counter() - started, 1)}


def retrieve(only: list[str] | None, shard: str = "0/1") -> None:
    index, count = map(int, shard.split("/"))
    for n, t in enumerate(tasks()):
        if n % count != index or (only and t["instance_id"] not in only):
            continue
        if all((OUT / "retrieval" / m / f"{t['instance_id']}.json").exists() for m in ("bm25", "bm25+graph")) \
                and (OUT / "retrieval-signals" / f"{t['instance_id']}.json").exists():
            continue
        try:
            r = asyncio.run(retrieve_one(t))
        except Exception as exc:  # reported, not hidden
            print("error", t["instance_id"], repr(exc)[:300], flush=True)
            continue
        for mode, p in r["preds"].items():
            path = OUT / "retrieval" / mode / f"{t['instance_id']}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(p))
        sig = OUT / "retrieval-signals" / f"{t['instance_id']}.json"  # re-rank later without rebuilding graphs
        sig.parent.mkdir(parents=True, exist_ok=True)
        sig.write_text(json.dumps(r["signals"]))
        print(t["instance_id"], t["language"], r["code_files"], r["seconds"], flush=True)


# -- agent tier ---------------------------------------------------------------

def _span_of(name: str, args: dict, result: str) -> tuple[str, int, int] | None:
    """The file range a read showed, for the trajectory."""
    if name == "read_file" and args.get("path"):
        start = int(args.get("start") or 1)
        return args["path"], start, int(args.get("end") or start + 199)
    if name == "read_code":
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return None
        sym = data.get("symbol") if isinstance(data, dict) else None
        sym = sym if isinstance(sym, dict) else data if isinstance(data, dict) else {}
        if sym.get("file_path") and sym.get("start_line"):
            return sym["file_path"], int(sym["start_line"]), int(sym.get("end_line") or sym["start_line"])
    return None


async def agent_one(task: dict, arm: str, llm: LLM) -> dict:
    started = time.perf_counter()
    files = tree_at(task["repo"], task["base_commit"])
    tools, toolset = list(BASE_TOOLS[:-1]), None
    if arm == "graph":
        from app.agents.actions.code_graph.code_graph import CodeGraph

        graph, _ = await graph_for(task, files)
        toolset = CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph, "blob_store": MemBlobStore(graph)})
        tools += graph_tool_schemas()
    tools.append(SUBMIT)
    ws = Workspace(files, toolset)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Repository: {task['repo']}\n\nIssue:\n{task['problem_statement']}"}]
    steps, usage, tool_calls, context, nudged, turns = [], {"prompt": 0, "completion": 0}, {}, None, False, 0
    while turns < MAX_TURNS and context is None:
        turns += 1
        r = await llm(messages, tools)
        usage["prompt"] += r.usage.prompt_tokens
        usage["completion"] += r.usage.completion_tokens
        msg = r.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Call submit with the context."})
            continue
        for call in msg.tool_calls:
            name = call.function.name
            tool_calls[name] = tool_calls.get(name, 0) + 1
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "submit":
                context = [c for c in args.get("context", []) if isinstance(c, dict) and c.get("file")]
                content = "submitted"
            else:
                try:
                    content = await ws.call(name, args)
                except Exception as exc:  # a tool error goes back to the agent, as in production
                    content = f"tool error: {type(exc).__name__}: {exc}"[:500]
                span = _span_of(name, args, content)
                if span:
                    steps.append({"files": [span[0]], "spans": {span[0]: [{"start": span[1], "end": span[2]}]}})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content[:MAX_TOOL_CHARS]})
    spans: dict[str, list[dict]] = {}
    for c in context or []:
        try:
            spans.setdefault(c["file"], []).append({"start": int(c["start_line"]), "end": int(c["end_line"])})
        except (KeyError, TypeError, ValueError):
            continue
    p = pred(task, steps, list(spans), spans)
    p["meta"] = {"arm": arm, "turns": turns, "tool_calls": tool_calls, "usage": usage, "gave_up": context is None,
                 "seconds": round(time.perf_counter() - started, 1)}
    return p


def agent(arm: str, shard: str) -> None:
    index, count = map(int, shard.split("/"))
    llm = LLM()
    for n, t in enumerate(tasks()):
        if n % count != index:
            continue
        path = OUT / "agent" / arm / f"{t['instance_id']}.json"
        if path.exists():
            continue
        try:
            p = asyncio.run(agent_one(t, arm, llm))
        except Exception as exc:  # reported, not hidden
            p = {"instance_id": t["instance_id"], "error": repr(exc)[:500]}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(p))
        print(t["instance_id"], arm, p.get("meta", {}).get("turns"), "error" if "error" in p else "", flush=True)


# -- scoring ------------------------------------------------------------------

def score() -> None:
    """Every prediction set through the official scorer (its own Python 3.11 venv)."""
    bench = HERE / "ContextBench" / "Context-Bench"
    for preds in sorted(p for p in (OUT / "retrieval").glob("*") if p.is_dir()) + \
            sorted(p for p in (OUT / "agent").glob("*") if p.is_dir()):
        merged = OUT / "scored" / f"{preds.parent.name}-{preds.name}.jsonl"
        merged.parent.mkdir(parents=True, exist_ok=True)
        rows = [json.loads(f.read_text()) for f in sorted(preds.glob("*.json"))]
        rows = [r for r in rows if "error" not in r]
        inp = merged.with_suffix(".pred.jsonl")
        inp.write_text("".join(json.dumps(r) + "\n" for r in rows))
        subprocess.run([str(HERE / ".venv311" / "bin" / "python"), "-m", "contextbench.evaluate",
                        "--gold", str(bench / "data" / "contextbench_verified.parquet"), "--pred", str(inp),
                        "--cache", str(HERE / "repos"), "--out", str(merged)],
                       cwd=bench, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("scored", merged.name, len(rows), flush=True)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("retrieve")
    r.add_argument("--only", nargs="*")
    r.add_argument("--shard", default="0/1")
    a = sub.add_parser("agent")
    a.add_argument("--arm", choices=("search", "graph"), required=True)
    a.add_argument("--shard", default="0/1")
    sub.add_parser("score")
    args = ap.parse_args()
    if args.cmd == "retrieve":
        retrieve(args.only, args.shard)
    elif args.cmd == "agent":
        agent(args.arm, args.shard)
    else:
        score()


if __name__ == "__main__":
    main()
