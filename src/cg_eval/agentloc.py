"""B5b: SWE-bench Lite file localization by an agent (llm_1), with and without PipesHub's code graph.

Both arms get the same model, prompt, turn limit and the tools a coding agent
has: a regex grep over the whole repo, file reads by line range, directory
listing, and ``submit``. The ``graph`` arm also gets PipesHub's real Code Graph
toolset (search_code, read_code, outline, get_neighbour, impact,
find_symbol_path, query_code_graph, entry_points), with the descriptions
PipesHub's own agents see, over the graph built in memory for that commit.
Scored as in B5a (Acc@1/5/10 on the submitted ranking), with tokens, tool calls
and time per run.

usage: python -m cg_eval.agentloc run --split dev|test --arm search|graph [--shard i/n] [--only ID ...]
       python -m cg_eval.agentloc score
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import re
import time
from pathlib import Path

from .run import CONNECTOR  # noqa: F401  (sets PIPESHUB_PY on sys.path)
from .memgraph import MemBlobStore, MemGraph
from .offline import ORG, IndexedRepo, build_edges, index_files
from .swebench import MODELS, gold_files, instances, tree_at

OUT = Path(__file__).resolve().parents[2] / "reports" / "b5b"
USER = "user-eval"
MAX_TURNS = 30
MAX_TOOL_CHARS = 12_000
GRAPH_TOOLS = ("search_code", "read_code", "outline", "get_neighbour", "impact", "find_symbol_path",
               "query_code_graph", "entry_points")
SYSTEM = (
    "You localize the code change that resolves a GitHub issue. Investigate the repository with "
    "the tools, then call submit with up to 10 repo-relative paths of the non-test source files "
    "that must be edited, most likely first. Do not write a fix. Be efficient: stop investigating "
    "once you are confident."
)


def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {"type": "function", "function": {"name": name, "description": description, "parameters": {
        "type": "object", "properties": properties, "required": required}}}


BASE_TOOLS = [
    _fn("grep", "Regex search over every text file in the repository. Returns up to 50 matching "
        "lines as path:line: text, and how many matched in total.",
        {"pattern": {"type": "string", "description": "Python regular expression"},
         "path_glob": {"type": "string", "description": "Optional glob to restrict files, e.g. 'django/db/**'"}},
        ["pattern"]),
    _fn("read_file", "Read lines of a file, numbered. At most 300 lines per call.",
        {"path": {"type": "string"}, "start": {"type": "integer", "description": "First line, 1-based"},
         "end": {"type": "integer", "description": "Last line, inclusive"}}, ["path"]),
    _fn("list_dir", "List the files and folders directly inside a directory ('' for the root).",
        {"path": {"type": "string"}}, []),
    _fn("submit", "Finish: the files to edit, most likely first (at most 10).",
        {"files": {"type": "array", "items": {"type": "string"}}}, ["files"]),
]


def graph_tool_schemas() -> list[dict]:
    """PipesHub's Code Graph tools as its agents see them (Tool.to_schema), connector_id filled in."""
    from app.agents.actions.code_graph.code_graph import CodeGraph

    json_types = {"string": "string", "integer": "integer", "float": "number", "boolean": "boolean",
                  "array": "array", "object": "object"}
    out = []
    for name in GRAPH_TOOLS:
        meta = getattr(CodeGraph, name)._agent_tool_meta
        props, required = {}, []
        for p in meta.parameters:
            if p.name in ("connector_id", "repo"):
                continue
            prop = {"type": json_types[p.type.value], "description": p.description}
            if getattr(p, "items", None) is not None:
                prop["items"] = p.items
            if getattr(p, "enum", None) is not None:
                prop["enum"] = p.enum
            props[p.name] = prop
            if p.required:
                required.append(p.name)
        out.append(_fn(name, meta.description, props, required))
    return out


class Workspace:
    """The repo at the issue's base commit: plain-file tools, and the graph tools when asked."""

    def __init__(self, files: dict[str, bytes], toolset=None) -> None:
        self.text = {p: b.decode("utf-8", "replace") for p, b in files.items()
                     if b"\0" not in b[:4096] and len(b) < 2_000_000}
        self.toolset = toolset

    def grep(self, pattern: str, path_glob: str | None = None) -> str:
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return f"invalid regex: {exc}"
        hits, total = [], 0
        for path in sorted(self.text):
            if path_glob and not fnmatch.fnmatch(path, path_glob):
                continue
            for i, line in enumerate(self.text[path].splitlines(), 1):
                if rx.search(line):
                    total += 1
                    if len(hits) < 50:
                        hits.append(f"{path}:{i}: {line.strip()[:200]}")
        return "\n".join(hits) + f"\n({total} matching lines{'; first 50 shown' if total > 50 else ''})"

    def read_file(self, path: str, start: int = 1, end: int | None = None) -> str:
        if path not in self.text:
            return f"no such text file: {path}"
        lines = self.text[path].splitlines()
        start = max(1, start or 1)
        end = min(len(lines), end or start + 199, start + 299)
        return "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1)) + f"\n({len(lines)} lines in file)"

    def list_dir(self, path: str = "") -> str:
        prefix = path.strip("/") + "/" if path.strip("/") else ""
        children = sorted({p[len(prefix):].split("/", 1)[0] + ("/" if "/" in p[len(prefix):] else "")
                           for p in self.text if p.startswith(prefix)})
        return "\n".join(children) if children else f"no such directory: {path}"

    async def call(self, name: str, args: dict) -> str:
        if name in ("grep", "read_file", "list_dir"):
            return getattr(self, name)(**args)
        if self.toolset is None or name not in GRAPH_TOOLS:
            return f"unknown tool: {name}"
        result = await getattr(self.toolset, name)(connector_id=CONNECTOR, **args)
        text = result[1] if isinstance(result, tuple) else result
        return text if isinstance(text, str) else json.dumps(text)


class LLM:
    def __init__(self) -> None:
        from openai import AsyncAzureOpenAI

        cfg = json.loads(MODELS.read_text())["llm_1"]["configuration"]
        self.model = cfg.get("deploymentName") or cfg["model"]
        # PipesHub's AzureOpenAILLM.AZURE_OPENAI_VERSION.
        self.client = AsyncAzureOpenAI(api_key=cfg["apiKey"], api_version="2025-04-01-preview",
                                       azure_endpoint=cfg["endpoint"])

    async def __call__(self, messages: list[dict], tools: list[dict]):
        for attempt in range(8):
            try:
                return await self.client.chat.completions.create(model=self.model, messages=messages, tools=tools)
            except Exception as exc:  # rate limits and transient errors: back off and retry
                if attempt == 7:
                    raise
                await asyncio.sleep(min(60, 2 ** attempt + 1) if "429" in str(exc) else 3)


async def localize(inst: dict, arm: str, llm: LLM) -> dict:
    started = time.perf_counter()
    files = tree_at(inst["repo"], inst["base_commit"])
    toolset, tools = None, list(BASE_TOOLS)
    if arm == "graph":
        from app.agents.actions.code_graph.code_graph import CodeGraph

        graph = MemGraph(ORG, search_backend="neo4j")
        repo = IndexedRepo(inst["repo"].split("/")[1], CONNECTOR, f"group-{inst['instance_id']}")
        await index_files(graph, repo, files)
        await build_edges(graph, repo)
        from .agentqa import settle_as_built  # recorded as built, as production does
        await settle_as_built(graph, repo.group_id)
        toolset = CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph,
                             "blob_store": MemBlobStore(graph)})
        tools = tools[:-1] + graph_tool_schemas() + tools[-1:]
    setup_seconds = time.perf_counter() - started
    ws = Workspace(files, toolset)

    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Repository: {inst['repo']}\n\nIssue:\n{inst['problem_statement']}"}]
    usage = {"prompt": 0, "cached": 0, "completion": 0, "reasoning": 0, "calls": 0}
    tool_calls: dict[str, int] = {}
    submitted, nudged, turns = None, False, 0
    agent_started = time.perf_counter()
    while turns < MAX_TURNS and submitted is None:
        turns += 1
        r = await llm(messages, tools)
        u = r.usage
        usage["calls"] += 1
        usage["prompt"] += u.prompt_tokens
        usage["completion"] += u.completion_tokens
        usage["cached"] += getattr(getattr(u, "prompt_tokens_details", None), "cached_tokens", 0) or 0
        usage["reasoning"] += getattr(getattr(u, "completion_tokens_details", None), "reasoning_tokens", 0) or 0
        msg = r.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Call submit with your ranked list of files."})
            continue
        for call in msg.tool_calls:
            name = call.function.name
            tool_calls[name] = tool_calls.get(name, 0) + 1
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "submit":
                submitted = [f for f in args.get("files", []) if isinstance(f, str)][:10]
                content = "submitted"
            else:
                try:
                    content = await ws.call(name, args)
                except Exception as exc:  # a tool error goes back to the agent, as in production
                    content = f"tool error: {type(exc).__name__}: {exc}"[:500]
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content[:MAX_TOOL_CHARS]})
    gold, _ = gold_files(inst["patch"])
    return {"instance_id": inst["instance_id"], "repo": inst["repo"], "arm": arm, "gold": gold,
            "submitted": submitted or [], "turns": turns, "tool_calls": tool_calls, "usage": usage,
            "seconds": {"setup": round(setup_seconds, 1), "agent": round(time.perf_counter() - agent_started, 1)}}


def run(split: str, arm: str, shard: str, only: list[str] | None) -> None:
    index, count = map(int, shard.split("/"))
    llm = LLM()
    target = OUT / split / arm
    target.mkdir(parents=True, exist_ok=True)
    for n, inst in enumerate(instances(split)):
        if n % count != index or (only and inst["instance_id"] not in only):
            continue
        path = target / f"{inst['instance_id']}.json"
        if path.exists():
            continue
        try:
            result = asyncio.run(localize(inst, arm, llm))
        except Exception as exc:  # reported, not hidden
            result = {"instance_id": inst["instance_id"], "arm": arm, "error": repr(exc)[:500]}
        path.write_text(json.dumps(result))
        print(inst["instance_id"], arm, result.get("turns"), result.get("usage", {}).get("prompt"),
              "error" if "error" in result else "", flush=True)


def score() -> None:
    report = {}
    for split_dir in sorted(p for p in OUT.iterdir() if p.is_dir()):
        for arm_dir in sorted(p for p in split_dir.iterdir() if p.is_dir()):
            rs = [json.loads(p.read_text()) for p in sorted(arm_dir.glob("*.json"))]
            ok = [r for r in rs if "error" not in r]
            row = {"issues": len(rs), "errors": len(rs) - len(ok),
                   "no_submit": sum(not r["submitted"] for r in ok)}
            for k in (1, 5, 10):
                row[f"acc@{k}"] = round(100 * sum(set(r["gold"]) <= set(r["submitted"][:k]) for r in ok) / max(1, len(ok)), 1)
            for key in ("prompt", "cached", "completion", "reasoning", "calls"):
                row[f"mean_{key}"] = round(sum(r["usage"][key] for r in ok) / max(1, len(ok)))
            row["mean_tool_calls"] = round(sum(sum(r["tool_calls"].values()) for r in ok) / max(1, len(ok)), 1)
            row["tool_mix"] = {}
            for r in ok:
                for name, c in r["tool_calls"].items():
                    row["tool_mix"][name] = row["tool_mix"].get(name, 0) + c
            row["mean_agent_seconds"] = round(sum(r["seconds"]["agent"] for r in ok) / max(1, len(ok)), 1)
            report[f"{split_dir.name}/{arm_dir.name}"] = row
    (OUT / "results.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", default="dev")
    r.add_argument("--arm", choices=("search", "graph"), required=True)
    r.add_argument("--shard", default="0/1")
    r.add_argument("--only", nargs="*")
    sub.add_parser("score")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.split, args.arm, args.shard, args.only)
    else:
        score()


if __name__ == "__main__":
    main()
