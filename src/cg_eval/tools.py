"""Agent-tool cases: call PipesHub's real CodeGraph tools against the offline graph.

Each case names the review finding it checks. Cases with a measurable answer
(callers, callees, file listings) are scored against the answer key; the rest
are pass/fail checks of a behaviour the review says is broken.
"""
from __future__ import annotations

import json
from collections import defaultdict

from .memgraph import MemBlobStore, MemGraph
from .offline import ORG, record_id_for
from .score import load_key

USER = "user-eval"
EDGE_LANGS = {"python", "typescript", "tsx", "javascript"}


def _payload(result) -> dict:
    text = result[1] if isinstance(result, tuple) else result
    return json.loads(text) if isinstance(text, str) else text


def _ep(row: dict) -> str:
    q = row.get("qualified_name") or ""
    tail = q.partition(":")[2]
    return f"{row.get('file_path')}::{tail}" if tail and not tail[:1] == "L" else str(row.get("file_path"))


def _norm(ep: str) -> str:
    path, sep, q = ep.partition("::")
    return f"{path}::{q.partition(':')[2]}" if sep else path


def _prf(expected: set, got: set) -> dict:
    tp = len(expected & got)
    return {"precision": tp / len(got) if got else None,
            "recall": tp / len(expected) if expected else None,
            "expected": len(expected), "got": len(got)}


def _case(id_: str, passed: bool, detail: str, tags: list[str], **metrics) -> dict:
    return {"id": id_, "passed": passed, "detail": detail, "tags": tags, "metrics": metrics}


async def run_tool_cases(graph: MemGraph, repos: dict, args) -> list[dict]:
    from app.agents.actions.code_graph.code_graph import CodeGraph

    from .run import ADMIN, CONNECTOR, MAIN

    key = load_key(args.key / f"step-{args.step}" / MAIN)
    tool = CodeGraph({"org_id": ORG, "user_id": USER, "graph_provider": graph,
                      "blob_store": MemBlobStore(graph)})
    langs = {f["path"]: f["lang"] for f in key["files"]}
    calls = [e for e in key["edges"] if e["rel"] == "CALLS" and "::" in e["to"]
             and langs.get(e["to"].partition("::")[0]) in EDGE_LANGS]
    callers_of, callees_of = defaultdict(set), defaultdict(set)
    for e in calls:
        callers_of[e["to"]].add(_norm(e["from"]))
        callees_of[e["from"]].add(_norm(e["to"]))
    cases: list[dict] = []

    async def neighbours(ep: str, direction: str, **kw) -> dict:
        path, _, q = ep.partition("::")
        return _payload(await tool.get_neighbour(connector_id=CONNECTOR, file_path=path,
                                                 qualified_name=q, direction=direction,
                                                 repo=MAIN, **kw))

    # Callers / callees against the key (A-2, A-5 and the edge-recall gaps, seen through the tool).
    for label, table, direction, k in (("callers", callers_of, "inbound", 25),
                                       ("callees", callees_of, "outbound", 25)):
        picked = sorted(table, key=lambda t: (-len(table[t]), t))[:k]
        totals = {"tp": 0, "got": 0, "expected": 0}
        worst = []
        for ep in picked:
            out = await neighbours(ep, direction, edge_types=["CALLS"], limit=200)
            got = {_ep(r) for r in out.get("neighbors", [])}
            exp = table[ep]
            totals["tp"] += len(exp & got)
            totals["got"] += len(got)
            totals["expected"] += len(exp)
            worst.append((len(exp & got) / len(exp), ep))
        p = totals["tp"] / totals["got"] if totals["got"] else None
        r = totals["tp"] / totals["expected"] if totals["expected"] else None
        worst.sort()
        cases.append(_case(
            f"{label}-vs-key", bool(p is not None and p >= 0.9 and r is not None and r >= 0.8),
            f"{label} of the {k} most-connected symbols: precision "
            f"{'—' if p is None else f'{p:.0%}'}, recall {'—' if r is None else f'{r:.0%}'}; "
            f"worst {worst[0][1]} ({worst[0][0]:.0%})",
            ["A-5", "R-1"], precision=p, recall=r, **totals))

    # A-2: rows must say which way they point; multi-hop rows must not repeat or include the anchor.
    hub = max((ep for ep in callers_of if ep in callees_of),
              key=lambda ep: len(callers_of[ep]) + len(callees_of[ep]))
    out = await neighbours(hub, "any", edge_types=["CALLS"], limit=200)
    rows = out.get("neighbors", [])
    has_direction = bool(rows) and all("direction" in r for r in rows)
    cases.append(_case("A-2-direction", has_direction,
                       f"{len(rows)} CALLS rows around {hub}; rows carry a direction: {has_direction}",
                       ["A-2"]))
    out = await neighbours(hub, "any", depth=2, limit=500)
    seen = [(_ep(r), r.get("relation"), r.get("direction")) for r in out.get("neighbors", [])]
    dupes = len(seen) - len(set(seen))
    self_rows = sum(1 for ep, *_ in seen if ep == _norm(hub))
    deep = [r for r in out.get("neighbors", []) if (r.get("hop") or 1) > 1]
    # A second-hop row is unreadable without the node it was reached through.
    has_via = bool(deep) and all("via" in r for r in deep)
    cases.append(_case("A-2-multihop", dupes == 0 and self_rows == 0 and has_via,
                       f"depth 2: {len(seen)} rows, {dupes} duplicates, anchor listed {self_rows}x, "
                       f"rows carry `via`: {has_via}", ["A-2"]))

    # A-2: find_symbol_path must follow edge direction.
    full_of: dict[str, str] = {}
    ncallees: dict[str, set[str]] = defaultdict(set)
    for e in calls:
        if "::" not in e["from"]:
            continue
        full_of[_norm(e["from"])], full_of[_norm(e["to"])] = e["from"], e["to"]
        ncallees[_norm(e["from"])].add(_norm(e["to"]))
    positives, negatives = [], []
    for a, mids in sorted(ncallees.items()):
        for b in sorted(mids):
            positives += [(full_of[a], full_of[c]) for c in sorted(ncallees.get(b, ())) if c != a]
        sibs = sorted(mids)
        for x, y in zip(sibs, sibs[1:]):
            # Same-file symbols may share a class, which legitimately connects them.
            if x.partition("::")[0] == y.partition("::")[0]:
                continue
            if not _reaches(x, y, ncallees) and not _reaches(y, x, ncallees):
                negatives.append((full_of[x], full_of[y]))
    found_pos = found_neg = 0
    for a, c in positives[:15]:
        found_pos += bool((await _path(tool, CONNECTOR, a, c, repo=MAIN)).get("found"))
    for x, y in negatives[:15]:
        found_neg += bool((await _path(tool, CONNECTOR, x, y, repo=MAIN)).get("found"))
    n_pos, n_neg = min(15, len(positives)), min(15, len(negatives))
    cases.append(_case("A-2-directed-path", found_neg == 0,
                       f"spurious paths between unrelated callees of one caller: {found_neg}/{n_neg}; "
                       f"real two-hop call paths found: {found_pos}/{n_pos}", ["A-2"],
                       spurious=found_neg, real_found=found_pos))

    # A-1: a line range returns those lines, not the whole class.
    for tc in key["tool_cases"]:
        if tc["tool"] != "read_code" or tc["id"] != "A-1-line-range":
            continue
        out = _payload(await tool.read_code(connector_id=CONNECTOR, repo=MAIN, **tc["args"]))
        code = "\n".join(b.get("code", "") for b in out.get("blocks", []))
        n_lines = code.count("\n") + 1 if code else 0
        exp = tc["expect"]
        ok = (exp["contains"] in code and n_lines <= exp["max_lines"]
              and exp["must_not_contain"] not in code)
        cases.append(_case(tc["id"], ok, f"lines {tc['args']['lines']} returned {n_lines} lines "
                           f"({len(code):,} chars)", tc["tags"], lines=n_lines, chars=len(code)))

    # A-7: byte-identical files must both stay visible.
    for path in ("backend/app/shared/retry.py", "backend/app/legacy/retry.py"):
        out = _payload(await tool.read_code(connector_id=CONNECTOR, file_path=path, repo=MAIN))
        ok = bool(out.get("blocks")) and "error" not in out
        cases.append(_case(f"A-7-read-{path.rsplit('/', 2)[-2]}", ok,
                           f"read_code({path}): {'ok' if ok else out.get('error') or 'no blocks'}",
                           ["A-7"]))
    out = _payload(await tool.get_neighbour(connector_id=CONNECTOR, qualified_name="retry_with_backoff"))
    paths = {c.get("file_path") for c in out.get("candidates", [])} | (
        {out["symbol"].get("file_path")} if out.get("symbol") else set())
    want = {"backend/app/shared/retry.py", "backend/app/legacy/retry.py"}
    cases.append(_case("A-7-name-lookup", want <= paths,
                       f"bare-name lookup of retry_with_backoff saw {sorted(p for p in paths if p)}",
                       ["A-7"]))

    # A-3: the same path in two repos of one connector must not be merged or picked silently.
    if ADMIN in repos:
        out = _payload(await tool.read_code(connector_id=CONNECTOR, file_path="backend/app/main.py"))
        code = "\n".join(b.get("code", "") for b in out.get("blocks", []))
        named = sorted(out.get("repos") or [])
        picked = _payload(await tool.read_code(connector_id=CONNECTOR, file_path="backend/app/main.py",
                                               repo=ADMIN))
        picked_code = "\n".join(b.get("code", "") for b in picked.get("blocks", []))
        ok = (not code and named == sorted([MAIN, ADMIN])
              and "replica_path" in picked_code and picked.get("repo") == ADMIN)
        cases.append(_case("A-3-same-path", ok,
                           f"without repo: {'error naming ' + str(named) if named else 'returned code'}; "
                           f"with repo={ADMIN}: {'admin code' if 'replica_path' in picked_code else 'wrong'}"
                           f", labelled {picked.get('repo')!r}", ["A-3"]))

    # S-1: a denied record and an unknown id must get the same answer, and no path.
    denied_path = "backend/app/settings.py"
    denied = record_id_for(MAIN, denied_path)
    graph.denied.add(denied)
    try:
        a = _payload(await tool.read_code(connector_id=CONNECTOR, record_id=denied))
        b = _payload(await tool.read_code(connector_id=CONNECTOR, record_id="rec-0000000000000000"))
    finally:
        graph.denied.discard(denied)
    same = json.dumps(a).replace(denied, "X") == json.dumps(b).replace("rec-0000000000000000", "X")
    leaked = denied_path in json.dumps(a)
    cases.append(_case("S-1-record-id", same and not leaked,
                       f"denied vs unknown answers identical: {same}; path leaked: {leaked}", ["S-1"]))

    # A-4: a storage failure must surface as an error, not as "no callers". Both real
    # providers log and return [] unless the caller passes raise_on_error=True.
    original = graph.get_neighbors_for_nodes_by_relationship_types

    async def boom(*_a, **kw):
        if kw.get("raise_on_error"):
            raise RuntimeError("graph unavailable")
        return []
    graph.get_neighbors_for_nodes_by_relationship_types = boom
    try:
        out = await neighbours(hub, "inbound", edge_types=["CALLS"])
    finally:
        graph.get_neighbors_for_nodes_by_relationship_types = original
    cases.append(_case("A-4-storage-error", "error" in out,
                       f"provider raised; tool returned {'an error' if 'error' in out else json.dumps(out)[:120]}",
                       ["A-4"]))

    # A-5 / O-3: results for a language without cross-file edges must say coverage is partial.
    go = next(s for s in key["symbols"] if s["lang"] == "go" and s["kind"] == "function")
    out = await neighbours(f"{go['path']}::{go['qualified']}", "inbound")
    blob = json.dumps(out).lower()
    signals = [w for w in ("coverage", "graph_status", "partial", "unsupported") if w in blob]
    cases.append(_case("A-5-coverage-signal", bool(signals),
                       f"Go symbol {go['qualified']}: coverage signal {signals or 'none'}", ["A-5", "O-3"]))

    # SC-6: a bare common name must find every exact definition.
    exact = {f"{s['path']}" for s in key["symbols"] if s["name"] == "get"
             and langs.get(s["path"]) in EDGE_LANGS}
    out = _payload(await tool.get_neighbour(connector_id=CONNECTOR, qualified_name="get"))
    seen_paths = {c.get("file_path") for c in out.get("candidates", [])}
    cases.append(_case(f"SC-6-name-lookup-{args.search}", exact <= seen_paths,
                       f"bare 'get': {len(exact & seen_paths)}/{len(exact)} exact definitions listed "
                       f"({args.search} search)", ["SC-6"]))

    # Listing a file returns what it defines.
    hits = total = 0
    files = sorted({s["path"] for s in key["symbols"] if s["lang"] in EDGE_LANGS})[::12]
    for path in files:
        out = _payload(await tool.query_code_graph(connector_id=CONNECTOR, select=path, repo=MAIN))
        got = {(n.get("qualified_name") or "").partition(":")[2] for n in out.get("nodes", [])}
        want = {s["qualified"].partition(":")[2] for s in key["symbols"] if s["path"] == path}
        hits += len(want & got)
        total += len(want)
    cases.append(_case("query-file-symbols", total and hits / total >= 0.95,
                       f"{hits}/{total} declared symbols listed across {len(files)} files", [],
                       recall=hits / total if total else None))

    # A-9: the tools that find code without knowing its file, against the key.
    if hasattr(tool, "search_code"):
        sample = [sy for sy in key["symbols"] if sy["lang"] in EDGE_LANGS][::15][:40]
        found = 0
        for sy in sample:
            name = sy["qualified"].partition(":")[2].rsplit(".", 1)[-1]
            out = _payload(await tool.search_code(connector_id=CONNECTOR, pattern=name, repo=MAIN,
                                                  include_tests=True, limit=200))
            found += any(m.get("file_path") == sy["path"] and m.get("qualified_name") == sy["qualified"]
                         for m in out.get("matches", []))
        cases.append(_case("A-9-search-identifier", found >= 0.95 * len(sample),
                           f"{found}/{len(sample)} definitions found by bare name", ["A-9"],
                           recall=found / len(sample) if sample else None))

        hits = total = 0
        for path in files:
            out = _payload(await tool.outline(connector_id=CONNECTOR, file_path=path, repo=MAIN))
            got = {x.get("qualified_name") for x in out.get("symbols", [])}
            want = {sy["qualified"] for sy in key["symbols"] if sy["path"] == path}
            hits += len(want & got)
            total += len(want)
        cases.append(_case("A-9-outline", bool(total) and hits >= 0.95 * total,
                           f"{hits}/{total} declared symbols in outlines of {len(files)} files", ["A-9"],
                           recall=hits / total if total else None))

        hubs_ = sorted(callers_of, key=lambda t: (-len(callers_of[t]), t))[:15]
        exp_files = got_files = 0
        for ep in hubs_:
            path, _, q = ep.partition("::")
            out = _payload(await tool.impact(connector_id=CONNECTOR, file_path=path, qualified_name=q,
                                             depth=1, repo=MAIN, include_tests=True))
            impacted = {f.get("file_path") for f in out.get("impacted_files", [])}
            want = {c.partition("::")[0] for c in callers_of[ep]}
            exp_files += len(want)
            got_files += len(want & impacted)
        cases.append(_case("A-9-impact-files", bool(exp_files) and got_files >= 0.8 * exp_files,
                           f"{got_files}/{exp_files} caller files of the 15 hubs named by impact", ["A-9"],
                           recall=got_files / exp_files if exp_files else None))

        out = _payload(await tool.entry_points(connector_id=CONNECTOR, repo=MAIN))
        points = out.get("entry_points", [])
        cases.append(_case("A-9-entry-points", bool(points) and all(p.get("reason") for p in points),
                           f"{len(points)} entry points; first: "
                           f"{', '.join(p['file_path'] + '#' + p['qualified_name'] for p in points[:3])}",
                           ["A-9"]))

    # A-8: what typical results cost the model's context, in characters of JSON.
    def _sizes(raw: list[str]) -> dict:
        n = sorted(len(r) for r in raw)
        return {"calls": len(n), "median": n[len(n) // 2] if n else 0,
                "p90": n[int(len(n) * 0.9)] if n else 0, "max": n[-1] if n else 0}

    hubs = sorted(callers_of, key=lambda t: (-len(callers_of[t]), t))[:25]
    walks, deep, reads = [], [], []
    for ep in hubs:
        path, _, q = ep.partition("::")
        args_ = {"connector_id": CONNECTOR, "file_path": path, "qualified_name": q, "repo": MAIN}
        walks.append((await tool.get_neighbour(direction="inbound", **args_))[1])
        deep.append((await tool.get_neighbour(direction="any", depth=2, **args_))[1])
        reads.append((await tool.read_code(**args_))[1])
    dirs = sorted({p.rpartition("/")[0] for p in files if "/" in p})[:10]
    listings = [(await tool.query_code_graph(connector_id=CONNECTOR, select=d + "/", repo=MAIN))[1]
                for d in dirs]
    sizes = {"get_neighbour": _sizes(walks), "get_neighbour_depth2": _sizes(deep),
             "read_code": _sizes(reads), "query_code_graph_dir": _sizes(listings)}
    cases.append(_case("A-8-result-size", True, "; ".join(
        f"{k} median {v['median']} / p90 {v['p90']} chars" for k, v in sizes.items()),
        ["A-8"], **{f"{k}_{m}": v[m] for k, v in sizes.items() for m in ("median", "p90", "max")}))
    return cases


def _reaches(a: str, b: str, callees_of: dict, depth: int = 4) -> bool:
    frontier, seen = {a}, {a}
    for _ in range(depth):
        nxt = set()
        for n in frontier:
            for m in callees_of.get(n, ()):
                if m == b:
                    return True
                if m not in seen:
                    seen.add(m)
                    nxt.add(m)
        frontier = nxt
    return False


async def _path(tool, connector: str, a: str, b: str, **kw) -> dict:
    pa, _, qa = a.partition("::")
    pb, _, qb = b.partition("::")
    return _payload(await tool.find_symbol_path(connector_id=connector, file_path_a=pa,
                                                qualified_name_a=qa, file_path_b=pb,
                                                qualified_name_b=qb, **kw))
