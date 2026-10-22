"""codebase-memory-mcp 0.11/0.12 -> edges in the key's form (rel, from, to, confidence).

Indexes the repo with its own CLI (`index_repository`, mode full), then reads
CALLS, IMPORTS and INHERITS from the SQLite store that index writes. Its Cypher
tool pages by re-running the query, which is quadratic on large repos; the
rows are the same.
A qualified name is `<project>.<module path>.<Container>.<name>`; dropping the
project and the file's module path leaves `Container.name`. Java names start at
the package folder (the class is the file stem) and Go names at the package
folder with no receiver: a Go method name is one node per package, shared by
every receiver type, so it maps to each owner the tool links to it
(DEFINES_METHOD). A Module or File node is the file itself, so an IMPORTS edge
to one is IMPORTS_FROM.

usage: python cbm_edges.py <cbm binary> <repo dir> <out edges.jsonl>
(HOME should point at a private folder: the tool keeps its index there.)
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

RELS = ("CALLS", "IMPORTS", "INHERITS", "IMPLEMENTS")
PACKAGE_NAMED = (".java", ".kt", ".scala", ".go")  # qualified names start at the folder, not the file
FILE_LABELS = {"Module", "File"}
KIND = {"Function": "function", "Method": "method", "Class": "class", "Interface": "interface",
        "Struct": "class", "Enum": "class", "Trait": "class", "Type": "class", "Variable": "field"}


def cli(binary: str, tool: str, args: dict) -> dict:
    out = subprocess.run([binary, "cli", "--quiet", tool, json.dumps(args)],
                         capture_output=True, text=True, check=True).stdout
    return json.loads(out[out.index("{"):])


def main() -> None:
    binary, repo, out_path = sys.argv[1], str(Path(sys.argv[2]).resolve()), sys.argv[3]
    project = cli(binary, "index_repository", {"repo_path": repo, "mode": "full"})["project"]

    def endpoint(label: str, qname: str, path: str) -> str | None:
        if not path:
            return None
        if label in FILE_LABELS:
            return path
        kind = KIND.get(label)
        if kind is None:
            return None
        rest = qname[len(project) + 1:] if qname.startswith(project + ".") else qname
        module = path.rsplit(".", 1)[0].replace("/", ".")
        if path.endswith(PACKAGE_NAMED):
            folder = module.rpartition(".")[0]
            prefixes = (folder,) if folder else ()
            if not folder:
                return f"{path}::{kind}:{rest}"
        else:
            prefixes = (module, module.rsplit(".__init__", 1)[0] if module.endswith(".__init__") else None)
        for prefix in prefixes:
            if prefix and rest.startswith(prefix + "."):
                return f"{path}::{kind}:{rest[len(prefix) + 1:]}"
        return None

    db = Path(os.environ["HOME"], ".cache/codebase-memory-mcp", project + ".db")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    nodes = {nid: endpoint(label, qname, path) for nid, label, qname, path in conn.execute(
        "SELECT id, label, qualified_name, file_path FROM nodes WHERE project = ?", (project,))}
    owners: dict[int, list[str]] = {}
    for owner, method, name in conn.execute(
            "SELECT e.source_id, e.target_id, b.name FROM edges e JOIN nodes b ON b.id = e.target_id "
            "WHERE e.project = ? AND e.type = 'DEFINES_METHOD' AND b.label = 'Method'", (project,)):
        if nodes.get(owner) and "::" in nodes[owner]:
            path, _, chain = nodes[owner].partition("::")
            owners.setdefault(method, []).append(f"{path}::method:{chain.split(':', 1)[1]}.{name}")

    def endpoints(nid: int) -> list[str]:
        node = nodes.get(nid)
        if node and nid in owners and "::method:" in node and "." not in node.partition("::method:")[2]:
            # A Go method: one node for every receiver. CBM_SHARED_METHODS=drop leaves out a
            # node shared by several receivers instead of fanning it out to each.
            if len(owners[nid]) > 1 and os.environ.get("CBM_SHARED_METHODS") == "drop":
                return []
            return owners[nid]
        return [node] if node else []

    rows = conn.execute(
        "SELECT e.type, e.source_id, e.target_id, json_extract(e.properties, '$.confidence') FROM edges e "
        f"WHERE e.project = ? AND e.type IN ({','.join('?' * len(RELS))})", (project, *RELS))
    count = 0
    with open(out_path, "w") as out:
        for rel, a, b, conf in rows:
            for src in endpoints(a):
                for dst in endpoints(b):
                    if src == dst:
                        continue
                    name = rel
                    if rel == "IMPORTS":
                        name = "IMPORTS_FROM" if "::" not in dst else "IMPORTS"
                    out.write(json.dumps({"rel": name, "from": src, "to": dst,
                                          "confidence": "" if conf is None else f"{conf:.2f}"}) + "\n")
                    count += 1
                    if name == "IMPORTS":
                        # The tool keeps symbol imports only; the file they come from is
                        # derived from the symbol's own file (no barrel hop), labelled so.
                        out.write(json.dumps({"rel": "IMPORTS_FROM", "from": src,
                                              "to": dst.split("::")[0], "confidence": "derived"}) + "\n")
    print(count, "edges", project)


if __name__ == "__main__":
    main()
