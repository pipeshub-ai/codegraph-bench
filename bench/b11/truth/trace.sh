#!/usr/bin/env bash
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/../../env.sh"
# Django's runtime call edges for the B11 key (optional: the key ships in release data-v2).
# Runs Django 3.2's own test suite on Python 3.9 under a profiler (trace_run.py), one module at a time.
#   trace.sh <out name> <runtests args...>      e.g. trace.sh mods/auth_tests auth_tests
# Then: (cd src && $PY -m cg_eval.b12_runtime ../bench/b11/truth/runtime/mods/*.jsonl)
set -uo pipefail
HERE=$(cd "$(dirname "$0")" && pwd)
DJ=$WORK/src/django   # fetched by snapshot.sh build
git -C "$DJ" checkout -q b6475d7d7940f3ce575e0b0f2d83e517f899b4cf 2>/dev/null || git -C "$DJ" checkout -q FETCH_HEAD
out=$1; shift
mkdir -p "$HERE/runtime/$(dirname "$out")" "$HERE/pylib"
docker run --rm --cpus 2 -u "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONPATH=/src \
  -v "$DJ:/src" -v "$HERE/trace_run.py:/trace_run.py:ro" -v "$HERE/runtime:/out" -v "$HERE/pylib:/tmp/.local" \
  -w /src/tests python:3.9-slim sh -c \
  'python -c "import asgiref,pytz,sqlparse" 2>/dev/null || pip install -q --user "asgiref>=3.2.10,<4" pytz "sqlparse<0.5" >/dev/null 2>&1; timeout -s INT -k 60 ${TRACE_TIMEOUT:-900} python /trace_run.py /out/'"$out"'.jsonl --parallel 1 "$@"' sh "$@"
