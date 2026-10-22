"""Graphify graph.json -> edges in the key's form (rel, from, to, confidence).

A node is a file (`label` = its file name) or a symbol in `source_file`: `name()`
a function, `.name()` a method, `Name` a class. A symbol's owner comes from the
owner's `method` or `contains` edge, which gives `Owner.name`. Graphify's
`calls` and `indirect_call` are CALLS, `inherits` is heritage, and an `imports`
or `imports_from` edge is IMPORTS_FROM to a file node and IMPORTS to a symbol.
Edges to nodes outside the repo (no source_file) are dropped: the key never
names them, so they could only count against the tool.

usage: python graphify_edges.py <graph.json> <out edges.jsonl>
"""
import json
import sys

REL = {"calls": "CALLS", "indirect_call": "CALLS", "inherits": "INHERITS",
       "implements": "IMPLEMENTS", "extends": "INHERITS"}


def main() -> None:
    graph = json.load(open(sys.argv[1]))
    nodes = {n["id"]: n for n in graph["nodes"]}
    # A file node is labelled with its file name or a tail of its path (`requests/utils.py`).
    files = {nid for nid, n in nodes.items()
             if n.get("source_file") and "(" not in n.get("label", "") and "." in n.get("label", "")
             and ("/" + n["source_file"]).endswith("/" + n["label"].lstrip("/"))}
    parent = {}
    for e in graph["links"]:
        if e["relation"] in ("method", "contains") and e["source"] not in files \
                and e["source"] in nodes and e["target"] in nodes:
            parent.setdefault(e["target"], e["source"])

    def name(nid: str) -> str:
        label = nodes[nid].get("label", "")
        return label.lstrip(".").split("(")[0]

    def endpoint(nid: str):
        n = nodes.get(nid)
        if not n or not n.get("source_file") or n.get("file_type") != "code":
            return None
        if nid in files:
            return n["source_file"]
        chain, seen, cur = [name(nid)], {nid}, parent.get(nid)
        while cur and cur not in seen and cur not in files:
            seen.add(cur)
            chain.insert(0, name(cur))
            cur = parent.get(cur)
        kind = "method" if nodes[nid].get("label", "").startswith(".") else (
            "function" if nodes[nid].get("label", "").endswith(")") else "class")
        return f"{n['source_file']}::{kind}:{'.'.join(chain)}"

    out = open(sys.argv[2], "w")
    count = 0
    for e in graph["links"]:
        rel = e["relation"]
        src, dst = endpoint(e["source"]), endpoint(e["target"])
        if not src or not dst:
            continue
        if rel in ("imports", "imports_from"):
            rel = "IMPORTS_FROM" if e["target"] in files else "IMPORTS"
        elif rel in REL:
            rel = REL[rel]
        else:
            continue
        out.write(json.dumps({"rel": rel, "from": src, "to": dst,
                              "confidence": e.get("confidence")}) + "\n")
        count += 1
    print(count, "edges")


if __name__ == "__main__":
    main()
