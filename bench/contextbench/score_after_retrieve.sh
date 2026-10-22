#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../env.sh"
# Waits for the retrieval rerun, then scores the two retrieval sets with the official scorer.
cd "$(dirname "$0")"
while ps -eo args -u "$USER" | grep -q '[c]b.py retrieve'; do sleep 30; done
echo "$(date +%T) retrieval done: $(ls ../../reports/contextbench/retrieval-signals | wc -l)/500"
CB_SCORE_TIERS=retrieval $PY -u score_by_repo.py
echo "$(date +%T) scoring done"
