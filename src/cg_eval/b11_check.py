"""B11 question checks: every gold symbol exists, every chain is real, and no question gives its answer away.

  python -m cg_eval.b11_check questions.jsonl        # prints one line per problem, exits 1 if any

A question is a JSON object:
  id, level ("easy" | "medium" | "tough"), kind, repo ("vscode"), question,
  answer  -- the symbols an answer must name (`path::Type.method`, no kind prefix),
  accept  -- optional: further symbols that are right but not required,
  chain   -- optional (kind "flow"): an ordered list whose consecutive pairs must be SCIP CALLS edges,
  expected -- a short prose answer, for a judge and for review.
Medium and tough questions must not contain any identifier of a gold symbol (its last name part,
its owner, or its file's base name) longer than 3 characters.
"""
from __future__ import annotations

import json
import os
import re
import sys
from functools import cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
# B11_REPO picks the snapshot: `vscode` (B11) or `django32` (B12).
QREPO = os.environ.get("B11_REPO", "vscode")
TRUTH = ROOT / "bench" / "truth" / QREPO / "edges.jsonl"
REPO = ROOT / "bench" / "repos" / QREPO
LEVELS = ("easy", "medium", "tough")
_KIND = re.compile(r"^(?:function|method|class|interface|field|type_alias|enum|variable|namespace|module):")


def _plain(endpoint: str) -> str:
    path, _, q = endpoint.partition("::")
    return f"{path}::{_KIND.sub('', q)}" if q else path


@cache
def truth() -> tuple[frozenset[str], frozenset[tuple[str, str]]]:
    syms, calls = set(), set()
    for line in TRUTH.read_text().splitlines():
        e = json.loads(line)
        a, b = _plain(e["from"]), _plain(e["to"])
        syms.update((a, b))
        if e["rel"] == "CALLS":
            calls.add((a, b))
    return frozenset(syms), frozenset(calls)


@cache
def dispatched() -> frozenset[tuple[str, str]]:
    """Calls seen at run time reaching an override of what the key resolves (B12 runtime trace):
    a flow chain may step through one, an answer key never requires one."""
    f = TRUTH.with_name("runtime_overrides.jsonl")
    rows = [json.loads(line) for line in f.read_text().splitlines()] if f.exists() else []
    return frozenset((_plain(e["from"]), _plain(e["to"])) for e in rows)


def _defined_in_source(symbol: str) -> bool:
    path, _, q = symbol.partition("::")
    f = REPO / path
    if not q or not f.is_file():
        return False
    name = q.rsplit(".", 1)[-1]
    name = "constructor" if name == "<constructor>" else name
    if f.suffix == ".py":
        return re.search(rf"^\s*(?:async\s+)?(?:def|class)\s+{re.escape(name)}\b", f.read_text(errors="ignore"), re.M) is not None
    return re.search(rf"\b{re.escape(name)}\b\s*(?:[<(=:]|\?\s*[:(])", f.read_text(errors="ignore")) is not None


def _identifiers(symbol: str) -> set[str]:
    path, _, q = symbol.partition("::")
    parts = {p for p in q.split(".") if p and p != "<constructor>"}
    parts.add(Path(path).stem)
    return {p for p in parts if len(p) > 3}


def check(q: dict) -> list[str]:
    syms, calls = truth()
    problems = []
    for field in ("id", "level", "kind", "question", "answer", "expected"):
        if not q.get(field) and q.get(field) != []:
            problems.append(f"missing {field}")
    if q.get("level") not in LEVELS:
        problems.append(f"level {q.get('level')!r}")
    gold = list(q.get("answer") or []) + list(q.get("accept") or []) + list(q.get("chain") or [])
    for s in gold:
        if s not in syms and not _defined_in_source(s):
            problems.append(f"unknown symbol {s}")
    chain = q.get("chain") or []
    for a, b in zip(chain, chain[1:]):
        if (a, b) not in calls and (a, b) not in dispatched():
            problems.append(f"not a SCIP call: {a} -> {b}")
    if q.get("level") in ("medium", "tough"):
        text = q.get("question", "")
        for s in gold:
            for ident in _identifiers(s):
                if re.search(rf"(?<![A-Za-z0-9_]){re.escape(ident)}(?![A-Za-z0-9_])", text):
                    problems.append(f"question names `{ident}` from {s}")
    return problems


def main() -> None:
    qs = [json.loads(line) for line in Path(sys.argv[1]).read_text().splitlines() if line.strip()]
    bad = 0
    ids = [q.get("id") for q in qs]
    for dup in {i for i in ids if ids.count(i) > 1}:
        print(f"duplicate id {dup}")
        bad += 1
    for q in qs:
        for p in check(q):
            print(f"{q.get('id')}: {p}")
            bad += 1
    print(f"{len(qs)} questions, {bad} problems", file=sys.stderr)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
