# data/v2: B11 and B7

| Asset | What it holds |
| --- | --- |
| `truth-b11.tar.gz` | B11 answer keys, unpacked into the repo root: `bench/truth/vscode/` (SCIP, the VS Code snapshot) and `bench/truth/django32/` (SCIP plus calls seen running Django 3.2's tests; `edges_scip.jsonl` is the SCIP part, `edges_manual.jsonl` the hand-checked additions, `runtime_overrides.jsonl` traced edges left out for review). `files.jsonl` lists the files each key covers. |
| `b11-reports.tar.gz` | Every B11 answer and its score. `b11-*` is VS Code, `b12-*` is Django. `*-q300-<model>`: the runs behind the "Claude Code alone" and "+ Graphify" rows. `*-final-<model>`: the runs behind the "+ PipesHub" rows. `*-time-pipeshub-<model>`: 60 questions rerun at normal load, behind the PipesHub time column; `*-time-alone-<model>`: Claude Code alone on the same 60. Each folder: `questions.jsonl`, `r<N>/runs/<arm>/<id>.json` (the answer, tokens, time, cost), `scored*.jsonl` (per answer), `results*.json` (means; `-strip-src` for VS Code). |
| `b7-reports.tar.gz` | `b7v2/`: every Kafka answer (`r<N>/<arm>/<id>.json`), both judges' grades (`judge-a/`, `judge-b/`), `graded.jsonl` and `results.json`. The post's setups are `cc-plain`, `agent-code-only` and `agent-with-company`. |
| `SHA256SUMS` | Checksums of the three archives. |

Agent transcripts (`*.stream.jsonl`) are not included. A handful of answers named the harness's local path or
the private snapshot repositories; those strings are replaced by `<bench>` and `<you>/codegraph-snapshot-`.
