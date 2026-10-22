"""The answer-key model the fixture generators write into.

A generator adds a file's source and, next to it, every relation that source
creates. Because both come from the same call, the key cannot drift from the
code it describes.

Endpoint notation
-----------------
``path``                         the file itself (record / imports block / module scope)
``path::kind:Qualified.Name``    a symbol block, as PipesHub's ``qualifiedName`` spells it

Scoring compares endpoints on ``path`` plus the qualified tail (``Qualified.Name``)
and reports a kind mismatch separately, so a different kind spelling is not
counted as a missed edge.

Edge conventions (see docs/answer-key.md for the full list)
-------------------------------------------------------
CALLS         innermost named enclosing symbol -> callee definition. A
              constructor call / ``new X`` targets ``class:X``. Module-level
              code (and decorators on a module-level def) uses the file as
              ``from`` only for module-level statements; a decorator call is
              attributed to the decorated definition.
IMPORTS       file -> each imported symbol's *definition* (resolved through
              barrels and re-exports).
IMPORTS_FROM  file -> the file the import specifier names.
RE_EXPORTS    file -> the file an ``export ... from`` names.
EXPORTS       file -> each top-level symbol the file exports (JS/TS only).
INHERITS / IMPLEMENTS / EXTENDS   class -> base. Scored as one heritage group;
              the exact relation is reported as a secondary accuracy.
OVERRIDES     method -> the nearest base/interface method it overrides.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

HERITAGE = frozenset({"INHERITS", "IMPLEMENTS", "EXTENDS"})
RELATIONS = frozenset({
    "CALLS", "IMPORTS", "IMPORTS_FROM", "RE_EXPORTS", "EXPORTS", "OVERRIDES", *HERITAGE,
})

LANG_BY_EXT = {
    "py": "python", "pyi": "python",
    "js": "javascript", "jsx": "javascript", "mjs": "javascript", "cjs": "javascript",
    "ts": "typescript", "mts": "typescript", "cts": "typescript", "tsx": "tsx",
    "c": "c", "h": "c", "cc": "cpp", "cpp": "cpp", "hpp": "cpp", "cxx": "cpp",
    "cs": "csharp", "java": "java", "kt": "kotlin", "kts": "kotlin",
    "scala": "scala", "groovy": "groovy", "gradle": "groovy", "go": "go", "rs": "rust",
    "rb": "ruby", "php": "php", "swift": "swift", "dart": "dart", "lua": "lua",
}


def endpoint(path: str, qualified: str | None = None) -> str:
    return f"{path}::{qualified}" if qualified else path


@dataclass
class FileSpec:
    path: str
    content: bytes
    lang: str | None
    # What a correct pipeline should do with the file, not what PipesHub does today.
    expect_index: bool = True
    role: str = "source"
    tags: tuple[str, ...] = ()
    symlink_to: str | None = None


class FileCtx:
    """Relations declared by one file. ``q`` arguments are ``kind:Qualified.Name``."""

    def __init__(self, repo: "Repo", spec: FileSpec) -> None:
        self.repo = repo
        self.spec = spec

    @property
    def path(self) -> str:
        return self.spec.path

    def _edge(self, rel: str, src: str, dst: str, cat: str, note: str = "") -> "FileCtx":
        assert rel in RELATIONS, rel
        self.repo.edges.append({
            "rel": rel, "from": src, "to": dst, "cat": cat,
            "lang": self.spec.lang, "note": note,
        })
        return self

    def sym(self, *qualified: str) -> "FileCtx":
        for q in qualified:
            kind, _, name = q.partition(":")
            self.repo.symbols.append({"path": self.path, "kind": kind, "qualified": q,
                                      "name": name.rsplit(".", 1)[-1], "lang": self.spec.lang})
        return self

    def calls(self, from_q: str | None, to_path: str, to_q: str, cat: str = "direct",
              note: str = "") -> "FileCtx":
        return self._edge("CALLS", endpoint(self.path, from_q), endpoint(to_path, to_q), cat, note)

    def imports(self, to_path: str, *to_qs: str, via: str | None = None, cat: str = "named",
                note: str = "") -> "FileCtx":
        """One import statement: IMPORTS per name plus one IMPORTS_FROM.

        ``via`` is the file the specifier names when it differs from where the
        symbols are defined (a barrel).
        """
        for q in to_qs:
            self._edge("IMPORTS", self.path, endpoint(to_path, q), cat, note)
        target = via or to_path
        # Two statements importing from one file still make one file-level edge.
        if not any(e["rel"] == "IMPORTS_FROM" and e["from"] == self.path and e["to"] == target
                   for e in self.repo.edges):
            self._edge("IMPORTS_FROM", self.path, target, cat, note)
        return self

    def re_exports(self, to_path: str, cat: str = "barrel") -> "FileCtx":
        return self._edge("RE_EXPORTS", self.path, to_path, cat)

    def exports(self, *qs: str) -> "FileCtx":
        for q in qs:
            self._edge("EXPORTS", self.path, endpoint(self.path, q), "export")
        return self

    def inherits(self, from_q: str, to_path: str, to_q: str, rel: str = "INHERITS",
                 cat: str = "heritage") -> "FileCtx":
        assert rel in HERITAGE
        return self._edge(rel, endpoint(self.path, from_q), endpoint(to_path, to_q), cat)

    def overrides(self, from_q: str, to_path: str, to_q: str, cat: str = "override") -> "FileCtx":
        return self._edge("OVERRIDES", endpoint(self.path, from_q), endpoint(to_path, to_q), cat)

    def canary(self, kind: str, value: str, note: str = "") -> "FileCtx":
        self.repo.canaries.append({"kind": kind, "path": self.path, "value": value, "note": note})
        return self


@dataclass
class Repo:
    name: str
    files: dict[str, FileSpec] = field(default_factory=dict)
    edges: list[dict] = field(default_factory=list)
    symbols: list[dict] = field(default_factory=list)
    canaries: list[dict] = field(default_factory=list)
    tool_cases: list[dict] = field(default_factory=list)

    def add(self, path: str, content: str | bytes, *, lang: str | None = "auto",
            role: str = "source", expect_index: bool = True, tags: tuple[str, ...] = (),
            symlink_to: str | None = None) -> FileCtx:
        assert path not in self.files, f"duplicate path {path}"
        if lang == "auto":
            lang = LANG_BY_EXT.get(path.rsplit(".", 1)[-1]) if "." in path.rsplit("/", 1)[-1] else None
        data = content.encode() if isinstance(content, str) else content
        spec = FileSpec(path, data, lang, expect_index, role, tuple(tags), symlink_to)
        self.files[path] = spec
        return FileCtx(self, spec)

    def file(self, path: str) -> FileCtx:
        return FileCtx(self, self.files[path])

    def remove(self, path: str) -> None:
        """Drop a file and every key entry that touches it (used by mutations)."""
        self.files.pop(path)
        def touches(ep: str) -> bool:
            return ep == path or ep.startswith(path + "::")
        self.edges = [e for e in self.edges if not (touches(e["from"]) or touches(e["to"]))]
        self.symbols = [s for s in self.symbols if s["path"] != path]
        self.canaries = [c for c in self.canaries if c["path"] != path]

    def tool_case(self, **case) -> None:
        self.tool_cases.append(case)

    # -- output ---------------------------------------------------------

    def write_tree(self, out: Path) -> None:
        for spec in self.files.values():
            target = out / spec.path
            target.parent.mkdir(parents=True, exist_ok=True)
            if spec.symlink_to:
                if target.is_symlink() or target.exists():
                    target.unlink()
                target.symlink_to(spec.symlink_to)
            else:
                target.write_bytes(spec.content)

    def write_key(self, out: Path) -> None:
        out.mkdir(parents=True, exist_ok=True)
        self._check()
        files = [{
            "path": s.path, "lang": s.lang, "role": s.role, "expect_index": s.expect_index,
            "tags": list(s.tags), "bytes": len(s.content), "symlink_to": s.symlink_to,
        } for s in sorted(self.files.values(), key=lambda s: s.path)]
        for name, rows in (("files", files), ("edges", self.edges), ("symbols", self.symbols),
                           ("canaries", self.canaries), ("tool_cases", self.tool_cases)):
            with (out / f"{name}.jsonl").open("w") as fh:
                for row in rows:
                    fh.write(json.dumps(row, sort_keys=True) + "\n")

    def _check(self) -> None:
        """Every endpoint must name a file in the repo and, for symbols, a declared symbol."""
        declared = {(s["path"], s["qualified"]) for s in self.symbols}
        problems = []
        seen = set()
        for e in self.edges:
            key = (e["rel"], e["from"], e["to"])
            if key in seen:
                problems.append(f"duplicate edge {key}")
            seen.add(key)
            for ep in (e["from"], e["to"]):
                path, _, q = ep.partition("::")
                if path not in self.files:
                    problems.append(f"{e['rel']} endpoint names a missing file: {ep}")
                elif q and (path, q) not in declared:
                    problems.append(f"{e['rel']} endpoint names an undeclared symbol: {ep}")
        if problems:
            raise ValueError("answer key is inconsistent:\n  " + "\n  ".join(problems[:50]))
