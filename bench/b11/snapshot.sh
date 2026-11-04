#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# The two codebases B11 asks about, rebuilt from upstream and checked against the measured trees.
#   snapshot.sh build      bench/repos/vscode (microsoft/vscode, src/vs/{base,editor,platform} as vs/)
#                          and bench/repos/django32 (django/django 3.2, the django/ package), each verified
#                          by its git tree hash, then committed in $WORK/snapshots/<name> for the connector
#   snapshot.sh push       push those commits to $B11_VSCODE_REPO / $B11_DJANGO_REPO (empty repos you created;
#                          private is fine: the GitHub Teams connector reads them with GITHUB_FIXTURE_TOKEN)
#   snapshot.sh graphify   Graphify's graphs of both trees (bench/work-v2/graphify/<name>), as measured
set -euo pipefail
VSCODE_SHA=3ac0f0563e382d8e3480c787d04a2d05afca6746   # microsoft/vscode, 2026-10-26 (1.144.0 development)
DJANGO_SHA=b6475d7d7940f3ce575e0b0f2d83e517f899b4cf   # django/django tag 3.2
VSCODE_TREE=040e9b2ba651af6895b89134800139555d88effa  # git tree of the snapshot the published runs indexed
DJANGO_TREE=770db1dd1075cddd99c4a4b3f1c5dba51c5ae893

fetch() {  # fetch <dir> <url> <sha>: one commit, no history
  [ -d "$1/.git" ] || git init -q "$1"
  git -C "$1" fetch -q --depth 1 "$2" "$3"
}

snap() {  # snap <name> <src-git> <sha> <tree> <path:dest>...
  local name=$1 src=$2 sha=$3 tree=$4; shift 4
  local out=$WORK/snapshots/$name; rm -rf "$out"; mkdir -p "$out"
  for p in "$@"; do
    mkdir -p "$out/$(dirname "${p#*:}")"
    git -C "$src" archive "$sha" "${p%%:*}" | tar -x -C "$out"
    [ "${p%%:*}" = "${p#*:}" ] || mv "$out/${p%%:*}" "$out/${p#*:}"
  done
  find "$out" -mindepth 1 -type d -empty -delete
  git -C "$out" init -q; git -C "$out" add -A
  local got; got=$(git -C "$out" write-tree)
  [ "$got" = "$tree" ] || { echo "$name: tree $got, expected $tree"; exit 1; }
  git -C "$out" -c user.name=snapshot -c user.email=snapshot@localhost commit -q -m "$name snapshot ($sha)"
  rm -rf "$ROOT/bench/repos/$name"; mkdir -p "$ROOT/bench/repos"
  git -C "$out" archive HEAD | (mkdir -p "$ROOT/bench/repos/$name" && tar -x -C "$ROOT/bench/repos/$name")
  echo "$name: tree $got verified; bench/repos/$name and $out"
}

case "${1:-}" in
  build)
    fetch "$WORK/src/vscode" https://github.com/microsoft/vscode "$VSCODE_SHA"
    fetch "$WORK/src/django" https://github.com/django/django "$DJANGO_SHA"
    snap vscode "$WORK/src/vscode" "$VSCODE_SHA" "$VSCODE_TREE" \
      src/vs/base:vs/base src/vs/editor:vs/editor src/vs/platform:vs/platform
    rmdir "$WORK/snapshots/vscode/src" 2>/dev/null || true
    snap django32 "$WORK/src/django" "$DJANGO_SHA" "$DJANGO_TREE" django:django ;;
  push)
    git -C "$WORK/snapshots/vscode" push -f "https://github.com/$B11_VSCODE_REPO.git" HEAD:main
    git -C "$WORK/snapshots/django32" push -f "https://github.com/$B11_DJANGO_REPO.git" HEAD:main ;;
  graphify)
    for name in vscode django32; do
      w=$ROOT/bench/work-v2/graphify/$name; rm -rf "$w"; mkdir -p "$w"
      cp -a "$ROOT/bench/repos/$name/." "$w/"
      (cd "$w" && "$ROOT/bench/tools/graphify/bin/graphify" update . --no-cluster > "../$name.log" 2>&1)
      echo "$name: $(ls "$w/graphify-out")"
    done ;;
  *) sed -n '3,10p' "$0"; exit 2 ;;
esac
