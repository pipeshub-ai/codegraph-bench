"""B12: merge runtime call edges (Django's own test suite, traced) into the SCIP key of the Django 3.2 snapshot.

  python -m cg_eval.b12_runtime ../bench/b11/truth/runtime/mods/*.jsonl     # one trace file per test module

The SCIP (pyright) key misses calls on receivers pyright cannot type (a parameter, an untyped attribute),
which is most of Django. The trace saw which function actually ran from which. A traced edge A -> B is
added to the key only when it is a call a reader can see: B's name followed by `(` appears in A's own
source (so no callbacks, signals or getattr dispatch), and the key has no edge from A to another
definition of that name (that would be the same call site reaching an override at run time; such edges
go to `runtime_overrides.jsonl` for review, not to the key). Calls of `C(...)` that ran `C.__init__`
become edges to the class, as SCIP writes constructor calls.

Writes bench/truth/django32/edges.jsonl (the SCIP key is kept once as edges_scip.jsonl) and prints counts.
"""
from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPO = ROOT / "bench" / "repos" / "django32"
KEY = ROOT / "bench" / "truth" / "django32"
_KIND = re.compile(r"^(?:function|method|class|field):")


class Defs:
    """Code object (firstlineno, name) -> key name, and each name's source, for one file."""

    def __init__(self, path: str) -> None:
        self.path = path
        text = (REPO / path).read_text(errors="ignore")
        self.lines = text.splitlines()
        self.by_line: dict[tuple[int, str], tuple[str, str]] = {}  # -> (key name, kind)
        self.spans: list[tuple[int, int, str, str]] = []  # start, end, key name, kind
        self._walk(ast.parse(text), [], None)

    def _walk(self, node: ast.AST, outer: list[str], owner_kind: str | None) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                name = ".".join(outer + [child.name])
                kind = "class" if isinstance(child, ast.ClassDef) else ("function" if not outer else "method")
                first = min([child.lineno] + [d.lineno for d in child.decorator_list])
                for line in {first, child.lineno}:
                    self.by_line[(line, child.name)] = (name, kind)
                self.spans.append((child.lineno, child.end_lineno or child.lineno, name, kind))
                self._walk(child, outer + [child.name], kind)
            else:
                self._walk(child, outer, owner_kind)

    def enclosing(self, line: int) -> tuple[str, str] | None:
        """The innermost function or method holding `line` (a lambda or comprehension belongs to it)."""
        best = None
        for start, end, name, kind in self.spans:
            if kind != "class" and start <= line <= end and (best is None or start >= best[0]):
                best = (start, name, kind)
        return (best[1], best[2]) if best else None

    def source(self, name: str) -> str:
        for start, end, n, _ in self.spans:
            if n == name:
                return "\n".join(self.lines[start - 1:end])
        return ""


_defs: dict[str, Defs] = {}


def defs(path: str) -> Defs | None:
    if path not in _defs:
        try:
            _defs[path] = Defs(path)
        except (OSError, SyntaxError):
            return None
    return _defs[path]


KNOWN: set[str] = set()  # names the SCIP key uses (`Class.method`), filled by main()


def as_keyed(q: str) -> str:
    """A nested name as the key spells it: scip-python sometimes drops an enclosing function
    (`Apps.lazy_model_operation.apply_next_model` is keyed `Apps.apply_next_model`)."""
    parts = q.split(".")
    if q in KNOWN or len(parts) < 3:
        return q
    for i in range(1, len(parts) - 1):
        short = ".".join(parts[:i] + parts[-1:])
        if short in KNOWN:
            return short
    return q


def resolve(path: str, line: int, name: str, *, caller: bool) -> tuple[str, str] | None:
    """(key endpoint, kind) for a traced code object, or None when it is not a nameable callee."""
    d = defs(path)
    if d is None:
        return None
    if name == "<module>":
        return (path, "file") if caller else None
    hit = d.by_line.get((line, name))
    if hit and hit[1] == "class":  # a class body runs at definition, not as a call
        return (path, "file") if caller else None
    if hit:
        return f"{path}::{as_keyed(hit[0])}", hit[1]
    inner = d.enclosing(line)  # lambda, comprehension, generator expression
    if inner:
        return f"{path}::{as_keyed(inner[0])}", inner[1]
    return (path, "file") if caller else None


def plain(endpoint: str) -> str:
    path, _, q = endpoint.partition("::")
    return f"{path}::{_KIND.sub('', q)}" if q else path


def kinded(name: str, kind: str) -> str:
    """`path::kind:Q`, as the SCIP key writes endpoints (a file stays a bare path)."""
    path, _, q = name.partition("::")
    return f"{path}::{kind}:{q}" if q else path


def main() -> None:
    scip = KEY / "edges_scip.jsonl"
    if not scip.exists():
        (KEY / "edges.jsonl").rename(scip)
    lines = scip.read_text().splitlines()
    for line in lines:
        e = json.loads(line)
        KNOWN.update(plain(x).partition("::")[2] for x in (e["from"], e["to"]))
    calls_from: dict[str, set[str]] = defaultdict(set)
    have: set[tuple[str, str]] = set()
    for line in lines:
        e = json.loads(line)
        if e["rel"] == "CALLS":
            a, b = plain(e["from"]), plain(e["to"])
            have.add((a, b))
            calls_from[a].add(b)
    added, overrides, stats = [], [], Counter()
    seen: set[tuple[str, str]] = set()
    raws = {raw for f in sys.argv[1:] for raw in Path(f).read_text().splitlines()}
    for raw in sorted(raws):
        t = json.loads(raw)
        a = resolve(*t["caller"], caller=True)
        b = resolve(*t["callee"], caller=False)
        if not a or not b:
            stats["not nameable"] += 1
            continue
        (a_name, a_kind), (b_name, b_kind) = a, b
        if a_name == b_name:
            continue
        callee_q = b_name.partition("::")[2]
        bare = callee_q.rsplit(".", 1)[-1]
        target, target_kind = b_name, b_kind
        if bare == "__init__" and "." in callee_q:  # `C(...)` ran C.__init__: SCIP names the class
            cls = callee_q.rsplit(".", 1)[0]
            target, target_kind, bare = f"{b_name.partition('::')[0]}::{cls}", "class", cls.rsplit(".", 1)[-1]
        if (a_name, target) in seen:
            continue
        seen.add((a_name, target))
        if (a_name, target) in have:
            stats["already in key"] += 1
            continue
        a_path, _, a_q = a_name.partition("::")
        d = defs(a_path)
        text = d.source(a_q) if (d and a_q) else (REPO / a_path).read_text(errors="ignore")
        if not re.search(rf"(?<![\w.]){re.escape(bare)}\s*\(|\.{re.escape(bare)}\s*\(", text):
            if bare != "__init__" and not (target_kind == "class" and re.search(r"super\(\)\.__init__\s*\(|\.__init__\s*\(", text)):
                stats["no visible call (callback, dispatch, property)"] += 1
                continue
            target, target_kind = b_name, b_kind  # an explicit __init__ call
        same_name = {x for x in calls_from.get(a_name, ()) if x.rsplit(".", 1)[-1].rsplit("::", 1)[-1] == bare}
        edge = {"cat": "runtime", "from": kinded(a_name, a_kind), "lang": "python", "note": "runtime",
                "rel": "CALLS", "to": kinded(target, target_kind)}
        if same_name:
            stats["override of a key edge (review)"] += 1
            overrides.append({**edge, "key_has": sorted(same_name)})
            continue
        stats["added"] += 1
        added.append(edge)
    # Calls verified by reading that no test on SQLite runs (PostgreSQL-only paths), one JSON edge per line.
    manual = KEY / "edges_manual.jsonl"
    lines += manual.read_text().splitlines() if manual.exists() else []
    with (KEY / "edges.jsonl").open("w") as f:
        for line in lines:
            f.write(line + "\n")
        for e in added:
            f.write(json.dumps(e, sort_keys=True) + "\n")
    with (KEY / "runtime_overrides.jsonl").open("w") as f:
        for e in overrides:
            f.write(json.dumps(e, sort_keys=True) + "\n")
    print(json.dumps(stats, indent=1), f"\nkey CALLS: {len(have)} scip + {len(added)} runtime", file=sys.stderr)


if __name__ == "__main__":
    main()
