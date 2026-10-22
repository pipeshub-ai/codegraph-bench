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
tool outputs and per-issue results: GitHub release `data-v1`.

This benchmark was run by the PipesHub team and includes PipesHub. MIT licensed (see LICENSE).
