#!/usr/bin/env bash
# Set up everything the benchmarks need, at the versions in pins.toml. Safe to re-run: each step
# skips what is already in place.
#
#   ./bootstrap.sh smoke                 # PipesHub (B1 commit) + venv, requests, Graphify, scip-python: B1 smoke run
#   ./bootstrap.sh all                   # everything below except data
#   ./bootstrap.sh pipeshub [REF ...]    # PipesHub worktrees at the given refs (default: every ref in pins.toml) + the venv
#   ./bootstrap.sh subjects [NAME ...]   # the B1/B2 subject repos at their pinned commits (default: all)
#   ./bootstrap.sh tools                 # Graphify, codebase-memory-mcp (0.12.0 and 0.11.0), CodeGraphContext
#   ./bootstrap.sh scip                  # scip CLI and scip-go binaries (scip-python/-typescript run via npx, scip-java via docker)
#   ./bootstrap.sh fixtures              # the generated fixture repos (B3, B3b)
#   ./bootstrap.sh datasets              # SWE-bench Lite + Verified, ContextBench (B5)
#   ./bootstrap.sh data                  # the published answer keys and results (GitHub release data-v1)
set -euo pipefail
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/bench/env.sh"
cd "$ROOT"
export PINS=$ROOT/pins.toml
B=$ROOT/bench
mkdir -p "$WORK" "$B/repos" "$B/tools" "$B/bin" "$B/dl"

fetch() {  # fetch <owner/repo> <tag> <asset ...>: public release downloads, no GitHub login needed
  local repo=$1 tag=$2; shift 2
  for a in "$@"; do curl -fsSL -o "$a" "https://github.com/$repo/releases/download/$tag/$a"; done
}

pin() {  # pin <section> <key>: a value from pins.toml (plain string or the 2nd field of a list)
  python3 - "$1" "$2" <<'EOF'
import sys, tomllib
v = tomllib.load(open(__import__("os").environ["PINS"], "rb"))[sys.argv[1]][sys.argv[2]]
print(v[1] if isinstance(v, list) else v)
EOF
}

pipeshub() {
  local repo branch base=$WORK/pipeshub-base
  repo=$(pin pipeshub repo); branch=$(pin pipeshub branch)
  if [ ! -d "$base/.git" ]; then
    git clone --no-checkout --filter=blob:none -b "$branch" "$repo" "$base"
  fi
  local refs=("$@")
  if [ ${#refs[@]} -eq 0 ]; then
    mapfile -t refs < <(python3 -c 'import tomllib;p=tomllib.load(open(__import__("os").environ["PINS"],"rb"))["pipeshub"];print("\n".join(sorted({v for k,v in p.items() if len(v)==9 and all(c in "0123456789abcdef" for c in v)})))')
  fi
  for ref in "${refs[@]}"; do
    [ -d "$WORK/pipeshub@$ref" ] || git -C "$base" worktree add -q --detach "$WORK/pipeshub@$ref" "$ref"
    echo "pipeshub@$ref ready"
  done
  # One venv per dependency set: the refs share one pyproject.toml except the speed-work refs (main merged in).
  # cg_eval puts the selected ref's backend/python first on sys.path, so the editable install never shadows it.
  for ref in "${refs[@]}"; do
    local src=$WORK/pipeshub@$ref/backend/python h
    h=$(sha256sum "$src/pyproject.toml" | cut -c1-12)
    [ -x "$WORK/venv-$h/bin/python" ] && continue
    python3.12 -m venv "$WORK/venv-$h"
    "$WORK/venv-$h/bin/pip" install -q uv
    (cd "$src" && "$WORK/venv-$h/bin/uv" pip install --python "$WORK/venv-$h/bin/python" -e .)
    "$WORK/venv-$h/bin/uv" pip install --python "$WORK/venv-$h/bin/python" -r "$B/requirements-bench.txt"
    echo "venv-$h (from $ref)"
  done
}

subjects() {
  local names=("$@")
  [ ${#names[@]} -eq 0 ] && mapfile -t names < <(python3 -c 'import tomllib;print("\n".join(tomllib.load(open(__import__("os").environ["PINS"],"rb"))["subjects"]))')
  for n in "${names[@]}"; do
    local url commit dest=$B/repos/$n
    url=$(python3 -c "import tomllib;print(tomllib.load(open(__import__('os').environ['PINS'],'rb'))['subjects']['$n'][0])"); commit=$(pin subjects "$n")
    [ -e "$dest" ] && { echo "$n present"; continue; }
    if [ "$n" = pipeshub-ai ]; then
      # A plain tree, no .git, as it was measured (the PR's own code as a subject).
      git -C "$WORK/pipeshub-base" fetch -q origin "$commit" 2>/dev/null || true
      mkdir -p "$dest" && git -C "$WORK/pipeshub-base" archive "$commit" | tar -x -C "$dest"
    else
      git init -q "$dest" && git -C "$dest" remote add origin "$url"
      git -C "$dest" fetch -q --depth 1 origin "$commit" && git -C "$dest" checkout -q FETCH_HEAD
    fi
    echo "$n at ${commit:0:12}"
  done
}

tools() {
  local T=$B/tools
  [ -x "$T/graphify/bin/graphify" ] || { python3.12 -m venv "$T/graphify"; "$T/graphify/bin/pip" install -q "$(pin tools graphify)"; }
  [ -x "$T/cgc/bin/cgc" ] || { python3.12 -m venv "$T/cgc"; "$T/cgc/bin/pip" install -q "$(pin tools codegraphcontext)" kuzu; }
  local v d
  for v in "$(pin tools codebase_memory_mcp)":cbm-012 "$(pin tools codebase_memory_mcp_scale)":cbm; do
    d=$T/${v#*:}; v=${v%%:*}
    [ -x "$d/codebase-memory-mcp" ] && continue
    mkdir -p "$d" "$B/dl/cbm-$v"
    (cd "$B/dl/cbm-$v" && fetch DeusData/codebase-memory-mcp "v$v" codebase-memory-mcp-linux-amd64.tar.gz checksums.txt \
       && grep ' codebase-memory-mcp-linux-amd64.tar.gz$' checksums.txt | sha256sum -c -) \
      && tar -xzf "$B/dl/cbm-$v/codebase-memory-mcp-linux-amd64.tar.gz" -C "$d"
    mkdir -p "$d/home"
  done
  "$T/graphify/bin/graphify" --version; "$T/cgc/bin/cgc" --version | tail -1
  "$T/cbm-012/codebase-memory-mcp" --version; "$T/cbm/codebase-memory-mcp" --version
}

scip() {
  if [ ! -x "$B/bin/scip" ]; then
    (cd "$B/dl" && fetch sourcegraph/scip "$(pin tools scip)" scip-linux-amd64.tar.gz scip-linux-amd64.tar.gz.sha256 \
       && sha256sum -c scip-linux-amd64.tar.gz.sha256 && tar -xzf scip-linux-amd64.tar.gz -C "$B/bin")
  fi
  if [ ! -x "$B/bin/scip-go" ]; then
    (cd "$B/dl" && fetch sourcegraph/scip-go "$(pin tools scip_go)" scip-go-linux-amd64.tar.gz scip-go-linux-amd64.tar.gz.sha256 \
       && sha256sum -c scip-go-linux-amd64.tar.gz.sha256 && tar -xzf scip-go-linux-amd64.tar.gz -C "$B/bin")
  fi
  "$B/bin/scip" --version
}

fixtures() {
  mkdir -p "$WORK/fixtures"
  local k url
  for k in main admin b3; do
    url=$(pin fixtures $k); local dest=$WORK/fixtures/$(basename "$url")
    [ -d "$dest/.git" ] || git clone -q "$url" "$dest"
    git -C "$dest" fetch -q --tags origin
    echo "$(basename "$url"): $(git -C "$dest" tag | tr '\n' ' ')"
  done
}

datasets() {
  local S=$B/swebench C=$B/contextbench
  "$PY" -m pip show huggingface_hub > /dev/null 2>&1 || "$(dirname "$PY")/uv" pip install --python "$PY" huggingface_hub
  "$PY" - "$S" <<'EOF'
import sys, tomllib
from huggingface_hub import snapshot_download
d = tomllib.load(open(__import__("os").environ["PINS"], "rb"))["datasets"]
for key, sub in (("swebench_lite", "lite"), ("swebench_verified", "verified")):
    snapshot_download(d[key]["hf"], repo_type="dataset", revision=d[key]["revision"], local_dir=f"{sys.argv[1]}/{sub}")
EOF
  if [ ! -d "$C/ContextBench/Context-Bench" ]; then
    mkdir -p "$C/ContextBench" && git clone -q https://github.com/anonymousUser2026/ContextBench "$C/ContextBench/Context-Bench"
    git -C "$C/ContextBench/Context-Bench" checkout -q cd5f62e4f3a426ed2a497ad7d1584e1c4b219058
  fi
  # The official scorer wants Python 3.11 (bench/contextbench/score_by_repo.py).
  [ -x "$C/.venv311/bin/python" ] || { uv venv -q --python 3.11 "$C/.venv311" && uv pip install -q --python "$C/.venv311/bin/python" -e "$C/ContextBench/Context-Bench"; }
  sha256sum -c <<EOF
7a21f37b8bc179c7db5beeb14e88ac538ba283455c776e6b2535bbfb6e3551b4  $S/lite/data/test-00000-of-00001.parquet
8312f321838051849d2fa7c6ca071244733a3e90bb517ff6ca8186f722199b5c  $S/lite/data/dev-00000-of-00001.parquet
a45b1fe4e2f0c8390b2b2938ac83e92ed5979000856808f3679c07812e9e6dcd  $S/verified/data/test-00000-of-00001.parquet
e9dcfd504cbfb849ac815a79040c793d0d92f94eecc9b5a4ee3e1445a2f8a791  $C/ContextBench/Context-Bench/data/contextbench_verified.parquet
EOF
}

data() {
  local tag=${DATA_TAG:-data-v1} repo=${DATA_REPO:-pipeshub-ai/codegraph-bench}
  mkdir -p "$WORK/data" && cd "$WORK/data"
  fetch "$repo" "$tag" SHA256SUMS MANIFEST.md truth.tar.gz b1-reports.tar.gz b2-reports.tar.gz b3-reports.tar.gz b5-reports.tar.gz
  sha256sum -c SHA256SUMS
  tar -xzf truth.tar.gz -C "$B"            # bench/truth/<repo>/ : the SCIP answer keys
  cd "$ROOT"
}

case "${1:-}" in
  smoke)    pipeshub "$(pin pipeshub b1)"; subjects requests; tools; scip ;;
  all)      pipeshub; subjects; tools; scip; fixtures; datasets ;;
  pipeshub|subjects|tools|scip|fixtures|datasets|data) f=$1; shift; "$f" "$@" ;;
  *) sed -n '2,15p' "$0"; exit 1 ;;
esac
