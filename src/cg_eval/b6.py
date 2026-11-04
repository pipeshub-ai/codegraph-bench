"""B6: one agent (headless Claude Code, Sonnet) with each code tool's MCP server, on B4's questions.

  python -m cg_eval.b6 run [--arms plain,pipeshub,...] [--limit N] [--jobs 4]
  python -m cg_eval.b6 score

Every arm runs the same Claude Code, model, prompt, built-in tools (Read, Grep, Glob; nothing that
writes) and turn limit; an arm adds exactly one MCP server. Runs are isolated from the user's own
Claude Code setup (--setting-sources project in a directory with no CLAUDE.md, --strict-mcp-config,
no session persistence). Spend is read from each run's own report and capped in total.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "b6"
SCRATCH = Path(os.environ.get("B6_SCRATCH") or ROOT / "bench" / "work" / "b6")
TOOLS = ROOT / "bench" / "tools"
BUDGET_USD = float(os.environ.get("B6_BUDGET_USD", "190"))
PER_RUN_USD = float(os.environ.get("B6_PER_RUN_USD", "1.0"))
MAX_TURNS = int(os.environ.get("B6_MAX_TURNS", "30"))
PORTS = {"requests": 18801, "gin": 18802, "django": 18803, "guava": 18804}
ARMS = ("plain", "pipeshub", "graphify", "cbm", "cgc")
BUILTIN = ["Read", "Grep", "Glob"]  # B9 swaps in Claude Code's shipped read-only toolset
APPEND = (
    "You answer questions about this code repository. Name every function or method as `path::Name` "
    "or `path::Type.method` (nested types joined with dots, e.g. `src/a/Lists.java::Lists.ReverseList.get`), "
    "with the path relative to the repository root and no parentheses. Be exhaustive for list questions and "
    "only list what you have checked in the code. Be efficient. End your final reply with exactly one line: "
    'ANSWER_JSON: {"answer": [...], "explanation": "..."} where answer is the list of such names '
    "(an empty list if the true answer is none)."
)


# B11: the PipesHub arm on a full stack -- its own /mcp behind a PAT, read-only code tools only.
STACK_URL = os.environ.get("B6_PIPESHUB_URL")
STACK_PAT = os.environ.get("B6_PIPESHUB_PAT_FILE")
STACK_TOOLS = ("code_repos", "code_search", "code_semantic_search", "code_symbols", "code_neighbours",
               "code_read", "code_outline", "code_path", "code_impact", "code_entry_points")
# B11 best setup (B6_BEST_SETUP): each tool arm as its maker recommends, plus one line naming what is indexed.
# PipesHub: only its code tools (its document tools stay hidden); its server instructions name them.
# Graphify: its MCP tools, its CLI through Bash, and what `graphify claude install` adds (CLAUDE.md rules
# and a PreToolUse hook), pointed at the prebuilt graph with GRAPHIFY_OUT.
BEST_SETUP = bool(os.environ.get("B6_BEST_SETUP"))
NON_CODE = ("agents", "chat", "connect_source", "create_collection", "directory", "download_record",
            "get_record_content", "indexing_status", "search", "sources", "upload")
# Graphify A/B (B6_GRAPHIFY_BIN, B6_GRAPHIFY_WORK, B6_GRAPHIFY_HINT_EXTRA): another install, graph set or prompt line.
GRAPHIFY_BIN = Path(os.environ.get("B6_GRAPHIFY_BIN") or TOOLS / "graphify" / "bin" / "graphify")
GRAPHIFY_RULES = (
    " This project has a knowledge graph with god nodes, community structure, and cross-file relationships. "
    'Run `graphify query "<question>"` before your first grep, search or source-file read for a codebase '
    "question, and put this rule in every subagent prompt that explores code. Read raw files only for lines "
    'the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and '
    '`graphify explain "<concept>"` for focused concepts.'
)


def _graph_dir(repo: str) -> Path:
    return ROOT / "bench" / os.environ.get("B6_GRAPHIFY_WORK", "work-v2") / "graphify" / repo / "graphify-out"


def best_setup(arm: str, repo: str) -> tuple[str, list[str], list[str], dict[str, str]]:
    """(prompt to append, extra CLI args, extra allowed tools, extra env) for an arm's best setup."""
    if not BEST_SETUP:
        return "", [], [], {}
    if arm == "pipeshub" and STACK_URL:
        name = os.environ["B6_PIPESHUB_REPO"]
        hint = (f" The repository you are in is indexed in PipesHub as {name}. Its pipeshub_code_* tools "
                "answer from the code graph: pipeshub_code_neighbours and pipeshub_code_impact list "
                "callers and dependents resolved to the right definition, pipeshub_code_path traces how "
                "A reaches B, pipeshub_code_semantic_search finds code from a description when you do "
                "not know its name, and pipeshub_code_search finds a name (mode identifier) or text in "
                "file bodies (mode text or regex, with a repo-relative path)."
                + os.environ.get("B6_PIPESHUB_HINT_EXTRA", ""))
        # B11 A/B: B6_PIPESHUB_DROP hides further code tools, e.g. "code_entry_points,code_symbols".
        drop = NON_CODE + tuple(t for t in os.environ.get("B6_PIPESHUB_DROP", "").split(",") if t)
        return hint, ["--disallowedTools", ",".join(f"mcp__pipeshub__pipeshub_{t}" for t in drop)], [], {}
    if arm == "graphify":
        hint = (" The repository you are in is indexed by Graphify; the mcp__graphify__* tools and the "
                "`graphify` command answer from its knowledge graph." + GRAPHIFY_RULES
                + os.environ.get("B6_GRAPHIFY_HINT_EXTRA", ""))
        hooks = {"hooks": {"PreToolUse": [
            {"matcher": "Bash|Grep", "hooks": [{"type": "command", "command": f"{GRAPHIFY_BIN} hook-guard search", "timeout": 10}]},
            {"matcher": "Read|Glob", "hooks": [{"type": "command", "command": f"{GRAPHIFY_BIN} hook-guard read", "timeout": 10}]},
        ]}}
        env = {"GRAPHIFY_OUT": str(_graph_dir(repo)), "PATH": f"{GRAPHIFY_BIN.parent}:{os.environ['PATH']}"}
        return hint, ["--settings", json.dumps(hooks)], ["Bash(graphify:*)"], env
    return "", [], [], {}


# B11: runs bill this key, not the logged-in account; read from models.json, never printed.
API_KEY_FILE = os.environ.get("B6_ANTHROPIC_KEY_FROM")


def _api_key_env() -> dict[str, str]:
    if not API_KEY_FILE:
        return {}
    entry, _, path = API_KEY_FILE.partition("@")
    return {"ANTHROPIC_API_KEY": json.loads(Path(path).expanduser().read_text())[entry]["configuration"]["apiKey"]}


def mcp_server(arm: str, repo: str) -> dict | None:
    """The one MCP server an arm adds, for one repo."""
    if arm == "pipeshub" and STACK_URL:
        token = Path(STACK_PAT).read_text().strip()
        return {"type": "http", "url": STACK_URL, "headers": {"Authorization": f"Bearer {token}"}}
    if arm == "pipeshub":
        return {"type": "http", "url": f"http://127.0.0.1:{PORTS[repo]}/mcp"}
    if arm == "graphify":
        graph = _graph_dir(repo) / "graph.json"
        return {"command": str(GRAPHIFY_BIN.parent / "graphify-mcp"), "args": [str(graph)]}
    if arm == "cbm":
        return {"command": str(TOOLS / "cbm-012" / "codebase-memory-mcp"), "args": [],
                "env": {"HOME": str(SCRATCH / "cbm-home"), "CBM_SHARED_METHODS": "drop"}}
    if arm == "cgc":
        return {"command": str(TOOLS / "cgc" / "bin" / "cgc"),
                "args": ["--db", "kuzudb", "--db-path", str(SCRATCH / "idx" / f"{repo}.kuzu"), "mcp", "start"],
                "env": {"HOME": str(SCRATCH / "cgc-home")}}
    return None


def spent() -> float:
    return sum(json.loads(p.read_text()).get("cost_usd") or 0.0 for p in OUT.glob("runs/*/*.json"))


def run_one(q: dict, arm: str) -> dict | None:
    path = OUT / "runs" / arm / f"{q['id']}.json"
    if path.exists():
        return None
    if spent() >= BUDGET_USD:
        return {"skipped": "budget"}
    repo_dir = SCRATCH / "repos" / q["repo"]
    setup_hint, setup_args, setup_allowed, setup_env = best_setup(arm, q["repo"])
    cmd = ["claude", "-p", q["question"], "--model", os.environ.get("B6_MODEL", "sonnet"), "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--setting-sources", "project", "--strict-mcp-config",
           "--tools", ",".join(dict.fromkeys(t.partition("(")[0] for t in BUILTIN)), "--append-system-prompt", APPEND + setup_hint,
           "--max-turns", str(MAX_TURNS), "--max-budget-usd", str(PER_RUN_USD)]
    allowed = list(BUILTIN)
    server = mcp_server(arm, q["repo"])
    if server is not None:
        cfg = SCRATCH / "cfg" / f"{arm}-{q['repo']}-{os.getpid()}-{threading.get_ident()}.json"  # concurrent runs each write their own
        cfg.parent.mkdir(parents=True, exist_ok=True)
        cfg.touch(mode=0o600, exist_ok=True)  # carries the stack's PAT in B11
        cfg.write_text(json.dumps({"mcpServers": {arm: server}}))
        cmd += ["--mcp-config", str(cfg)]
        if arm == "pipeshub" and STACK_URL:
            allowed += [f"mcp__pipeshub__pipeshub_{t}" for t in STACK_TOOLS]
        else:
            allowed.append(f"mcp__{arm}")
    cmd += setup_args + ["--allowedTools", ",".join(allowed + setup_allowed)]
    started = time.time()
    proc = subprocess.run(cmd, cwd=repo_dir, capture_output=True, text=True, timeout=1800, stdin=subprocess.DEVNULL,
                          env={**os.environ, "MCP_TIMEOUT": "120000", **_api_key_env(), **setup_env})
    tool_calls: dict[str, int] = {}
    final, mcp_status = {}, None
    for line in proc.stdout.splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            mcp_status = ev.get("mcp_servers")
        if ev.get("type") == "assistant":
            for part in ev.get("message", {}).get("content", []):
                if part.get("type") == "tool_use":
                    tool_calls[part["name"]] = tool_calls.get(part["name"], 0) + 1
        if ev.get("type") == "result":
            final = ev
    text = final.get("result") or ""
    m = re.search(r"ANSWER_JSON:\s*(\{.*\})\s*$", text.strip(), re.S)
    submitted = {"answer": [], "explanation": ""}
    if m:
        try:
            got = json.loads(m.group(1))
            submitted = {"answer": [a for a in got.get("answer", []) if isinstance(a, str)],
                         "explanation": str(got.get("explanation", ""))}
        except json.JSONDecodeError:
            pass
    rec = {"id": q["id"], "arm": arm, "submitted": submitted, "gave_up": not m,
           "cost_usd": final.get("total_cost_usd"), "turns": final.get("num_turns"),
           "is_error": final.get("is_error"), "subtype": final.get("subtype"), "usage": final.get("usage"),
           "model_usage": final.get("modelUsage"),
           "tool_calls": tool_calls, "mcp_servers": mcp_status, "seconds": round(time.time() - started, 1),
           "returncode": proc.returncode, "stderr_tail": proc.stderr[-500:]}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec))
    if os.environ.get("B6_KEEP_STREAM"):  # the whole transcript, to see what each tool call asked and got
        path.with_suffix(".stream.jsonl").write_text(proc.stdout)
    return rec


def run(arms: list[str], limit: int | None, jobs: int, repos: list[str] | None) -> None:
    qs = [json.loads(line) for line in (ROOT / "reports" / "b4" / "questions.jsonl").read_text().splitlines()]
    if repos:
        qs = [q for q in qs if q["repo"] in repos]
    if limit:
        qs = qs[:limit]
    work = [(q, arm) for q in qs for arm in arms]
    with ThreadPoolExecutor(jobs) as ex:
        for rec in ex.map(lambda qa: run_one(*qa), work):
            if rec and rec.get("skipped"):
                print("budget reached, stopping", flush=True)
                break
            if rec:
                print(rec["arm"], rec["id"], "cost", rec["cost_usd"], "turns", rec["turns"],
                      "tools", sum(rec["tool_calls"].values()), "gave_up" if rec["gave_up"] else "",
                      f"total ${spent():.2f}", flush=True)


def score() -> None:
    """B4's scorer on each arm's answers: sets against the SCIP truth, call paths hop by hop, judge for explain."""
    from . import agentqa
    from .qa_questions import REPOS, load

    agentqa.OUT = OUT  # the judge cache lands under reports/b6
    qs = {q["id"]: q for q in map(json.loads, (ROOT / "reports" / "b4" / "questions.jsonl").read_text().splitlines())}
    outs = {repo: load(repo)[0] for repo in REPOS}
    files = {repo: agentqa.keyed(repo) for repo in REPOS}
    per = []
    for d in sorted((OUT / "runs").iterdir()):
        for f in sorted(d.glob("*.json")):
            r = json.loads(f.read_text())
            q = qs[r["id"]]
            r["_repeat"] = "1"
            usage = r.get("usage") or {}
            per.append({"arm": d.name, "id": q["id"], "kind": q["kind"], "repo": q["repo"],
                        **agentqa.score_one(q, r, outs[q["repo"]], files[q["repo"]]),
                        "cost_usd": r.get("cost_usd") or 0.0, "turns": r.get("turns") or 0,
                        "tool_calls": sum(r["tool_calls"].values()),
                        "mcp_calls": sum(v for k, v in r["tool_calls"].items() if k.startswith("mcp__")),
                        "input_tokens": sum(usage.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
                        "seconds": r["seconds"], "gave_up": r["gave_up"]})
    (OUT / "scored.jsonl").write_text("".join(json.dumps(p) + "\n" for p in per))
    rows = {}
    for arm in [a for a in ARMS if any(p["arm"] == a for p in per)]:
        ps = [p for p in per if p["arm"] == arm]
        row = {"answers": len(ps)}
        lists = [p for p in ps if p["kind"] in ("callers", "callees", "impact")]
        row["list_f1"] = round(100 * sum(p["f1"] for p in lists) / max(1, len(lists)), 1)
        for kind in ("callers", "callees", "impact"):
            k = [p for p in ps if p["kind"] == kind]
            row[kind] = {m: round(100 * sum(p[m] for p in k) / max(1, len(k)), 1) for m in ("precision", "recall", "f1")}
        for kind in ("path", "negative"):
            k = [p for p in ps if p["kind"] == kind]
            row[kind] = round(100 * sum(p["correct"] for p in k) / max(1, len(k)), 1)
        k = [p for p in ps if p["kind"] == "explain" and p.get("judge") is not None]
        row["explain_judge"] = round(sum(p["judge"] for p in k) / max(1, len(k)), 2)
        for m in ("cost_usd", "turns", "tool_calls", "mcp_calls", "input_tokens", "seconds"):
            row["mean_" + m] = round(sum(p[m] for p in ps) / max(1, len(ps)), 3 if m == "cost_usd" else 1)
        row["gave_up"] = sum(p["gave_up"] for p in ps)
        rows[arm] = row
    (OUT / "results.json").write_text(json.dumps(rows, indent=1))
    print(json.dumps(rows, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--arms", default=",".join(ARMS))
    r.add_argument("--repos", default="")
    r.add_argument("--limit", type=int)
    r.add_argument("--jobs", type=int, default=4)
    sub.add_parser("score")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.arms.split(","), args.limit, args.jobs, [x for x in args.repos.split(",") if x] or None)
    else:
        score()


if __name__ == "__main__":
    main()
