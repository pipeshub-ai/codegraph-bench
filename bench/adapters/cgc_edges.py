"""CodeGraphContext 0.6.13 (KuzuDB backend) -> edges in the key's form.

Reads its KuzuDB graph directly (Cypher through the kuzu library CGC ships with). A Function named `<module>` is a
file's top-level code, so its calls come from the file. A symbol's owner is
whatever CONTAINS it (any type node -- Class, Go Struct, Interface, Enum,
Record, ... -- or another Function), which gives the `Owner.name` chain. Each
CALLS edge carries its own confidence label; HEURISTIC_CALLS are INFERRED;
INHERITS and IMPLEMENTS are heritage. Its IMPORTS point at unresolved module names, not
files, so it has no import edges to score.

usage: <cgc venv>/bin/python cgc_edges.py <kuzu db path> <repo dir> <out edges.jsonl>
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


# Every node table CGC 0.6.13 uses for a type (Go structs and interfaces, Java records, ...).
TYPE_LABELS = ("Class", "Struct", "Interface", "Enum", "Record", "Trait", "Object", "Mixin",
               "Union", "Extension", "Annotation")


def query(conn, cypher: str) -> list[dict]:
    result = conn.execute(cypher)
    cols = result.get_column_names()
    rows = []
    while result.has_next():
        rows.append(dict(zip(cols, result.get_next())))
    return rows


def main() -> None:
    import kuzu  # noqa: PLC0415 -- CGC's own dependency; run with its venv's python

    db, repo, out_path = sys.argv[1], str(Path(sys.argv[2]).resolve()), sys.argv[3]
    conn = kuzu.Connection(kuzu.Database(db, read_only=True))
    nodes: dict[str, tuple[str, str, str]] = {}  # uid -> (kind, name, path)
    spans: dict[str, tuple[int, int]] = {}  # type uid -> (first line, last line)
    for row in query(conn, "MATCH (f:Function) RETURN f.uid AS uid, f.name AS name, f.path AS path"):
        nodes[row["uid"]] = ("function", row["name"], row["path"])
    for label in TYPE_LABELS:
        for row in query(conn, f"MATCH (c:`{label}`) RETURN c.uid AS uid, c.name AS name, c.path AS path, "
                                 "c.line_number AS a, c.end_line AS b"):
            nodes[row["uid"]] = ("class", row["name"], row["path"])
            if row["a"] is not None and row["b"] is not None:
                spans[row["uid"]] = (row["a"], row["b"])
    parent: dict[str, str] = {}
    labels = ", ".join(f"'{label}'" for label in ("Function", *TYPE_LABELS))
    for row in query(conn, f"MATCH (p)-[:CONTAINS]->(f) WHERE label(p) IN [{labels}] "
                              f"AND label(f) IN [{labels}] RETURN p.uid AS p, f.uid AS f"):
        parent.setdefault(row["f"], row["p"])
    # A nested type (Java `Lists.ReverseList`, Python `class Meta`) hangs off its File, not
    # its outer type: take the innermost type in the same file whose lines enclose it.
    by_path: dict[str, list[str]] = {}
    for uid in spans:
        by_path.setdefault(nodes[uid][2], []).append(uid)
    for uids in by_path.values():
        for uid in uids:
            if uid in parent:
                continue
            a, b = spans[uid]
            outer = [o for o in uids if o != uid and spans[o][0] <= a and b <= spans[o][1] and spans[o] != (a, b)]
            if outer:
                parent[uid] = min(outer, key=lambda o: spans[o][1] - spans[o][0])

    def endpoint(uid: str) -> str | None:
        node = nodes.get(uid)
        if node is None or not node[2] or not node[2].startswith(repo + "/"):
            return None
        rel = node[2][len(repo) + 1:]
        if node[1] == "<module>":
            return rel
        chain, seen, cur = [node[1]], {uid}, parent.get(uid)
        while cur and cur not in seen and cur in nodes and nodes[cur][1] != "<module>":
            seen.add(cur)
            chain.insert(0, nodes[cur][1])
            cur = parent.get(cur)
        kind = node[0]
        if kind == "function" and uid in parent and nodes.get(parent[uid], ("",))[0] == "class":
            kind = "method"
        return f"{rel}::{kind}:{'.'.join(chain)}"

    count = 0
    with open(out_path, "w") as out:
        for rel, label, conf in (("CALLS", "CALLS", "e.confidence_label"),
                                 ("CALLS", "HEURISTIC_CALLS", "'INFERRED'"),
                                 ("INHERITS", "INHERITS", "'EXTRACTED'"),
                                 ("IMPLEMENTS", "IMPLEMENTS", "'EXTRACTED'")):
            for row in query(conn, f"MATCH (a)-[e:{label}]->(b) RETURN a.uid AS a, b.uid AS b, {conf} AS c"):
                src, dst = endpoint(row["a"]), endpoint(row["b"])
                if src and dst and src != dst:
                    out.write(json.dumps({"rel": rel, "from": src, "to": dst,
                                          "confidence": row["c"] or "EXTRACTED"}) + "\n")
                    count += 1
    print(count, "edges")


if __name__ == "__main__":
    main()
