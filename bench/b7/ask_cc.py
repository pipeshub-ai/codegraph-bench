"""B7 v2: the same questions to headless Claude Code in four setups, one agent and one prompt.

  python ask_cc.py <arm> [--questions questions_v2.jsonl] [--out b7v2] [--repeat 1] [--limit N] [--jobs 4]

Arms (every one has the Kafka checkout, docs/ removed, through Read, Grep and Glob):
  plain          nothing else
  graphify       + Graphify's graph of the checkout, in its maker's recommended setup (rules + hooks + MCP)
  pipeshub-code  + PipesHub's MCP server, code tools only (the code graph of apache/kafka)
  pipeshub       + PipesHub's MCP server, all tools: the code plus Kafka's Jira, KIPs (Confluence) and 4.3 docs
  dump           + the same Jira, Confluence and docs content as flat files under _company/ (export_dump.py)

Only the tools and one line naming what they index differ, so the comparison is the tools, not the
model or the instructions. Runs bill the API key in models.json (never printed), are isolated from
the user's Claude Code setup (--setting-sources project, --strict-mcp-config, no session persistence)
and land in reports/<out>/r<repeat>/cc-<arm>/ in ask.py's record shape plus token usage.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import setup

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SCRATCH = Path(os.environ.get(
    "B7G_SCRATCH", str(Path(__file__).resolve().parents[1] / "work" / "b7g")))
REPO = SCRATCH / "kafka"  # with graphify-out/; the other arms use a hard-linked copy without it
NOGRAPH = SCRATCH / "kafka-nograph"
DUMP = SCRATCH / "kafka-dump"  # the checkout plus _company/: export_dump.py's files
GRAPHIFY_BIN = ROOT / "bench" / "tools" / "graphify" / "bin" / "graphify"
MODEL = os.environ.get("B7_MODEL", "sonnet")
BUDGET_USD = float(os.environ.get("B7_BUDGET_USD", "150"))
PER_RUN_USD, MAX_TURNS = 2.0, 40
NON_CODE = ("sources", "search", "get_record_content", "download_record", "directory", "chat", "agents")

BASE = (
    "You answer questions from engineers at a company that runs Apache Kafka. You are in a checkout of "
    "Kafka's source code{tools}. Answer only from what your tools show, never from memory or outside "
    "knowledge: treat Kafka's code, tickets and design documents as your company's private material. "
    "Say plainly what you could not find. Be efficient."
)
GRAPHIFY_RULES = (
    " This project has a knowledge graph with god nodes, community structure, and cross-file relationships. "
    'Run `graphify query "<question>"` before your first grep, search or source-file read for a codebase '
    "question, and put this rule in every subagent prompt that explores code. Read raw files only for lines "
    'the graph or an earlier read already located. Use `graphify path "<A>" "<B>"` for relationships and '
    '`graphify explain "<concept>"` for focused concepts.'
)
TOOLS_LINE = {
    "plain": "",
    "graphify": ", and Graphify's knowledge graph of it (the mcp__graphify__* tools and the `graphify` command)",
    "pipeshub-code": ", and PipesHub, which indexes this repository (apache/kafka) as a code graph: its "
                     "pipeshub_code_* tools find symbols, callers, paths and text in the code",
    "pipeshub": ", and PipesHub, which indexes the company's sources: this repository (apache/kafka) as a code "
                "graph (pipeshub_code_* tools), plus the Kafka Jira project, the Kafka Confluence space with "
                "the design proposals (KIPs) and the Kafka 4.3 documentation site (pipeshub_search and "
                "pipeshub_get_record_content)",
    "dump": ", plus an export of the company's other sources as text files under _company/: the Kafka Jira "
            "project (_company/jira/, one file per issue), the Kafka Confluence space with the design proposals "
            "(KIPs) (_company/confluence/, one file per page) and the Kafka 4.3 documentation site (_company/docs/)",
}


def api_env() -> dict[str, str]:
    return {"ANTHROPIC_API_KEY": setup.MODELS["llm_judge"]["configuration"]["apiKey"]}


def arm_setup(arm: str) -> tuple[dict | None, list[str], list[str], dict[str, str]]:
    """(MCP server, extra CLI args, extra allowed tools, extra env)."""
    if arm == "graphify":
        hooks = {"hooks": {"PreToolUse": [
            {"matcher": "Bash|Grep", "hooks": [{"type": "command", "command": f"{GRAPHIFY_BIN} hook-guard search", "timeout": 10}]},
            {"matcher": "Read|Glob", "hooks": [{"type": "command", "command": f"{GRAPHIFY_BIN} hook-guard read", "timeout": 10}]},
        ]}}
        server = {"command": str(GRAPHIFY_BIN.parent / "graphify-mcp"), "args": [str(REPO / "graphify-out" / "graph.json")]}
        env = {"GRAPHIFY_OUT": str(REPO / "graphify-out"), "PATH": f"{GRAPHIFY_BIN.parent}:{os.environ['PATH']}"}
        return server, ["--settings", json.dumps(hooks)], ["Bash(graphify:*)", "mcp__graphify"], env
    if arm.startswith("pipeshub"):
        server = {"type": "http", "url": f"{setup.ORIGIN}/mcp",
                  "headers": {"Authorization": f"Bearer {setup.PAT_FILE.read_text().strip()}"}}
        extra = []
        if arm == "pipeshub-code":
            extra = ["--disallowedTools", ",".join(f"mcp__pipeshub__pipeshub_{t}" for t in NON_CODE)]
        return server, extra, ["mcp__pipeshub"], {}
    return None, [], [], {}


def spent(out: Path) -> float:
    total = 0.0
    for p in out.parent.glob("cc-*/*[0-9].json"):
        try:
            total += json.loads(p.read_text()).get("cost_usd") or 0.0
        except (OSError, ValueError):  # another arm mid-write
            pass
    return total


def ask_one(q: dict, arm: str, out: Path) -> dict | None:
    path = out / f"{q['id']}.json"
    if path.exists():
        return None
    if spent(out) >= BUDGET_USD:
        return {"skipped": "budget"}
    server, extra, allowed, env = arm_setup(arm)
    prompt = BASE.format(tools=TOOLS_LINE[arm]) + (GRAPHIFY_RULES if arm == "graphify" else "")
    tools = "Read,Grep,Glob" + (",Bash" if arm == "graphify" else "")
    cmd = ["claude", "-p", q["question"], "--model", MODEL, "--output-format", "stream-json", "--verbose",
           "--no-session-persistence", "--setting-sources", "project", "--strict-mcp-config",
           "--tools", tools, "--allowedTools", ",".join(["Read", "Grep", "Glob", *allowed]),
           "--append-system-prompt", prompt, "--max-turns", str(MAX_TURNS), "--max-budget-usd", str(PER_RUN_USD),
           *extra]
    if server:
        cfg = out / f".mcp-{q['id']}.json"
        cfg.write_text(json.dumps({"mcpServers": {arm.split("-")[0]: server}}))
        os.chmod(cfg, 0o600)
        cmd += ["--mcp-config", str(cfg)]
    started = time.time()
    error = None
    try:
        proc = subprocess.run(cmd, cwd={"graphify": REPO, "dump": DUMP}.get(arm, NOGRAPH), capture_output=True, text=True, timeout=1800,
                              env={**os.environ, **env, **api_env(), "MCP_TIMEOUT": "180000"})
        stdout, rc, stderr = proc.stdout, proc.returncode, proc.stderr
    except subprocess.TimeoutExpired as exc:
        stdout, rc, stderr, error = exc.stdout or "", None, "", "timeout"
    finally:
        if server:
            cfg.unlink(missing_ok=True)
    (out / f"{q['id']}.stream.jsonl").write_text(stdout if isinstance(stdout, str) else stdout.decode())
    used: list[str] = []
    final, mcp_status = {}, None
    for line in (stdout if isinstance(stdout, str) else stdout.decode()).splitlines():
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if ev.get("type") == "system" and ev.get("subtype") == "init":
            mcp_status = ev.get("mcp_servers")
        if ev.get("type") == "assistant":
            used += [p["name"] for p in ev.get("message", {}).get("content", []) if p.get("type") == "tool_use"]
        if ev.get("type") == "result":
            final = ev
    if final.get("is_error") and not error:
        error = final.get("subtype") or "error"
    u = final.get("usage") or {}
    rec = {"id": q["id"], "type": q["type"], "phase": f"cc-{arm}", "question": q["question"],
           "answer": final.get("result") or "", "tools": used, "citations": [], "error": error,
           "seconds": round(time.time() - started, 1), "cost_usd": final.get("total_cost_usd"),
           "turns": final.get("num_turns"), "model": MODEL,
           "input_tokens": sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")),
           "output_tokens": u.get("output_tokens"), "mcp_servers": mcp_status, "returncode": rc,
           "stderr_tail": (stderr or "")[-500:]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(rec, indent=1))
    tmp.replace(path)
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("arm", choices=tuple(TOOLS_LINE))
    ap.add_argument("--questions", default="questions_v2.jsonl")
    ap.add_argument("--out", default="b7v2")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    args = ap.parse_args()
    out = ROOT / "reports" / args.out / f"r{args.repeat}" / f"cc-{args.arm}"
    out.mkdir(parents=True, exist_ok=True)
    qs = [json.loads(line) for line in (HERE / args.questions).read_text().splitlines() if line.strip()]
    qs = qs[: args.limit] if args.limit else qs
    with ThreadPoolExecutor(args.jobs) as ex:
        for rec in ex.map(lambda q: ask_one(q, args.arm, out), qs):
            if rec and rec.get("skipped"):
                print("budget reached, stopping", flush=True)
                break
            if rec:
                mcp = sum(1 for t in rec["tools"] if t.startswith("mcp__") or t == "Bash")
                print(rec["id"], f"{rec['seconds']}s", "tools", len(rec["tools"]), "mcp", mcp, "cost", rec["cost_usd"],
                      "ERROR " + rec["error"] if rec["error"] else "", flush=True)


if __name__ == "__main__":
    main()
