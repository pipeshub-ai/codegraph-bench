# PipesHub code-graph benchmarks

The harness, pins and published results behind **"How Accurate Is Your Code Graph? A Benchmark of 4 Tools"**:
PipesHub's code graph measured against [Graphify](https://github.com/Graphify-Labs/graphify),
[codebase-memory-mcp](https://github.com/DeusData/codebase-memory-mcp) and
[CodeGraphContext](https://github.com/CodeGraphContext/CodeGraphContext).

| Benchmark | What it measures |
| --- | --- |
| B1 | Accuracy of calls, imports and inheritance on 9 open-source repositories, against SCIP answer keys |
| B2 | Build time, graph size and memory on a 29,195-file repository (home-assistant), into Neo4j |
| B3 / B3b | Whether the graph stays right as code changes: live through GitHub and crashes (B3), and incremental vs fresh builds for all four tools (B3b) |
| B5 | Whether graph signals help find the file to fix: SWE-bench Lite and Verified, ContextBench; with and without an LLM agent |
| B11 | Claude Code alone, + Graphify, + PipesHub, with Haiku, Sonnet and Opus, on 300 questions each about VS Code's core and Django 3.2 (post: "Haiku with a code graph matches Sonnet for about a tenth of the cost") |
| B7 | Apache Kafka questions answered from the code alone and with the project's Jira, wiki and docs connected through PipesHub (post: "A code graph isn't enough") |

## Quick start

```bash
./bootstrap.sh smoke
bench/run.sh truth requests
TOOLS="pipeshub graphify" bench/run.sh b1 requests
bench/run.sh verify b1
```

That reproduces the `requests` row of the accuracy table in about 15 minutes. **[AGENTS.md](AGENTS.md)** has
the full procedure for every benchmark: prerequisites, commands, costs, the PipesHub commit behind each number,
and the pitfalls. It is written so a coding agent can follow it without other context.

Published numbers: `expected/published/` (tables) and `expected/reports/` (what `verify.py` checks). Answer keys,
tool outputs and per-issue results: GitHub release `data-v1` (B1–B5) and `data/v2/` in this repo (B11, B7). B11's
questions, each with its correct answer, are `bench/b11/questions-{vscode,django}-300.jsonl`.

B11 and B7 run against a live PipesHub stack with Claude Code and need model keys: see AGENTS.md sections 3h and 3i.

This benchmark was run by the PipesHub team and includes PipesHub. MIT licensed (see LICENSE).
