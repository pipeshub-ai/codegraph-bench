"""B7, code-graph arm: the same 90 questions to headless Claude Code (Sonnet) with Graphify's MCP server.

  python ask_graphify.py [--limit N] [--jobs 4]

The agent sees apache/kafka at kafka_trunk.sha with docs/ removed (as PipesHub's code-only phase
excluded it), through Read, Grep, Glob and Graphify's graph of that checkout. No web access, no
writes. Kafka's KIPs and issues are public and a model may remember them; the prompt holds the agent
to what the checkout shows, as it would be for a private company's code. Runs are isolated from the user's Claude Code setup as in B6 (--setting-sources project,
--strict-mcp-config, no session persistence). Answers land in reports/b7/graphify/ in ask.py's
record shape, so grade.py scores them with the same judge.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
OUT = ROOT / "reports" / "b7" / "graphify"
SCRATCH = Path(os.environ.get(
    "B7G_SCRATCH", str(Path(__file__).resolve().parents[1] / "work" / "b7g")))
REPO = SCRATCH / "kafka"
GRAPHIFY_MCP = ROOT / "bench" / "tools" / "graphify" / "bin" / "graphify-mcp"
BUDGET_USD = float(os.environ.get("B7G_BUDGET_USD", "40"))
PER_RUN_USD = 1.0
MAX_TURNS = 30
APPEND = (
    "You answer questions about Apache Kafka. You have this checkout of the Kafka source code and a "
    "code graph of it (the graphify tools). Answer only from what the checkout and the graph show, "
    "never from memory or outside knowledge: treat this as a private codebase you have never seen. "
    "Name the classes, configs, KIPs or Jira issues the question asks for when you find them, and say "
    "plainly when the code does not tell you. Be efficient."
)


def spent() -> float:
    return sum(json.loads(p.read_text()).get("cost_usd") or 0.0 for p in OUT.glob("*.json"))


def ask_one(q: dict) -> dict | None:
    path = OUT / f"{q['id']}.json"
    if path.exists():
        return None
    if spent() >= BUDGET_USD:
        return {"skipped": "budget"}
    cfg = SCRATCH / "graphify-mcp.json"
    cfg.write_text(json.dumps({"mcpServers": {"graphify": {
        "command": str(GRAPHIFY_MCP), "args": [str(REPO / "graphify-out" / "graph.json")]}}}))
    cmd = ["claude", "-p", q["question"], "--model", "sonnet", "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--setting-sources", "project", "--strict-mcp-config",
           "--mcp-config", str(cfg), "--tools", "Read,Grep,Glob",
           "--allowedTools", "Read,Grep,Glob,mcp__graphify", "--append-system-prompt", APPEND,
           "--max-turns", str(MAX_TURNS), "--max-budget-usd", str(PER_RUN_USD)]
    started = time.time()
    error = None
    try:
        proc = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True, timeout=1800,
                              env={**os.environ, "MCP_TIMEOUT": "180000"})
        stdout, rc, stderr = proc.stdout, proc.returncode, proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout, rc, stderr, error = exc.stdout or "", None, "", "timeout"
    tools: list[str] = []
    final, mcp_status = {}, None
    for line in (stdout if isinstance(stdout, str) else stdout.decode()).splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            mcp_status = ev.get("mcp_servers")
        if ev.get("type") == "assistant":
            tools += [p["name"] for p in ev.get("message", {}).get("content", []) if p.get("type") == "tool_use"]
        if ev.get("type") == "result":
            final = ev
    if final.get("is_error") and not error:
        error = final.get("subtype") or "error"
    rec = {"id": q["id"], "type": q["type"], "phase": "graphify", "question": q["question"],
           "answer": final.get("result") or "", "tools": tools, "citations": [], "error": error,
           "seconds": round(time.time() - started, 1), "cost_usd": final.get("total_cost_usd"),
           "turns": final.get("num_turns"), "mcp_servers": mcp_status, "returncode": rc,
           "stderr_tail": (stderr or "")[-500:]}
    OUT.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1))
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    qs = [json.loads(line) for line in (HERE / "questions.jsonl").read_text().splitlines() if line.strip()]
    qs = qs[: args.limit] if args.limit else qs
    with ThreadPoolExecutor(args.jobs) as ex:
        for rec in ex.map(ask_one, qs):
            if rec and rec.get("skipped"):
                print("budget reached, stopping", flush=True)
                break
            if rec:
                graphify_calls = sum(1 for t in rec["tools"] if t.startswith("mcp__graphify"))
                print(rec["id"], f"{rec['seconds']}s", "tools", len(rec["tools"]), "graphify", graphify_calls,
                      "cost", rec["cost_usd"], f"total ${spent():.2f}", "ERROR " + rec["error"] if rec["error"] else "",
                      flush=True)


if __name__ == "__main__":
    main()
