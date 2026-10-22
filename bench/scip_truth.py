"""Turn a SCIP index into an answer key the harness scorer reads (B1).

Truth comes from the language's own compiler-grade indexer, not from PipesHub:
symbol identities from SCIP descriptors, definitions and references from SCIP
occurrences. Tree-sitter is used only to find what encloses a reference (the
named function it sits in, an import statement, a class header), because SCIP
indexers don't emit enclosing ranges.

Relations written: CALLS, IMPORTS, IMPORTS_FROM and heritage (INHERITS). The
key's conventions (docs/answer-key.md) apply: endpoints are `path` or
`path::kind:Container.name`, nothing outside the repo, a call at module scope
comes from the file, a call inside an unnamed lambda belongs to the named
function around it.

usage: python scip_truth.py <index.json from `scip print --json`> <repo dir> <out key dir> <lang>
"""
from __future__ import annotations

import importlib
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

LANG = {
    "python": {"exts": (".py",), "grammar": [("tree_sitter_python", "language")]},
    "typescript": {"exts": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"),
                   "grammar": [("tree_sitter_typescript", "language_typescript"),
                               ("tree_sitter_typescript", "language_tsx")]},
    "go": {"exts": (".go",), "grammar": [("tree_sitter_go", "language")]},
    "java": {"exts": (".java",), "grammar": [("tree_sitter_java", "language")]},
}
FUNC_NODES = {
    "python": {"function_definition"},
    "typescript": {"function_declaration", "generator_function_declaration", "method_definition"},
    "go": {"function_declaration", "method_declaration"},
    "java": {"method_declaration", "constructor_declaration"},
}
# A const bound to an arrow or function expression is a named function (TS/JS).
VALUE_FUNCS = {"arrow_function", "function_expression", "function"}
IMPORT_NODES = {
    "python": {"import_statement", "import_from_statement"},
    "typescript": {"import_statement"},
    "go": {"import_declaration"},
    "java": {"import_declaration"},
}
HERITAGE_NODES = {
    "python": {"argument_list"},  # only when the parent is a class_definition
    "typescript": {"class_heritage", "extends_clause", "implements_clause"},
    "go": set(),
    "java": {"superclass", "super_interfaces", "extends_interfaces"},
}


# Symbols of a language's standard library or runtime a repo name can shadow.
SHADOWABLE = re.compile(r"\S+ \S+ (python-stdlib|builtins|lib\.\w+\.d\.ts|std|jdk) ")

# -- SCIP symbols ------------------------------------------------------------

_DESC = re.compile(r"(`(?:[^`]|``)+`|[^/#.:()\[\]`]+)?([/#.:]|\([^)]*\)\.|\([^)]*\)|\[[^\]]*\])")


def parse_symbol(symbol: str) -> list[tuple[str, str]] | None:
    """`scheme manager package version descriptors` -> [(name, suffix)]; None for locals."""
    if symbol.startswith("local "):
        return None
    parts = symbol.split(" ", 4)
    if len(parts) < 5:
        return None
    out = []
    for m in _DESC.finditer(parts[4]):
        name = (m.group(1) or "").strip("`").replace("``", "`")
        suffix = m.group(2)
        kind = {"/": "ns", "#": "type", ".": "term", ":": "meta"}.get(suffix)
        if kind is None:
            kind = "method" if suffix.endswith(").") else ("param" if suffix.startswith("(") else "tparam")
        out.append((name, kind))
    return out


def qualified(desc: list[tuple[str, str]], callable_term: bool) -> str | None:
    """PipesHub's `kind:Container.name` for a definition; None for what the key never names."""
    if not desc or desc[-1][1] in ("ns", "meta", "param", "tparam"):
        return None
    # PipesHub names a nested definition under every named scope around it,
    # functions included (`Response.iter_content.generate`).
    containers = [n for n, k in desc[:-1] if k in ("type", "method", "term") and n]
    name, kind = desc[-1]
    if kind == "type":
        k = "class"
    elif kind == "method":
        k = "method" if containers else "function"
        name = name or ""
    elif kind == "term":
        k = "function" if callable_term else "field"
    else:
        return None
    if not name:
        return None
    return f"{k}:{'.'.join([*containers, name])}"


# -- tree-sitter ------------------------------------------------------------

def parsers(lang: str):
    from tree_sitter import Language, Parser  # noqa: PLC0415

    out = []
    for module, fn in LANG[lang]["grammar"]:
        out.append(Parser(Language(getattr(importlib.import_module(module), fn)())))
    return out


def pick_parser(lang: str, path: str, ps):
    return ps[1] if lang == "typescript" and path.endswith((".tsx", ".jsx")) and len(ps) > 1 else ps[0]


def spans(tree, lang: str):
    """(start_line, end_line, name_line, name_col) for named functions; import and heritage ranges."""
    funcs, imports, heritage = [], [], []
    stack = [tree.root_node]
    while stack:
        n = stack.pop()
        t = n.type
        if t in FUNC_NODES[lang]:
            name = n.child_by_field_name("name")
            if name is not None:
                start = n.parent if n.parent is not None and n.parent.type == "decorated_definition" else n
                funcs.append((start.start_point[0], n.end_point[0], name.start_point[0], name.start_point[1]))
        elif lang == "typescript" and t in ("variable_declarator", "public_field_definition",
                                            "field_definition"):
            # `const f = () =>` and a class field holding a function (`handle = (e) =>`).
            value, name = n.child_by_field_name("value"), n.child_by_field_name("name")
            if value is not None and name is not None and value.type in VALUE_FUNCS:
                funcs.append((n.start_point[0], n.end_point[0], name.start_point[0], name.start_point[1]))
        if t in IMPORT_NODES[lang]:
            imports.append((n.start_point, n.end_point))
        if t in HERITAGE_NODES[lang] and (lang != "python" or (n.parent and n.parent.type == "class_definition")):
            heritage.append((n.start_point, n.end_point))
        stack.extend(n.children)
    return funcs, imports, heritage


def _skip_type_args(after: str) -> str:
    """`[T](x)` (Go) or `<T>(x)` (TS, Java): the call's parenthesis follows the type arguments."""
    if not after or after[0] not in "[<":
        return after
    close = {"[": "]", "<": ">"}[after[0]]
    depth = 0
    for i, ch in enumerate(after):
        if ch == after[0]:
            depth += 1
        elif ch == close:
            depth -= 1
            if depth == 0:
                return after[i + 1:].lstrip()
    return after


def inside(pos, ranges) -> bool:
    return any(a <= pos <= b for a, b in ranges)


# -- conversion ----------------------------------------------------------------

def convert(index: dict, repo: Path, lang: str) -> tuple[list[dict], list[dict], Counter]:
    exts = LANG[lang]["exts"]
    ps = parsers(lang)
    docs = [d for d in index["documents"] if d["relative_path"].endswith(exts)]
    stats = Counter()

    # Every in-repo definition: symbol -> (path, line, col).
    defs: dict[str, tuple[str, int, int]] = {}
    for d in docs:
        for o in d["occurrences"]:
            if o.get("symbol_roles", 0) & 1 and not o["symbol"].startswith("local "):
                r = o["range"]
                defs.setdefault(o["symbol"], (d["relative_path"], r[0], r[1]))

    trees, source = {}, {}
    for d in docs:
        p = d["relative_path"]
        try:
            text = (repo / p).read_bytes()
        except OSError:
            continue
        source[p] = text.decode("utf-8", "replace").splitlines()
        trees[p] = spans(pick_parser(lang, p, ps).parse(text), lang)

    callable_defs = set()  # term symbols defined as a function value (TS `const f = () =>`)
    def_at: dict[tuple[str, int, int], str] = {}
    for d in docs:
        for o in d["occurrences"]:
            if o.get("symbol_roles", 0) & 1 and not o["symbol"].startswith("local "):
                def_at[(d["relative_path"], o["range"][0], o["range"][1])] = o["symbol"]
    for p, (funcs, _i, _h) in trees.items():
        for _s, _e, nl, nc in funcs:
            sym = def_at.get((p, nl, nc))
            if sym:
                callable_defs.add(sym)

    file_symbols = {s for s, (p, line, col) in defs.items() if (line, col) == (0, 0)}

    def is_module(sym: str) -> bool:
        """Python `pkg.mod`/__init__: or a TS file namespace, defined at the file's start."""
        desc = parse_symbol(sym)
        return bool(desc) and desc[-1][1] in ("meta", "ns") and sym in file_symbols

    modules: dict[str, str] = {}  # dotted module path -> its definition symbol
    for sym in defs:
        desc = parse_symbol(sym)
        if desc and desc[-1][1] == "meta" and len(desc) >= 2:
            modules[".".join(n for n, k in desc[:-1] if k == "ns")] = sym

    def resolve_module(sym: str, importer: str) -> str | None:
        """`exceptions/__init__:` from src/requests/x.py -> the symbol of src/requests/exceptions.py."""
        desc = parse_symbol(sym)
        if not desc or desc[-1][1] != "meta":
            return None
        dotted = ".".join(n for n, k in desc[:-1] if k == "ns")
        hits = [s for m, s in modules.items() if m == dotted or m.endswith("." + dotted)]
        if len(hits) > 1:
            here = importer.rsplit("/", 1)[0]
            hits = [s for s in hits if defs[s][0].startswith(here + "/")] or hits
        if len(hits) == 1:
            stats["module-resolved-by-path"] += 1
            return hits[0]
        return None

    def endpoint(sym: str) -> str | None:
        desc = parse_symbol(sym)
        if desc is None or sym not in defs:
            return None
        if is_module(sym):  # a module: the file itself
            return defs[sym][0]
        q = qualified(desc, sym in callable_defs)
        return f"{defs[sym][0]}::{q}" if q else None

    def named_symbol(p: str, span) -> str | None:
        sym = def_at.get((p, span[2], span[3]))
        if sym is None:
            # The implementation of an @overload'ed function: SCIP marks only one of
            # its definitions. All of them share the symbol, so match by name.
            row = source[p][span[2]] if span[2] < len(source[p]) else ""
            name = (row[span[3]:].split("(")[0].split() or [""])[0]
            same = {s for (dp, _l, _c), s in def_at.items() if dp == p
                    and (parse_symbol(s) or [("", "")])[-1][0] == name}
            sym = same.pop() if len(same) == 1 else None
        return endpoint(sym) if sym else None

    def enclosing(p: str, line: int) -> str:
        """The innermost *named* function around a line; a local or anonymous one
        hands the call to the next named scope out, then to the file."""
        around = sorted((sp for sp in trees[p][0] if sp[0] <= line <= sp[1]), key=lambda sp: -sp[0])
        for sp in around:
            ep = named_symbol(p, sp)
            if ep is not None:
                return ep
            stats["enclosing-skipped-local"] += 1
        return p

    edges: dict[tuple[str, str, str], dict] = {}
    shadow_refs: Counter = Counter()

    def add(rel: str, src: str, dst: str, cat: str) -> None:
        if src != dst:
            edges.setdefault((rel, src, dst), {"rel": rel, "from": src, "to": dst, "cat": cat,
                                               "lang": lang, "note": "scip"})

    if lang == "python":
        paths = {d["relative_path"] for d in docs}
        for p in trees:
            for line in source[p]:
                m = re.match(r"\s*from\s+(\.+)([\w.]*)\s+import\s+(.+)", line)
                if not m:
                    continue
                base = p.rsplit("/", 1)[0] if "/" in p else ""
                for _ in range(len(m.group(1)) - 1):
                    base = base.rsplit("/", 1)[0] if "/" in base else ""
                pkg = "/".join(x for x in [base, m.group(2).replace(".", "/")] if x)
                if not m.group(2) and f"{pkg}/__init__.py".lstrip("/") in paths:
                    # `from . import x` names the package itself: its __init__.py.
                    add("IMPORTS_FROM", p, f"{pkg}/__init__.py".lstrip("/"), "import-package")
                for name in re.findall(r"(\w+)(?:\s+as\s+\w+)?", m.group(3).strip("()\\ ")):
                    for cand in (f"{pkg}/{name}.py", f"{pkg}/{name}/__init__.py"):
                        if cand.lstrip("/") in paths:
                            add("IMPORTS_FROM", p, cand.lstrip("/"), "import-submodule")

    for d in docs:
        p = d["relative_path"]
        if p not in trees:
            continue
        funcs, imports, heritage = trees[p]
        lines = source[p]
        # `from m import f as g`: scip-python makes `g` a local; uses of g are uses of f.
        alias: dict[str, str] = {}
        prev = None
        for o in sorted(d["occurrences"], key=lambda o: (o["range"][0], o["range"][1])):
            if inside((o["range"][0], o["range"][1]), imports):
                if o["symbol"].startswith("local ") and o.get("symbol_roles", 0) & 1 and prev \
                        and prev[0] == o["range"][0] and not prev[1].startswith("local "):
                    alias[o["symbol"]] = prev[1]
                prev = (o["range"][0], o["symbol"])
        # 2. a field assigned in a class body: calls in its value come from the field
        field_at: dict[int, str] = {}
        for o in d["occurrences"]:
            if o.get("symbol_roles", 0) & 1 and not o["symbol"].startswith("local "):
                desc = parse_symbol(o["symbol"])
                if desc and desc[-1][1] == "term" and len(desc) >= 2 and desc[-2][1] == "type":
                    field_at[o["range"][0]] = o["symbol"]
        for o in d["occurrences"]:
            sym = alias.get(o["symbol"], o["symbol"])
            if o.get("symbol_roles", 0) & 1 or sym.startswith("local "):
                continue
            if sym not in defs:
                sym = resolve_module(sym, p) or sym
            if "`<init>`" in sym or "<init>" in sym:
                # A Java constructor (often implicit, so never defined): the call is to the class.
                owner = sym.split("`<init>`")[0] if "`<init>`" in sym else sym.split("<init>")[0]
                if owner in defs:
                    sym = owner
            if sym not in defs:
                stats["ref-outside-repo"] += 1
                if SHADOWABLE.match(sym):
                    shadow_refs[sym.rsplit("/", 1)[-1].rstrip("#.").split("#")[0]] += 1
                continue
            r = o["range"]
            line, c0 = r[0], r[1]
            c1 = r[3] if len(r) == 4 else r[2]
            pos = (line, c0)
            desc = parse_symbol(sym)
            dst = endpoint(sym)
            if dst is None:
                stats["target-not-nameable"] += 1
                continue
            if inside(pos, imports):
                if is_module(sym):
                    add("IMPORTS_FROM", p, dst, "import")
                else:
                    # IMPORTS_FROM comes from the specifier's own module reference, which
                    # names the file imported from (a barrel), not where the symbol lives.
                    add("IMPORTS", p, dst, "import")
                continue
            if inside(pos, heritage) and desc[-1][1] == "type":
                src = enclosing_class(p, line, defs, def_at, trees, endpoint)
                if src:
                    add("INHERITS", src, dst, "heritage")
                continue
            text = lines[line] if line < len(lines) else ""
            after = _skip_type_args(text[c1:].lstrip())
            before = text[:c0].rstrip()
            if lang == "java" and before.endswith("@"):
                # A Java annotation is metadata the compiler reads, not a call; a
                # Python or TS decorator is a call and stays one.
                stats["annotation"] += 1
                continue
            if desc[-1][1] in ("method", "type") or sym in callable_defs:
                jsx = lang == "typescript" and before.endswith("<") and not before.endswith("</")
                if after.startswith("(") or before.endswith("new") or before.endswith("@") or jsx:
                    cat = ("jsx" if jsx else "ctor" if desc[-1][1] == "type"
                           else "decorator" if before.endswith("@") else "call")
                    src = enclosing(p, line)
                    if src == p and line in field_at:  # class body: `tags = GenericRelation(...)`
                        src = endpoint(field_at[line]) or p
                    add("CALLS", src, dst, cat)
                    continue
            stats["reference-not-a-relation"] += 1

    # A repo class or function named like a builtin (requests' own ConnectionError):
    # scip-python records even its definition as the builtin's, so edges to it
    # can't be trusted either way. Detected where it happens: the occurrence on the
    # repo definition's own name points outside the repo. The scorer leaves these
    # names out on both sides.
    external_at = {}
    for d in docs:
        for o in d["occurrences"]:
            if o["symbol"] not in defs and not o["symbol"].startswith("local "):
                external_at[(d["relative_path"], o["range"][0], o["range"][1])] = o["symbol"]
    shadowed_set = set()
    for p in trees:
        for i, line in enumerate(source[p]):
            m = re.match(r"(?:class|def|func|function|interface|type)\s+([A-Za-z_]\w*)", line)
            if m and (p, i, m.start(1)) in external_at:
                shadowed_set.add(m.group(1))
    shadowed = sorted(shadowed_set)
    files = [{"path": d["relative_path"], "lang": lang, "expect_index": True} for d in docs]
    stats["definitions"] = len(defs)
    stats["shadowed-names"] = len(shadowed)
    stats.update(Counter(e["rel"] for e in edges.values()))
    return list(edges.values()), files, stats, shadowed


def enclosing_class(p, line, defs, def_at, trees, endpoint) -> str | None:
    """The class whose header holds a base-type reference: the type defined nearest above it."""
    best = None
    for sym, (dp, dl, dc) in defs.items():
        if dp == p and dl <= line and (parse_symbol(sym) or [("", "")])[-1][1] == "type":
            if best is None or dl > best[1]:
                best = (sym, dl)
    return endpoint(best[0]) if best else None


def main() -> None:
    index_path, repo, out, lang = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]), sys.argv[4]
    index = json.loads(index_path.read_text())
    edges, files, stats, shadowed = convert(index, repo, lang)
    out.mkdir(parents=True, exist_ok=True)
    (out / "edges.jsonl").write_text("".join(json.dumps(e, sort_keys=True) + "\n" for e in edges))
    (out / "files.jsonl").write_text("".join(json.dumps(f, sort_keys=True) + "\n" for f in files))
    (out / "stats.json").write_text(json.dumps(stats, indent=1, sort_keys=True))
    (out / "shadowed.json").write_text(json.dumps(shadowed))
    print(json.dumps(stats, sort_keys=True))


if __name__ == "__main__":
    main()
