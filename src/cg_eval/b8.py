"""B8: the same agent with and without PipesHub's code graph, on questions a text search answers badly.

  python -m cg_eval.b8 run --repeat 1 [--arms plain,pipeshub] [--limit N] [--jobs 4]
  python -m cg_eval.b8 score

B6's harness (headless Claude Code, Sonnet, Read/Grep/Glob, one optional MCP server, isolated from the
user's setup), on reports/b8/questions.jsonl (built by b8_questions, negatives audited). The arms differ
only in the PipesHub MCP server (b6_pipeshub_mcp, one per repo on PORTS). Both arms are told the same
definition of a call, the one the SCIP answer key uses. Each repeat writes its own runs directory.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import b6

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / os.environ.get("B8_OUT", "b8")
# B8_STRIP_SRC=1: also accept answers written from the checkout root (`src/vs/...`); results-strip-src.json.
STRIP_SRC = bool(os.environ.get("B8_STRIP_SRC"))
SUFFIX = "-strip-src" if STRIP_SRC else ""  # b8-short: graph servers with short descriptions
ARMS = ("plain", "graphify", "pipeshub")
_BASE = int(os.environ.get("B8_PORT_BASE", "18811"))
PORTS = {"guava": _BASE, "client-go": _BASE + 1, "excalidraw": _BASE + 2, "vscode": _BASE + 3}
BUDGET_USD = float(os.environ.get("B8_BUDGET_USD", "50"))
DEFINITION = (
    " A call counts only when it resolves, by the static types in the code, to that exact function or method: "
    "a call through an interface or base-class method counts for that interface or base method, not for its "
    "implementations; passing a function as a value (a callback, a method reference) is not a call; rendering "
    "a component as JSX is a call. All overloads of a name in one class count as one method, and one overload calling another is not a call."
)

# B8_CALL_DEF=runtime: for a key that includes calls seen running (B12), a call counts for what it runs.
RUNTIME_DEFINITION = (
    " A call counts when that function or method actually runs from the call site: a call on an object "
    "counts for the method that runs for the object's type, even when the type is only known at run time "
    "(a parameter, an attribute, a value returned by a factory or registry); constructing an object counts "
    "as a call to its class; passing a function as a value (a callback, a method reference) is not a call."
)


# B9 (B8_SHIPPED=1): Claude Code's own read-only toolset as shipped: search, read, read-only shell, subagents.
SHIPPED = ["Read", "Grep", "Glob", "Agent", "TodoWrite"] + [f"Bash({c}:*)" for c in (
    "rg", "grep", "find", "ls", "cat", "head", "tail", "wc", "sed -n", "awk", "sort", "uniq", "xargs", "tree")]


def setup(repeat: int) -> None:
    """Point B6's runner at B8: its questions' repos, ports, prompt, and this repeat's output."""
    b6.OUT = OUT / f"r{repeat}"
    b6.SCRATCH = ROOT / "bench"  # runs execute in bench/repos/<repo>
    b6.PORTS = PORTS
    b6.BUDGET_USD = BUDGET_USD
    if os.environ.get("B8_SHIPPED"):
        b6.BUILTIN = SHIPPED
    definition = RUNTIME_DEFINITION if os.environ.get("B8_CALL_DEF") == "runtime" else DEFINITION
    b6.APPEND = b6.APPEND.replace(" Be efficient.", definition + " Be efficient.")
    b6.spent = lambda: sum(json.loads(p.read_text()).get("cost_usd") or 0.0 for p in OUT.glob("r*/runs/*/*.json"))


def questions() -> list[dict]:
    return [json.loads(line) for line in (OUT / "questions.jsonl").read_text().splitlines()]


def run(arms: list[str], repeat: int, limit: int | None, jobs: int, ids: list[str] | None) -> None:
    setup(repeat)
    qs = [q for q in questions() if not ids or q["id"] in ids][:limit]
    with ThreadPoolExecutor(jobs) as ex:
        for rec in ex.map(lambda qa: b6.run_one(*qa), [(q, arm) for q in qs for arm in arms]):
            if rec and rec.get("skipped"):
                print("budget reached, stopping", flush=True)
                break
            if rec:
                print(f"r{repeat}", rec["arm"], rec["id"], "cost", rec["cost_usd"], "turns", rec["turns"],
                      "tools", sum(rec["tool_calls"].values()), "gave_up" if rec["gave_up"] else "",
                      f"total ${b6.spent():.2f}", flush=True)


def score() -> None:
    from . import agentqa
    from .qa_questions import load

    qs = {q["id"]: q for q in questions()}
    outs = {repo: load(repo)[0] for repo in {q["repo"] for q in qs.values()}}
    files = {repo: agentqa.keyed(repo) for repo in outs}
    per = []
    for f in sorted(OUT.glob("r*/runs/*/*.json")):
        r = json.loads(f.read_text())
        q = qs.get(r["id"])
        if q is None:  # a question dropped after the run
            continue
        if STRIP_SRC and r.get("submitted"):  # `src/vs/x.ts` for `vs/x.ts`: the checkout's root folder
            r["submitted"]["answer"] = [a[4:] if isinstance(a, str) and a.startswith("src/") else a
                                        for a in r["submitted"].get("answer") or []]
        if q["kind"] == "unreachable":
            s = {"correct": not r["submitted"]["answer"]}
        else:
            s = agentqa.score_one(q, r, outs[q["repo"]], files[q["repo"]])
        u = r.get("usage") or {}
        if r.get("model_usage"):  # every model the run used, subagents included
            mu = r["model_usage"].values()
            u = {"input_tokens": sum(m.get("inputTokens", 0) for m in mu),
                 "cache_read_input_tokens": sum(m.get("cacheReadInputTokens", 0) for m in mu),
                 "cache_creation_input_tokens": sum(m.get("cacheCreationInputTokens", 0) for m in mu),
                 "output_tokens": sum(m.get("outputTokens", 0) for m in mu)}
        per.append({"arm": r["arm"], "repeat": f.parts[-4], "id": q["id"], "kind": q["kind"], "repo": q["repo"],
                    "grep_hits": q.get("grep_hits"), **s,
                    "score": s["correct"] if "correct" in s else s["f1"],
                    "cost_usd": r.get("cost_usd") or 0.0, "turns": r.get("turns") or 0,
                    "tool_calls": sum(r["tool_calls"].values()),
                    "mcp_calls": sum(v for k, v in r["tool_calls"].items() if k.startswith("mcp__")),
                    "input_uncached": u.get("input_tokens", 0), "cache_read": u.get("cache_read_input_tokens", 0),
                    "cache_write": u.get("cache_creation_input_tokens", 0), "output_tokens": u.get("output_tokens", 0),
                    "seconds": r["seconds"], "gave_up": r["gave_up"]})
    for p in per:
        p["input_total"] = p["input_uncached"] + p["cache_read"] + p["cache_write"]
    (OUT / f"scored{SUFFIX}.jsonl").write_text("".join(json.dumps(p) + "\n" for p in per))
    metrics = ("score", "cost_usd", "turns", "tool_calls", "mcp_calls", "input_total", "input_uncached",
               "cache_read", "output_tokens", "seconds")

    def summary(ps: list[dict]) -> dict:
        return {"n": len(ps), **{m: round(statistics.mean(p[m] for p in ps), 3) for m in metrics},
                "gave_up": sum(p["gave_up"] for p in ps)}

    res: dict = {}
    for group in ("all", "kind", "repo"):
        keys = ["all"] if group == "all" else sorted({p[group] for p in per})
        for k in keys:
            sel = [p for p in per if group == "all" or p[group] == k]
            res[f"{group}:{k}"] = {arm: summary([p for p in sel if p["arm"] == arm]) for arm in ARMS
                                   if any(p["arm"] == arm for p in sel)}
    res["paired"] = paired(per)
    (OUT / f"results{SUFFIX}.json").write_text(json.dumps(res, indent=1))
    print(json.dumps(res, indent=1))


def paired(per: list[dict]) -> dict:
    """Per question (mean over repeats), pipeshub minus plain; sign-flip permutation p-value."""
    import random

    by = {}
    for p in per:
        by.setdefault((p["id"], p["arm"]), []).append(p)
    ids = sorted({i for i, a in by if a == "plain"} & {i for i, a in by if a == "pipeshub"})
    out = {"questions": len(ids)}
    rng = random.Random(0)
    for m in ("score", "input_total", "output_tokens", "turns", "tool_calls", "cost_usd", "seconds"):
        d = [statistics.mean(x[m] for x in by[(i, "pipeshub")]) - statistics.mean(x[m] for x in by[(i, "plain")])
             for i in ids]
        if not d:
            continue
        obs = abs(sum(d))
        hits = sum(abs(sum(x if rng.random() < 0.5 else -x for x in d)) >= obs for _ in range(10000))
        base = statistics.mean(statistics.mean(x[m] for x in by[(i, "plain")]) for i in ids)
        out[m] = {"mean_diff": round(statistics.mean(d), 3), "plain_mean": round(base, 3),
                  "pct": round(100 * statistics.mean(d) / base, 1) if base else None, "p": round(hits / 10000, 4)}
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arms", default=",".join(ARMS))
    r.add_argument("--repeat", type=int, default=1)
    r.add_argument("--limit", type=int)
    r.add_argument("--ids", default="")
    r.add_argument("--jobs", type=int, default=4)
    sub.add_parser("score")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.arms.split(","), args.repeat, args.limit, args.jobs, [x for x in args.ids.split(",") if x] or None)
    else:
        score()


if __name__ == "__main__":
    main()
