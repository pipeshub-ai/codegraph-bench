#!/usr/bin/env bash
# Bare clones of every ContextBench-verified repo, read at each task's base commit (as B5a's tree_at).
cd "$(dirname "$0")"; mkdir -p git
while IFS=$'\t' read -r repo url; do
  dest="git/${repo//\//__}.git"
  [ -d "$dest" ] && continue
  git clone -q --bare "$url" "$dest.part" && mv "$dest.part" "$dest" && echo "cloned $repo $(du -sh "$dest" | cut -f1)" || echo "FAILED $repo"
done < repos.tsv
echo "clones done"
