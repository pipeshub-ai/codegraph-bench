#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/env.sh"
# B11: 300 questions on each codebase (VS Code core, Django 3.2), 100 easy, 100 medium, 100 tough,
# asked of headless Claude Code in three setups: alone, + Graphify (its README's rules and hooks),
# + PipesHub (the cg-b11 stack's /mcp?tools=code). Each answer is scored against the answer key
# (bench/truth/<repo>, release data-v2).
#
#   MODELS="haiku sonnet opus"  REPOS="vscode django32"  ARMS=plain,graphify,pipeshub  REPEATS=1
#   JOBS=3       questions in flight per model and repo. Keep the total at 12 or less against one
#                PipesHub stack: past ~30 its Neo4j connection pool ran out and tool calls failed.
#   PREFIX=b11   results in reports/<PREFIX>-<repo>-<model>/
#   B6_ANTHROPIC_KEY_FROM="<entry>@$MODELS_JSON" bills an API key; empty (the default) runs on the
#                Claude Code account you are logged in with, as the published runs did.
# A rerun skips the answers already on disk.
set -uo pipefail
cd "$ROOT/src"
B11=$ROOT/bench/b11
export B6_KEEP_STREAM=1 B8_SHIPPED=1 B6_MAX_TURNS=50 B6_PER_RUN_USD=5 B6_BEST_SETUP=1
export B6_PIPESHUB_URL=${B6_PIPESHUB_URL:-'http://127.0.0.1:3960/mcp?tools=code'} B6_PIPESHUB_PAT_FILE=$B11/pat.token
export B6_ANTHROPIC_KEY_FROM=${B6_ANTHROPIC_KEY_FROM-}
# The PipesHub arm's one extra prompt line (the published runs had it; the other arms get their own tool line).
export B6_PIPESHUB_HINT_EXTRA=" Start every codebase question with these tools: before your first Grep, Glob, Read or Bash search, call pipeshub_code_search, pipeshub_code_neighbours, pipeshub_code_path or pipeshub_code_impact for the symbols or behaviour the question names, and read raw files for the lines they locate. The graph links a call only when the receiver's type is known from the code, so a caller list, impact set or path can be incomplete where values are untyped or passed in; for every-caller, impact or does-A-reach-B questions also search the call text before answering."
wait_cpu() { while [ "$(vmstat 3 2 | tail -1 | awk '{print 100-$15}')" -ge 90 ]; do sleep 30; done; }
"$B11/keep_acl.sh" & ACL=$!; trap 'kill $ACL' EXIT
declare -A BUDGET=([haiku]=60 [sonnet]=200 [opus]=300)
mkdir -p "$ROOT/reports/logs"
pids=()
for repo in ${REPOS:-vscode django32}; do
  case $repo in
    # The snapshot drops VS Code's src/ folder; accept answers written from the real layout (src/vs/...).
    vscode)   q=$B11/questions-vscode-300.jsonl; env=(B8_STRIP_SRC=1 B8_PORT_BASE=18821 B6_PIPESHUB_REPO="$B11_VSCODE_REPO") ;;
    # Django's key includes calls seen running, so a call counts for the method it runs.
    django32) q=$B11/questions-django-300.jsonl; env=(B8_CALL_DEF=runtime B6_PIPESHUB_REPO="$B11_DJANGO_REPO") ;;
    *) echo "unknown repo $repo"; exit 2 ;;
  esac
  for m in ${MODELS:-haiku sonnet opus}; do
    out=${PREFIX:-b11}-$repo-$m
    mkdir -p "$ROOT/reports/$out" && cp "$q" "$ROOT/reports/$out/questions.jsonl"
    ( for r in $(seq 1 "${REPEATS:-1}"); do
        wait_cpu
        env "${env[@]}" B6_MODEL=$m B8_OUT=$out B8_BUDGET_USD=${BUDGET[$m]} \
          "$PY" -m cg_eval.b8 run --repeat "$r" --arms "${ARMS:-plain,graphify,pipeshub}" --jobs "${JOBS:-3}" \
          ${LIMIT:+--limit $LIMIT} >> "$ROOT/reports/logs/$out.log" 2>&1
      done
      env "${env[@]}" B8_OUT=$out "$PY" -m cg_eval.b8 score > "$ROOT/reports/logs/$out-score.log" 2>&1
      echo "done $repo $m" ) &
    pids+=($!)
  done
done
wait "${pids[@]}"  # not the guard, which never exits
# A run that hit the account's usage limit is scored as wrong: rerun those (delete their .json) before trusting a table.
echo "limit-hits: $(grep -l usage_limit_reached "$ROOT"/reports/"${PREFIX:-b11}"-*/r*/runs/*/*.stream.jsonl 2>/dev/null | wc -l)"
