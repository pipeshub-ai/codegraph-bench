"""B4: an agent (llm_1) answers code questions with known answers, with and without PipesHub's code graph.

  python -m cg_eval.agentqa run --arm search|graph [--repeat N] [--jobs 8]
  python -m cg_eval.agentqa score

The same model, prompt, turn limit and plain tools as B5b (grep, read_file, list_dir); the graph
arm adds PipesHub's Code Graph toolset over the repo built in memory with production code. List
answers are scored as sets against the SCIP truth (precision, recall, exact); a call path counts if
every hop is a true call from the start to the end; "explain" answers are graded by llm_judge
against the true callees. Questions come from qa_questions.py.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time
from pathlib import Path

from .agentloc import BASE_TOOLS, LLM, MAX_TOOL_CHARS, ORG, USER, Workspace, _fn, graph_tool_schemas
from .memgraph import MemBlobStore, MemGraph
from .offline import IndexedRepo, build_edges, index_files, read_tree
from .qa_questions import BENCH, REPOS, load
from .run import CONNECTOR
from .swebench import MODELS

OUT = Path(__file__).resolve().parents[2] / "reports" / "b4"
MAX_TURNS = 30
SYSTEM = (
    "You answer questions about a code repository. Investigate with the tools, then call submit. "
    "Name every function or method as `path::Name` or `path::Type.method` (nested types joined with "
    "dots, e.g. `src/a/Lists.java::Lists.ReverseList.get`), with the path relative to the repository "
    "root and no parentheses. Give `answer` as a list of such names (an empty list if the true answer "
    "is none) and `explanation` as short prose. Be exhaustive for list questions and only list what "
    "you have checked in the code. Be efficient."
)
SUBMIT = _fn("submit", "Finish with the answer.",
             {"answer": {"type": "array", "items": {"type": "string"},
                         "description": "path::Name entries; empty if none"},
              "explanation": {"type": "string"}}, ["answer"])

_graphs: dict[str, object] = {}
_files: dict[str, dict] = {}


async def toolset_for(repo: str):
    """PipesHub's Code Graph toolset over the repo, built once per process."""
    if repo not in _graphs:
        from app.agents.actions.code_graph.code_graph import CodeGraph

        graph = MemGraph(ORG, search_backend="neo4j")
        indexed = IndexedRepo(repo, CONNECTOR, f"group-{repo}")
        await index_files(graph, indexed, files_for(repo))
        await build_edges(graph, indexed)
        await settle_as_built(graph, indexed.group_id)
        _graphs[repo] = CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph,
                                   "blob_store": MemBlobStore(graph)})
    return _graphs[repo]


async def settle_as_built(graph, group_id: str) -> None:
    """Record the build as finished, as the production runner does; otherwise every tool
    answer carries "its edges have not been built yet", and the agent is told to distrust it."""
    from app.modules.code_graph.build_state import BuildStateStore

    store = BuildStateStore(graph)
    start = await store.begin(ORG, CONNECTOR, group_id, "eval")
    await store.settle_built(ORG, group_id, lease_token="eval", start=start,
                             started_at_ms=int(time.time() * 1000), advance_watermark=True, full=True)


def files_for(repo: str) -> dict:
    if repo not in _files:
        _files[repo] = read_tree(BENCH / "repos" / REPOS[repo])
    return _files[repo]


async def answer(q: dict, arm: str, llm: LLM) -> dict:
    tools = [t for t in BASE_TOOLS if t["function"]["name"] != "submit"]
    toolset = None
    if arm == "graph":
        toolset = await toolset_for(q["repo"])
        tools += graph_tool_schemas()
    tools.append(SUBMIT)
    ws = Workspace(files_for(q["repo"]), toolset)
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"Repository: {q['repo']}\n\nQuestion: {q['question']}"}]
    usage = {"prompt": 0, "completion": 0, "calls": 0}
    tool_calls: dict[str, int] = {}
    submitted, nudged, turns = None, False, 0
    started = time.perf_counter()
    while turns < MAX_TURNS and submitted is None:
        turns += 1
        r = await llm(messages, tools)
        usage["calls"] += 1
        usage["prompt"] += r.usage.prompt_tokens
        usage["completion"] += r.usage.completion_tokens
        msg = r.choices[0].message
        messages.append(msg.model_dump(exclude_none=True))
        if not msg.tool_calls:
            if nudged:
                break
            nudged = True
            messages.append({"role": "user", "content": "Call submit with your answer."})
            continue
        for call in msg.tool_calls:
            name = call.function.name
            tool_calls[name] = tool_calls.get(name, 0) + 1
            try:
                args = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            if name == "submit":
                submitted = {"answer": [a for a in args.get("answer", []) if isinstance(a, str)],
                             "explanation": str(args.get("explanation", ""))}
                content = "submitted"
            else:
                try:
                    content = await ws.call(name, args)
                except Exception as exc:  # a tool error goes back to the agent, as in production
                    content = f"tool error: {type(exc).__name__}: {exc}"[:500]
            messages.append({"role": "tool", "tool_call_id": call.id, "content": content[:MAX_TOOL_CHARS]})
    return {"id": q["id"], "arm": arm, "submitted": submitted or {"answer": [], "explanation": ""},
            "gave_up": submitted is None, "turns": turns, "tool_calls": tool_calls, "usage": usage,
            "seconds": round(time.perf_counter() - started, 1)}


async def run(arm: str, repeat: int, jobs: int) -> None:
    qs = [json.loads(line) for line in (OUT / "questions.jsonl").read_text().splitlines()]
    target = OUT / "runs" / f"{arm}-r{repeat}"
    target.mkdir(parents=True, exist_ok=True)
    llm, gate = LLM(), asyncio.Semaphore(jobs)
    if arm == "graph":  # one build per repo, before the questions race for it
        for repo in REPOS:
            await toolset_for(repo)

    async def one(q: dict) -> None:
        path = target / f"{q['id']}.json"
        if path.exists():
            return
        async with gate:
            try:
                result = await answer(q, arm, llm)
            except Exception as exc:  # reported, not hidden
                result = {"id": q["id"], "arm": arm, "error": repr(exc)[:500]}
        path.write_text(json.dumps(result))
        print(q["id"], arm, repeat, result.get("turns"), "error" if "error" in result else "", flush=True)

    await asyncio.gather(*(one(q) for q in qs))


# -- scoring --------------------------------------------------------------

def norm(name: str) -> str:
    """Tolerate the ways an agent writes a name: backticks, `()`, a kind prefix, `#` for `.`."""
    # `checkArgument#3` is PipesHub's mark for the third overload; SCIP names them all alike.
    name = re.sub(r"#\d+", "", name.strip().strip("`").replace("()", "")).replace("#", ".")
    path, sep, q = name.partition("::")
    q = re.sub(r"^(function|method|class|func):", "", q.strip())
    return f"{path.strip().lstrip('./')}{sep}{q}"


def prf(got: set[str], want: set[str]) -> dict:
    tp = len(got & want)
    p = tp / len(got) if got else (1.0 if not want else 0.0)
    r = tp / len(want) if want else (1.0 if not got else 0.0)
    return {"precision": p, "recall": r, "f1": 2 * p * r / (p + r) if p + r else 0.0, "exact": got == want}


JUDGE = (
    "You grade an answer to a question about code. The reference lists the functions the subject "
    "truly calls directly, from a compiler-grade index, and what each of those calls in turn. "
    "Grade only what the answer claims about the code: 2 = its account of the direct calls is "
    "correct and covers the important ones with no invented calls; 1 = mostly correct but misses "
    "important calls or makes a minor wrong claim; 0 = wrong, mostly missing, or it invents calls "
    "that do not exist. Reply with JSON only: {\"score\": 0|1|2, \"invented\": [names the answer "
    "claims are called but are not in the reference], \"reason\": \"one sentence\"}."
)


def judge(q: dict, sub: dict) -> dict:
    import anthropic

    cfg = json.loads(MODELS.read_text())["llm_judge"]["configuration"]
    client = anthropic.Anthropic(api_key=cfg["apiKey"])
    ref = {"subject": q["target"], "direct_calls": q["answer"], "second_level": q["second_level"]}
    body = f"Question: {q['question']}\n\nReference: {json.dumps(ref)}\n\nAnswer list: {json.dumps(sub['answer'])}\n\n" \
           f"Answer explanation:\n{sub['explanation']}"
    last = ""
    for attempt in range(8):
        try:
            r = client.messages.create(model=cfg["model"], max_tokens=1200, system=JUDGE,
                                       messages=[{"role": "user", "content": body}])
            # The reply can open with a thinking block; the verdict is in the text block.
            text = next(b.text for b in r.content if getattr(b, "type", "") == "text")
            return json.loads(text[text.index("{"): text.rindex("}") + 1])
        except Exception as exc:  # noqa: BLE001  transient API errors and an unparseable reply: retry
            last = f"{type(exc).__name__}: {exc}"[:300]
            time.sleep(min(60, 2 ** attempt))
    return {"score": None, "reason": "judge failed", "error": last}


def keyed(repo: str) -> set[str]:
    """The files the truth covers; a named function elsewhere (requests' tests) cannot be scored, as in B1."""
    return {json.loads(line)["path"] for line in (BENCH / "truth" / repo / "files.jsonl").read_text().splitlines()}


def canon(name: str, known: set[str]) -> str:
    """A nested function as a parser names it (`Response.iter_content.generate`) -> the
    truth's name for it (`Response.generate`), when only that one exists."""
    if name in known:
        return name
    path, _, q = name.partition("::")
    parts = q.split(".")
    hits = {f"{path}::{'.'.join(parts[:i] + parts[i + 1:])}" for i in range(len(parts) - 1)} & known
    return hits.pop() if len(hits) == 1 else name


def score_one(q: dict, r: dict, out: dict, files: set[str]) -> dict:
    known = set(out) | {b for targets in out.values() for b in targets}
    got = {canon(n, known) for n in map(norm, r["submitted"]["answer"]) if n.partition("::")[0] in files}
    if q["kind"] == "path":
        chain = [canon(norm(a), known) for a in r["submitted"]["answer"]]
        ok = len(chain) >= 2 and chain[0] == q["target"] and chain[-1] == q["to"] and all(
            b in out.get(a, ()) for a, b in zip(chain, chain[1:]))
        return {"correct": ok}
    if q["kind"] == "explain":
        cache = OUT / "judge" / f"{r['arm']}-{r['_repeat']}-{q['id']}.json"
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(judge(q, r["submitted"])))
        j = json.loads(cache.read_text())
        return {"judge": j.get("score"), "invented": len(j.get("invented") or []),
                **prf(got, set(q["answer"]))}
    s = prf(got, set(q["answer"]))
    if q["kind"] == "negative":
        s["correct"] = not got
    return s


def score() -> None:
    qs = {q["id"]: q for q in map(json.loads, (OUT / "questions.jsonl").read_text().splitlines())}
    outs = {repo: load(repo)[0] for repo in REPOS}
    files = {repo: keyed(repo) for repo in REPOS}
    rows, per = {}, []
    for d in sorted((OUT / "runs").iterdir()):
        arm, repeat = d.name.rsplit("-r", 1)
        for f in sorted(d.glob("*.json")):
            r = json.loads(f.read_text())
            if "error" in r:
                per.append({"arm": arm, "repeat": repeat, "id": r["id"], "error": True})
                continue
            q = qs[r["id"]]
            r["_repeat"] = repeat
            per.append({"arm": arm, "repeat": repeat, "id": q["id"], "kind": q["kind"], "repo": q["repo"],
                        **score_one(q, r, outs[q["repo"]], files[q["repo"]]), "tokens": r["usage"]["prompt"] + r["usage"]["completion"],
                        "tool_calls": sum(r["tool_calls"].values()), "seconds": r["seconds"], "gave_up": r["gave_up"]})
    (OUT / "scored.jsonl").write_text("".join(json.dumps(p) + "\n" for p in per))
    for arm in sorted({p["arm"] for p in per}):
        ps = [p for p in per if p["arm"] == arm and not p.get("error")]
        row = {"answers": len(ps), "errors": sum(1 for p in per if p["arm"] == arm and p.get("error"))}
        for kind in ("callers", "callees", "impact"):
            k = [p for p in ps if p["kind"] == kind]
            row[kind] = {m: round(100 * sum(p[m] for p in k) / max(1, len(k)), 1) for m in ("precision", "recall", "f1", "exact")}
        for kind in ("path", "negative"):
            k = [p for p in ps if p["kind"] == kind]
            row[kind] = round(100 * sum(p["correct"] for p in k) / max(1, len(k)), 1)
        k = [p for p in ps if p["kind"] == "explain" and p.get("judge") is not None]
        row["explain"] = {"judge_mean": round(sum(p["judge"] for p in k) / max(1, len(k)), 2),
                          "with_invented_calls": round(100 * sum(p["invented"] > 0 for p in k) / max(1, len(k)), 1)}
        row["mean_tokens"] = round(sum(p["tokens"] for p in ps) / max(1, len(ps)))
        row["mean_tool_calls"] = round(sum(p["tool_calls"] for p in ps) / max(1, len(ps)), 1)
        row["mean_seconds"] = round(sum(p["seconds"] for p in ps) / max(1, len(ps)), 1)
        rows[arm] = row
    (OUT / "results.json").write_text(json.dumps(rows, indent=1))
    print(json.dumps(rows, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arm", choices=("search", "graph"), required=True)
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--jobs", type=int, default=8)
    sub.add_parser("score")
    args = ap.parse_args()
    if args.cmd == "run":
        asyncio.run(run(args.arm, args.repeat, args.jobs))
    else:
        score()


if __name__ == "__main__":
    main()
