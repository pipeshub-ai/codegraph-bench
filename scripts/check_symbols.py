"""Compile the generated Python and compare the key's symbols with PipesHub's parser.

Run with PipesHub's venv; PIPESHUB_PY points at backend/python of the checkout under test.
"""
import os
import sys
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.environ.get("PIPESHUB_PY", "../pipeshub-code-graph/backend/python"))

from app.modules.parsers.code_parser.engine import parse_code  # noqa: E402

import importlib  # noqa: E402

from fixture_gen.model import Repo  # noqa: E402

parts = sys.argv[1].split(",") if len(sys.argv) > 1 else ["toplevel", "backend"]
step = int(sys.argv[2]) if len(sys.argv) > 2 else 0
repo = Repo("check")
for name in parts:
    importlib.import_module(f"fixture_gen.{name}").build(repo, step)

declared = defaultdict(set)
for s in repo.symbols:
    declared[s["path"]].add(s["qualified"].partition(":")[2])

missing = defaultdict(list)
compile_errors = []
for path, spec in sorted(repo.files.items()):
    if spec.lang is None or spec.symlink_to or not spec.expect_index:
        continue
    data = spec.content
    if data.startswith(b"\xff\xfe"):
        data = data.decode("utf-16").encode()
    if spec.lang == "python":
        try:
            compile(data, path, "exec")
        except SyntaxError as exc:
            compile_errors.append(f"{path}: {exc}")
    try:
        parsed = parse_code(data, spec.lang)
    except Exception as exc:  # noqa: BLE001
        missing[spec.lang].append(f"{path}: parser error {exc}")
        continue
    got = {".".join([*s.parent_chain, s.name]) for s in parsed.symbols if s.name}
    for q in sorted(declared.get(path, ())):
        if q not in got:
            missing[spec.lang].append(f"{path}::{q}")

print("compile errors:", *compile_errors, sep="\n  ") if compile_errors else print("compile errors: none")
for lang, rows in sorted(missing.items()):
    print(f"{lang}: {len(rows)} declared symbols the parser does not produce")
    for row in rows[:40]:
        print("  ", row)
