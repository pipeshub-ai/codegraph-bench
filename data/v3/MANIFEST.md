# data/v3: B11 on PipesHub v1.2.0

The runs behind the post as published on 2026-11-23. B11's answer keys did not change: they are `truth-b11.tar.gz`
in `data/v2/` (`./bootstrap.sh data3` checks both and unpacks the keys).

| Asset | What it holds |
| --- | --- |
| `b11-reports.tar.gz` | One folder per codebase and model, `b11-vscode-<model>` and `b11-django32-<model>`, with every run behind the post: Claude Code alone and + Graphify from the earlier runs (2026-11-01..05) plus one run on 2026-11-23, and + PipesHub from two runs on release v1.2.0 (2026-11-22). Each run is its own `r<N>/runs/<arm>/<id>.json` (the answer, tokens, time, cost); `RUNS.md` says which run each `r<N>` is. `scored*.jsonl` scores every answer; `results*.json` is the mean over all runs (`-strip-src` for VS Code), the post's accuracy, tokens and cost. `b11-timing/`: the post's times. `timing.json` gives each setup's mean seconds on the same 60 questions per model, measured one run at a time with 8 questions in flight, and which run each comes from; the `main-*-v120-time-*` folders are the 60-question samples re-measured alone because their full runs overlapped others, and the three `anchor` folders are the 30-question check of how much that overlap slowed things down. |
| `SHA256SUMS` | Checksum of the archive. |

Agent transcripts (`*.stream.jsonl`) are not included. Local paths and the private snapshot repositories' owner are
replaced by `<bench>` and `<you>/codegraph-snapshot-`.
