# Reproducing the code-graph benchmarks: guide for coding agents

This repository reproduces every number in the blog post **"How Accurate Is Your Code Graph? A Benchmark of
4 Tools"**: PipesHub's code graph against Graphify, codebase-memory-mcp and CodeGraphContext. Read this file
end to end before running anything. Follow the steps in order; each one says what it costs, how long it takes,
where its output lands and how to check it.

It also reproduces two later posts: **"Haiku with a code graph matches Sonnet for about a tenth of the cost"**
(B11: Claude Code alone, with Graphify and with PipesHub, on 300 questions about each of two codebases, with
three models) and **"A code graph isn't enough"** (B7: Apache Kafka questions answered from the code alone and
with the project's Jira, wiki and docs connected).

The repository holds the harness, the pins and the published results. Everything large (subject repositories,
tools, datasets, answer keys) is fetched by `bootstrap.sh` into git-ignored folders.

## 1. What each benchmark measures

| Id | Question | Needs an LLM / API key | Main script |
| --- | --- | --- | --- |
| B1 | How accurate are each tool's code relationships (calls, imports, imports of a symbol, inheritance) on 9 real repositories, against SCIP answer keys? | No | `bench/run.sh b1` |
| B2 | How long does PipesHub take to build a graph into Neo4j, how big is it, how much memory, how fast do agent tools answer? Same build time for two other tools on home-assistant (29,195 files). | No | `bench/run.sh b2`, `b2-speed`, `b2-tools` |
| B3 | Through the live product (GitHub connector, docker compose stack): is the graph right after a first sync, after 7 code changes pushed 3 times each, and after services are killed mid-sync? | Yes: PipesHub needs an LLM and an embedding model; a GitHub token | `bench/run.sh b3` |
| B3b | Offline: after each of 7 changes, does each tool's incrementally updated graph equal a fresh full build? | No | `bench/run.sh b3b` |
| B5a | Without an LLM: does adding graph signals to search rank the file to fix higher (SWE-bench Lite and held-out Verified)? | Embeddings only (dense arms) | `bench/run.sh b5a` |
| B5b | With an LLM agent: does adding PipesHub's code-graph tools help it find the file to fix (SWE-bench Lite, 300 issues)? | Yes | `bench/run.sh b5b` |
| B5c | ContextBench (500 tasks, 8 languages): the same, no-LLM retrieval and LLM agent, with the official scorer | Embeddings + LLM | `bench/run.sh b5c` |
| B11 | Does a code graph help a coding agent answer questions about code? Claude Code alone, + Graphify, + PipesHub, with Haiku, Sonnet and Opus, on 300 questions each about VS Code's core (TypeScript, 4,872 files) and Django 3.2 (Python, 858 files), scored against compiler (and, for Django, runtime) answer keys | Yes: Claude Code (account or API key); PipesHub needs an LLM, an embedding model and a GitHub token | `bench/run.sh b11` |
| B7 | Kafka: how much of a real engineer's question can be answered from the code alone, and how much once the project's Jira, wiki and docs site are connected? 90 questions, Claude Code alone and PipesHub's agent before and after the three connectors, two LLM graders | Yes: Claude Code, PipesHub's LLM + embeddings, two grader models | `bench/run.sh b7` |

`expected/published/*.json` holds every published table with its caption and notes; `expected/reports/` holds
the same numbers as machine-readable files that `verify.py` compares against.

## 2. Prerequisites

Measured on: Linux x86_64 (Ubuntu 24.04), 32 vCPU (AMD EPYC), 125 GB RAM, shared with other work. You need:

- **OS:** Linux x86_64 (the release binaries are linux-amd64). macOS may work with changes; not tested.
- **CPU/RAM:** 8+ cores and 32 GB RAM for B1/B2 on the large repositories; CodeGraphContext needs up to 24 GB on
  guava and could not finish the PipesHub backend within 90% of 125 GB. The smoke run needs 4 cores, 8 GB.
- **Disk:** ~5 GB for the smoke run; ~60 GB for everything (subjects 0.8 GB, SCIP indexes 0.7 GB, tools 4 GB,
  tool working copies 1.5 GB, Maven cache 3 GB, SWE-bench 5.5 GB, ContextBench clones 10 GB, two PipesHub venvs ~6 GB each).
- **Software:** `git`, `python3.12` (3.12.3 measured) with `venv`, Python 3.11 + [`uv`](https://docs.astral.sh/uv/)
  (ContextBench's scorer only), Node.js 24 with `npx` (scip-python, scip-typescript), Docker (Neo4j for B2,
  the full stack for B3, scip-go and scip-java images), `curl`, GNU `time` at `/usr/bin/time`, `vmstat`, `free`.
- **Keys (only B3 and B5):** copy `models.example.json` to `models.json` and fill in `llm_1` (an OpenAI-compatible
  chat model; measured: `gpt-5.6-luna` on Azure OpenAI) and `embedding_1` (measured: `text-embedding-3-small` on
  Azure OpenAI). PipesHub's provider names apply (`azureOpenAI`, `openAI`, `anthropic`, ...). B3 also needs a
  GitHub personal access token with `repo` scope as `GITHUB_FIXTURE_TOKEN` (environment or a `.env` file at the
  repo root) and your own copy of the fixture repository (step 3e).
- **B11 and B7 also need:** the Claude Code CLI (`claude`, 2.1.x; measured up to 2.1.318), logged in to an
  account that may run Haiku, Sonnet and Opus, or an Anthropic key via `B6_ANTHROPIC_KEY_FROM`; a GitHub token
  (`GITHUB_FIXTURE_TOKEN`) that can read the repositories the connectors sync; for B7's graders, `llm_judge`
  (Anthropic) and `llm_2` (GPT on Azure) in `models.json`.

Configuration lives in `bench/env.sh` (defaults) and an optional `config.env` (overrides; see
`config.env.example`). Every script sources `bench/env.sh`; nothing else hard-codes a path.

## 3. Steps

Run everything from the repository root.

### 3a. Machine load (always)

The harness pauses heavy steps while the machine is over 90% CPU (`wait_cpu` in the scripts) and stops a tool
when memory passes 88% (the run is then marked `capped (memory)`). Keep other work off the machine during timed
runs (B2): timings were measured with ~20% run-to-run noise on a shared machine.

### 3b. Smoke run first (about 15 minutes, no keys)

```bash
./bootstrap.sh smoke            # PipesHub at the B1 commit + its venv, requests, Graphify/cbm/cgc, scip CLIs
bench/run.sh truth requests     # SCIP answer key for requests (or ./bootstrap.sh data, then skip this)
TOOLS="pipeshub graphify" bench/run.sh b1 requests
bench/run.sh verify b1          # requests rows: ok; everything else: "not run"
```

Expected: `ok b1-v2/pipeshub/requests/report.json` and `ok b1-v2/graphify/requests/report.json`, F1 on
hand-written code 92.5 (PipesHub) and 91.4 (Graphify), see `reports/b1-v2/COMPARISON.md`. If these do not
match, stop and read section 5 before running anything larger.

### 3c. B1: accuracy (about 4 hours on 32 cores; no keys)

```bash
./bootstrap.sh pipeshub         # every pinned PipesHub commit as a worktree under work/
./bootstrap.sh subjects         # the 9 subject repositories at their pinned commits (+ home-assistant for B2)
./bootstrap.sh tools && ./bootstrap.sh scip
bench/run.sh truth              # all 9 answer keys (~1 h: django and the PipesHub backend take longest), or ./bootstrap.sh data
bench/run.sh b1                 # all 9 repositories x 4 tools; CodeGraphContext dominates the time (guava ~85 min)
bench/run.sh verify b1
```

Output: `reports/b1-v2/<tool>/<repo>/report.json` and `reports/b1-v2/COMPARISON.md` (the blog's table is the
"Hand-written code" section; F1 is the number in brackets). Per-repository build times as measured are in
`expected/b1-COMPARISON.md` ("Time and peak memory").

### 3d. B2: scale (about 2 hours; no keys; Docker)

```bash
bench/run.sh b2          # each subject into a fresh Neo4j 5.26 (2 GB heap, 1 GB page cache, --memory 3g --cpus 4, APOC)
bench/run.sh b2-speed    # home-assistant at the speed commit, 8 files in flight: graph_seconds ~271
bench/run.sh b2-tools    # Graphify 0.9.92 (~1,880 s, ~10 GB) and codebase-memory-mcp 0.11.0 (~110 s) on home-assistant
bench/run.sh verify b2 b2-speed b2-tools
```

Edge counts and graph size are checked exactly (home-assistant: 474,086 code edges); timings within ±25% and
only the published ones. Agent-tool latency (`tools` in each report) is recorded but not checked: it depends on
the machine.

### 3e. B3: the live product (about 3 hours; LLM + embedding + GitHub token; Docker)

1. Fork or copy `https://github.com/tushar1245/pipeshub-codegraph-fixture-b3` into your account (keep its
   `step-0` ... `step-7` tags), set `B3_REPO=<you>/pipeshub-codegraph-fixture-b3` in `config.env`, and point
   `FIXTURE_DIR` at a local clone whose `origin` is your copy (`./bootstrap.sh fixtures` clones the original;
   change its remote). **B3 force-pushes each step to that repository's `main`.**
2. `cp bench/b3/b3.env.example bench/b3/b3.env` and fill in the `<generate>` values.
3. `bench/run.sh b3` builds PipesHub's image from the pinned commit, brings up the stack (`docker compose -p
   cg-b3`), creates the org, LLM, embeddings and the GitHub connector, syncs, then pushes steps 1..7 three times
   and finally runs the crash-recovery scenarios. Output: `reports/b3/steps.jsonl`, `reports/b3/recovery.jsonl`.
4. `bench/run.sh verify b3`: every step must have 0 false positives and precision 1.0; recall within ±1 point.
   Time-to-ready (6–25 s per push, 42–123 s after a kill) is recorded, not checked.

GitHub rate limits: a token's 5,000 requests/hour is enough for one full run; repeated runs within an hour can
pause the connector until the limit resets (it resumes on its own).

### 3f. B3b: incremental updates, 4 tools (about 2 hours; no keys)

```bash
./bootstrap.sh fixtures
bench/run.sh b3b          # PipesHub drift (minutes), then Graphify, codebase-memory-mcp, CodeGraphContext (cgc is slow)
bench/run.sh verify b3b
```

Output: `reports/b3b/pipeshub-drift/` (PipesHub; step 0: 99.9 / 89.4 (94.4), step 7: 99.9 / 89.1 (94.2) on
hand-written code) and `reports/b3b/summary.json` (the other three). Run the CodeGraphContext arm alone: its Kuzu
database accepts one process at a time.

### 3g. B5: SWE-bench and ContextBench (keys; see costs)

```bash
./bootstrap.sh datasets   # SWE-bench Lite + Verified at pinned revisions, ContextBench at its commit, checksums verified
bench/run.sh b5a          # ~6 h, ~140M embedding tokens (~$2.80 at text-embedding-3-small prices) incl. the retry
bench/run.sh b5b          # ~5 h, ~57M prompt tokens across both arms (77% cached at measurement)
bench/run.sh b5c          # ~10 h: 500 tasks retrieval + 2 agent arms (~102k / 136k prompt tokens per task) + scoring
bench/run.sh verify b5a b5b b5c
```

Tolerances: B5a ±0.5 points (embeddings come from an API); B5b ±2 points (LLM agent); ContextBench ±1 point
(the agent tier drifts more than the retrieval tier; read the per-set rows).

### 3h. B11: Claude Code with and without a code graph, three models (keys; a day)

Two codebases, each rebuilt from upstream and checked by its git tree hash: VS Code's `src/vs/{base,editor,platform}`
(placed at `vs/`) and Django 3.2's `django/` package. PipesHub indexes them through its GitHub connector, so the
snapshots go to two GitHub repositories of yours (empty when created; private is fine). Set in `config.env`:
`B11_VSCODE_REPO=you/codegraph-snapshot-vscode` and `B11_DJANGO_REPO=you/codegraph-snapshot-django32`.

```bash
./bootstrap.sh tools                         # Graphify 0.9.92 (the Graphify arm)
./bootstrap.sh data2                         # answer keys -> bench/truth/vscode, bench/truth/django32
./bootstrap.sh pipeshub "$(python3 -c "import tomllib;print(tomllib.load(open('pins.toml','rb'))['pipeshub']['b11'])")"
bench/b11/snapshot.sh build                  # fetch both upstream commits, rebuild, verify the trees
bench/b11/snapshot.sh push                   # to $B11_VSCODE_REPO and $B11_DJANGO_REPO
bench/b11/snapshot.sh graphify               # Graphify's graphs of both trees (about 2 minutes)
cp bench/b11/b11.env.example bench/b11/b11.env   # generate the secrets it names
PIPESHUB_REF=<the b11 pin> bench/b11/stack.sh build && bench/b11/stack.sh up    # http://127.0.0.1:3960
(cd bench/b11 && "$PY" setup.py && "$PY" setup_django.py)   # admin, LLM, embeddings, PAT, both connectors
bench/b11/wait_index.sh                      # until both repos are indexed and their code graphs built
bench/run.sh b11                             # MODELS, REPOS, ARMS, REPEATS, JOBS: see bench/run_b11.sh
bench/run.sh verify b11
```

- **Setups.** *Claude Code alone*: the checkout through Read, Grep, Glob and read-only shell. *+ Graphify*: also
  Graphify's MCP server, rules and hooks as its README recommends. *+ PipesHub*: also the stack's
  `/mcp?tools=code` with a personal access token, and three prompt lines: one asking the agent to start with PipesHub's
  tools, one on when a call on a value of unknown type counts toward a path, and one asking for independent
  lookups in the same turn (`B6_PIPESHUB_HINT_EXTRA` in `run_b11.sh`). Every setup gets the same base prompt and
  questions.
- **Scoring.** Each answer is a list of functions, scored by F1 against the key. VS Code's key is SCIP's;
  Django's adds calls seen running Django's own tests (rebuild with `bench/b11/truth/trace.sh` and
  `cg_eval.b12_runtime`; optional, the key ships in `data-v2`).
- **Cost** at API prices, per question per setup: Haiku about $0.006–0.012, Sonnet $0.05–0.08, Opus
  $0.12–0.18. One run of everything (600 questions x 3 setups x 3 models) is about $400. The published runs
  used a Claude account (`B6_ANTHROPIC_KEY_FROM` empty) and report costs at API prices.
- **Time.** With 12 questions in flight against one stack, 300 questions with one model and setup took about
  15 minutes with Sonnet and up to an hour with Haiku (more turns per question). Indexing the two snapshots
  takes longer than any run; `wait_index.sh` says when it is done.
- **Check.** `verify.py b11` compares each setup's overall accuracy within 3 points and, for the five question kinds
  with 20 or more questions (callers, debug, flow, impact, locate), within 8; the smaller kinds move more than that
  between identical runs. Tokens, time and cost are reported, not checked.

### 3i. B7: Kafka, with and without the project's knowledge (keys; half a day)

```bash
./bootstrap.sh pipeshub 0b33bc842            # pins.toml [pipeshub] b7
cp bench/b7/b7.env.example bench/b7/b7.env   # generate the secrets it names
PIPESHUB_REF=0b33bc842 bench/b7/stack.sh build && bench/b7/stack.sh up    # http://127.0.0.1:3950, plus jira-proxy
(cd bench/b7 && "$PY" setup.py)              # stage 1: admin, LLM, embeddings, GitHub connector on $B7_KAFKA_REPO
(cd bench/b7 && "$PY" setup_phase_b.py)      # stage 2: Jira (KAFKA, since 2022), Confluence (KAFKA space), docs site
bench/b7/checkout.sh                         # Claude Code's checkout: kafka_trunk.sha, docs/ removed
bench/run.sh b7                              # CC_ARMS=plain by default; PipesHub's agent with code only and with all four sources; grading
bench/run.sh verify b7
```

- **Setups in the post.** *Claude Code alone* (`cc-plain`): the checkout. *PipesHub, repository only*
  (`agent-code-only`): PipesHub's agent limited to the GitHub connector. *PipesHub, repository + knowledge*
  (`agent-with-company`): the same agent with all four connectors. The scripts also run the setups the first
  version of the post had (`CC_ARMS="graphify pipeshub-code pipeshub dump"`).
- **The code connector syncs a repository's default branch.** The published run synced `apache/kafka` at
  `kafka_trunk.sha`; to pin it, point `B7_KAFKA_REPO` at a fork whose default branch is that commit.
- **Apache's Jira** is read through `jira_proxy.py` (a local read-only proxy in the stack) so the Jira Data Center
  connector can page it; a company points the connector at its own Jira.
- **Grading.** `grade_v2.py`: `llm_judge` (Claude Sonnet) and `llm_2` (GPT) score each answer as correct, partly
  correct or wrong against the reference answer and its key facts; the post reports judge A's "correct".
- **Check.** `verify.py b7` compares each setup's share correct per judge within 5 points.

## 4. Pins and provenance

`pins.toml` pins every input. The PipesHub code under test is on the public fork
`tushar1245/pipeshub-ai`, branch `backup/code-graph-pre-squash-2026-10-21` (the pull request's history before it
was squashed). Each benchmark ran at a specific commit of that branch; `bench/run.sh` checks it out for you:

| Numbers | PipesHub commit | How we know |
| --- | --- | --- |
| B1 PipesHub rows (all 9 repositories) | `6c6e29ab5` | reproduced exactly from a clean clone of this repository (every true/false positive count). The Python, Go and Java rows were measured on 2026-10-19 on the working tree that became this commit (they also reproduce at `8ce111f5a`); the TypeScript rows were re-run on 2026-10-20 after the barrel re-export fix |
| B1 competitor rows | tool versions in pins.toml | run 2026-10-19 with `bench/run_b1_v2.sh` |
| B2 edges, store size, memory, latency | `f9a196bfc` | `reports/b2` written 2026-10-17 13:54–14:21; that commit 13:53 |
| B2 271 s build time (home-assistant) | `b479eadf0` | the `speed3` run (2026-10-20 23:42) used the uncommitted speed work, committed as `b479eadf0`; the commit records edge sets identical to before |
| B2 631 s "before" | `8e53d5a11`, 1 file in flight | the `base` run (2026-10-20 23:23), on the PR branch just before the speed commit |
| B2 Graphify 1,880 s / codebase-memory-mcp 110 s | Graphify 0.9.92 / cbm 0.11.0 | `run_tools_large.sh` |
| B3 live | `ec84a057f` | stack image built from the worktree at that commit; last results 2026-10-19 13:34 |
| B3b PipesHub | `6c6e29ab5` | the drift run (2026-10-19 15:22) used work in progress later committed as `6c6e29ab5`; reproduced exactly at that commit, while `8ce111f5a` finds 16 fewer edges |
| B5a, B5-G1 retry | `f9a196bfc` | runs 2026-10-18 – 2026-10-19 09:15 |
| B5b search arm / graph arm (90.3%) | `f9a196bfc` / `4b3b974b7` | the graph arm was re-run on 2026-10-20 without a false "graph stale" warning |
| B5c ContextBench | `4b3b974b7` (inferred) | the scripts' default checkout during the runs (2026-10-20/21); an earlier note recorded `b479eadf0` for the agent tier. Their code-graph code is the same for these tools, so expect no difference beyond noise |

The subject `pipeshub-ai` (B1/B2) is the pull request's own tree at `ec75f1812`, unpacked without `.git`, as it
was measured.

Not pinned: the `sourcegraph/scip-java:latest` and `sourcegraph/scip-go:latest` images were pulled on
2026-10-16 and their digests were not recorded (scip-go itself is the pinned v0.2.7 binary; the image only
supplies the Go toolchain). If a newer image changes a Java answer key, use the published keys
(`./bootstrap.sh data`) to separate a truth change from a tool change.

### Provenance: B11 and B7

| Numbers | What produced them |
| --- | --- |
| B11 Claude Code alone, + Graphify | Claude Code 2.1.x with Graphify 0.9.92, 2026-11-01..05. VS Code: two runs with Haiku, one with Sonnet and Opus. Django: two runs with Haiku, three with Sonnet; Claude Code alone twice and Graphify once with Opus. No PipesHub involved |
| B11 + PipesHub: accuracy, tokens, cost | An experiment build of PipesHub (`exp/cg-ab2` at `fec6544a9`, not public) on `main` of 2026-11-05, with four switches on: the definition each unlinked call site sits in, the calls to follow after a path search that found nothing (short listing), and callers and impact through overridden methods. One run per model and codebase, 2026-11-05..06, with the three prompt lines in `run_b11.sh`. The same behaviour without switches is #5472 (merged), #5473 and #5474; the `b11` pin merges them. Their review fixes (a hard cap on the calls looked up, callable kinds filtered before the two definitions are taken, failed walks skipped and logged) came after the runs, and the pin has not been re-run, so expect agreement within the tolerance, not exactly |
| B11 + PipesHub: time | 60 of the questions (`*-time-pipeshub-*`) rerun on the same build with at most 6 in flight, because the full runs shared the stack with up to 24 questions in flight and its tool calls slowed. The Claude Code alone and Graphify times are from their full runs; `*-time-alone-*` has Claude Code alone on the same 60 questions for a like-for-like check |
| B7 | Image built 2026-10-21 05:17 from the code-graph pull request's tree; its last commit then was `0b33bc842` (inferred from the times). Runs 2026-10-30: Claude Code (Sonnet) twice per setup, PipesHub's agent (`llm_1`) once per setup |

## 5. Pitfalls we hit (read before debugging)

- **Wrong PipesHub commit.** Numbers move with the commit. Always go through `bench/run.sh` (it sets
  `PIPESHUB_REF`), or set `PIPESHUB_REF` yourself and run `./bootstrap.sh pipeshub <ref>` first.
- **CPU over 90%.** Scripts wait (`wait_cpu`); a run that seems stuck is usually waiting. Timings taken on a busy
  machine are not comparable.
- **Memory.** CodeGraphContext uses 20+ GB on django and guava; the guard stops a tool at 88% machine memory and the
  table says `capped (memory)`. Neo4j for B2 is capped at 3 GB by design (the shipped limit).
- **CodeGraphContext's Kuzu database is single-process.** Never run two cgc processes on one database; run its arm
  alone.
- **client-go is mostly generated code.** 2,010 of its 2,551 files are generated and PipesHub skips them by policy;
  the blog's table is "hand-written code" (those files left out for every tool). The all-files table is also in
  `COMPARISON.md`.
- **scip-python memory.** Indexing the PipesHub backend needs `NODE_OPTIONS=--max-old-space-size=12288` (set in
  `bench/make_truth.sh`).
- **scip-java builds.** guava is indexed with `-DskipTests -Dmaven.javadoc.skip=true -pl guava -am`; `target/`
  folders are removed afterwards so the tree matches what the tools see.
- **B3 needs its own repository.** It force-pushes; never point `B3_REPO` at a repository you care about.
- **GitHub rate limits** pause the B3 connector for up to an hour; it resumes by itself.
- **Timing noise.** ±25% on the shared measuring machine (e.g. the PipesHub backend: 278 s and 335 s in two runs).
- **Generated outputs are large.** `bench/work-v2` (tool copies, ~1.5 GB) and `reports/b2-speed` edge dumps
  (~2 GB) can be deleted after verifying.

- **B11: too many questions in flight breaks the PipesHub arm silently.** Around 30–48 concurrent agents, the
  stack's Neo4j connection pool ran out: tool calls came back as timeouts, "Tool not found" or "No indexed
  repository you can read", and about 22% of answers were affected. Keep `JOBS` so the total is 12 or less, and
  check transcripts for those strings (`B6_KEEP_STREAM=1` keeps them) before trusting a table. The published
  numbers re-ran every affected question at 12 in flight.
- **B11: a usage limit is scored as a wrong answer.** On an account, `usage_limit_reached` ends the run; the run
  script counts them at the end. Delete those answers' `.json` and rerun (finished answers are skipped).
- **B11: "not found" for an indexed repo.** If every code tool says the repository is not indexed or not
  readable after a sync, the org READER edge on its record group was dropped; `keep_acl.sh` (started by the run
  script) re-adds it.
- **B11: answers in VS Code's real layout.** The snapshot drops `src/`; agents sometimes answer `src/vs/...`.
  The VS Code runs score with `B8_STRIP_SRC=1`, which accepts both.
- **B7: the code connector follows the default branch** (see 3i), and the Kafka answers depend on what the
  tracker and wiki hold on the day you sync; expect drift beyond the tolerance on questions about recent issues.

## 6. Published data (release `data-v1`)

`./bootstrap.sh data` downloads release `data-v1` of `pipeshub-ai/codegraph-bench` into `work/data`,
checks `SHA256SUMS`, and unpacks the SCIP answer keys into `bench/truth/`. Manually:

```bash
mkdir -p work/data && cd work/data
for a in SHA256SUMS MANIFEST.md truth.tar.gz b1-reports.tar.gz b2-reports.tar.gz b3-reports.tar.gz b5-reports.tar.gz; do
  curl -fsSLO "https://github.com/pipeshub-ai/codegraph-bench/releases/download/data-v1/$a"; done
sha256sum -c SHA256SUMS
```

`MANIFEST.md` in the release lists each asset: the answer keys, every tool's B1/B3b edges, the B1/B2/B3/B3b/B5
reports and the B5 per-issue outputs.

Release **`data-v2`** (`./bootstrap.sh data2`) adds B11's answer keys (`truth-b11.tar.gz`, unpacked into
`bench/truth/vscode` and `bench/truth/django32`), every B11 answer with its scores (`b11-reports.tar.gz`: the
`b11-q300-*`/`b12-q300-*` runs behind the Claude Code alone and Graphify rows, the `b11-final-*`/`b12-final-*`
runs behind the PipesHub rows, and the `*-time-*` samples behind the PipesHub time column; `b11` is VS Code, `b12`
Django) and B7's answers and both judges' grades
(`b7-reports.tar.gz`). Agent transcripts are not included.

## 7. Layout

```
bootstrap.sh            set-up, in sections (see its header)
pins.toml               every version and commit
bench/env.sh            shared settings (config.env overrides)
bench/run.sh            one entry per benchmark + verify
bench/make_truth.sh     SCIP indexes -> answer keys (scip_truth.py)
bench/run_b1_v2.sh      B1 (all tools, memory guard)      bench/adapters/   each tool's graph -> common edge format
bench/run_b2*.sh, run_tools_large.sh, compile_b2.py       B2
bench/b3/               B3 live stack, steps, recovery     bench/b3b/        B3b incremental runs + rescore
bench/run_b5*.sh        B5a/B5b                            bench/contextbench/  B5c
bench/b11/               B11: snapshot.sh, stack, setup, keep_acl, wait_index, the two 300-question sets, truth/ (Django trace)
bench/run_b11.sh         B11 runner (3 setups x models x repos)
bench/b7/                B7 (Kafka): stack, two-stage setup, jira_proxy, checkout.sh, ask.py (PipesHub agent), ask_cc.py (Claude Code), grade_v2.py, questions_v2.jsonl
bench/run_b7v2.sh        B7 runner
src/cg_eval/            the harness: runs PipesHub's own parser, projection and edge builder in memory (B1, B3b, B5)
                        or against Neo4j (B2), and the scorer
fixture_gen/, gold/     the generated fixture repositories and their answer keys (B3, B3b)
docs/answer-key.md      answer-key conventions
expected/               published numbers (reports/ for verify.py, published/ for reading)
verify.py               reports/ against expected/
```
