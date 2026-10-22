"""B3: score the live stack's code graph and time push-to-ready, one fixture step at a time.

  python live.py score --step N   # live edges vs the answer key for step N
  python live.py step N           # push step N to the copy's main, resync, wait, score

Edges are read from the stack's Neo4j in answer-key notation and scored with the
offline scorer (``cg_eval.score``), so live and offline numbers compare directly.
A step is ready when no file is still waiting to index and the repo's code-graph
build state says ready, settled after the push. Results append to reports/b3/steps.jsonl.
"""
import os
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from pathlib import Path

from neo4j import GraphDatabase

HERE = Path(__file__).resolve().parent
EVAL = HERE.parent.parent
sys.path.insert(0, str(EVAL / "src"))
from cg_eval.offline import UNNAMED_KINDS  # noqa: E402
from cg_eval.score import load_key, score_edges  # noqa: E402

import setup  # noqa: E402  (admin API helpers; reads secrets from 0600 files)

logging.getLogger("neo4j").setLevel(logging.ERROR)
FIXTURE = Path(os.environ["FIXTURE_DIR"])  # set by bench/env.sh
MAIN = "pipeshub-codegraph-fixture"
OUT = EVAL / "reports" / "b3"
WAITING = ("QUEUED", "IN_PROGRESS", "NOT_STARTED", "PAUSED")


def driver():
    pw = next(l.split("=", 1)[1] for l in (HERE / "b3.env").read_text().splitlines() if l.startswith("NEO4J_PASSWORD="))
    return GraphDatabase.driver("bolt://127.0.0.1:37687", auth=("neo4j", pw))


def connector_id() -> str:
    return json.loads((HERE / "state.json").read_text())["connector_id"]


def live_edges(cid: str) -> list[dict]:
    """The builder's edges for this connector, as ``path`` or ``path::kind:Qualified``."""
    with driver().session() as s:
        paths = {r["id"]: r["p"] for r in s.run(
            "MATCH (r:Record {connectorId: $c}) MATCH (f:Codefiles {id: r.id}) RETURN r.id AS id, f.filePath AS p", c=cid)}

        def endpoint(labels: list[str], key: str, rec: str | None, q: str | None) -> str | None:
            if "Record" in labels:
                return paths.get(key)
            path = paths.get(rec)
            kind, _, tail = (q or "").partition(":")
            if not path:
                return None
            if not tail or kind in UNNAMED_KINDS or (tail.startswith("L") and tail[1:2].isdigit()) or tail == "anonymous":
                return path
            return f"{path}::{q}"

        out = []
        for r in s.run("MATCH (a)-[e:RECORD_RELATION {source: 'code_graph'}]->(b) "
                       "WHERE a.connectorId = $c OR b.connectorId = $c "
                       "RETURN labels(a) AS la, a.id AS ka, a.recordId AS ra, a.qualifiedName AS qa, "
                       "labels(b) AS lb, b.id AS kb, b.recordId AS rb, b.qualifiedName AS qb, "
                       "e.relationshipType AS rel, e.confidence AS c", c=cid):
            src, dst = endpoint(r["la"], r["ka"], r["ra"], r["qa"]), endpoint(r["lb"], r["kb"], r["rb"], r["qb"])
            if src and dst:
                out.append({"rel": r["rel"], "from": src, "to": dst, "confidence": r["c"]})
    return out


def status(cid: str) -> dict:
    with driver().session() as s:
        waiting = s.run("MATCH (r:Record {connectorId: $c}) WHERE r.indexingStatus IN $w RETURN count(r) AS n",
                        c=cid, w=list(WAITING)).single()["n"]
        # Classification runs after indexing, inside the same record event.
        enriching = s.run("MATCH (r:Record {connectorId: $c}) WHERE r.extractionStatus IN ['IN_PROGRESS', 'QUEUED'] "
                          "RETURN count(r) AS n", c=cid).single()["n"]
        builds = s.run("MATCH (p:SyncPoint) WHERE p.edgeBuildStatus IS NOT NULL RETURN p.edgeBuildStatus AS st, "
                       "p.edgeBuildPending AS pending, p.lastEdgeBuildAt AS at, p.edgeBuildLastError AS err").data()
        latest = s.run("MATCH (r:Record {connectorId: $c}) RETURN max(r.updatedAtTimestamp) AS t", c=cid).single()["t"]
    return {"waiting": waiting, "enriching": enriching, "builds": builds, "latest_record_update": latest}


def score(step: int, cid: str) -> dict:
    key = load_key(EVAL / "gold" / f"step-{step}" / MAIN)
    s = score_edges(key, live_edges(cid))
    return {"overall": s["overall"], "by_relation": s.get("by_relation", {})}


def _edge_set(cid: str) -> frozenset:
    return frozenset((e["rel"], e["from"], e["to"]) for e in live_edges(cid))


def wait_ready(cid: str, since_ms: int, timeout_s: int = 3600) -> dict:
    """Seconds from *since_ms* until: nothing waits to index (indexed), the edges stop changing
    for good (graph_converged), the build state first reads ready with nothing pending
    (status_ready: what a person or agent is told), and nothing is being classified either
    (quiescent: the next step is pushed only then, so steps never overlap)."""
    started, indexed_at, ready_at, quiet_since, seen = time.time(), None, None, None, []
    history: list[tuple[float, frozenset]] = []
    since_s = since_ms / 1000
    while time.time() - started < timeout_s:
        now = time.time()
        history.append((now, _edge_set(cid)))
        st = status(cid)
        changed = (st["latest_record_update"] or 0) > since_ms
        if st["waiting"] == 0 and changed and indexed_at is None:
            indexed_at = now
        for b in st["builds"]:
            if b["err"] and b["err"] not in seen:
                seen.append(b["err"])
        ready = any(b["st"] == "ready" and not b["pending"] and (b["at"] or 0) > since_ms for b in st["builds"])
        if ready and st["waiting"] == 0 and ready_at is None:
            ready_at = now
        quiet = ready and st["waiting"] == 0 and st["enriching"] == 0
        quiet_since = (quiet_since or now) if quiet else None
        if quiet_since and now - quiet_since >= 15:  # idle for 15 s, not between two events
            final = history[-1][1]
            converged = history[-1][0]
            for t, edges in reversed(history):
                if edges != final:
                    break
                converged = t
            return {"indexed_s": round((indexed_at or now) - since_s, 1),
                    "graph_converged_s": round(converged - since_s, 1),
                    "status_ready_s": round((ready_at or now) - since_s, 1),
                    "quiescent_s": round(quiet_since - since_s, 1), "build_errors": seen}
        time.sleep(3)
    return {"timeout": True, "build_errors": seen, "last_status": status(cid)}


def step(n: int, repeat: int = 1) -> dict:
    cid = connector_id()
    # Forced so a repeat can return main to step 0; within a repeat every push is a fast-forward.
    subprocess.run(["git", "-C", str(FIXTURE), "push", "-q", "--force", "origin",
                    f"step-{n}^{{commit}}:refs/heads/main"], check=True, capture_output=True)
    pushed_ms = int(time.time() * 1000)
    setup.resync(cid)
    timing = wait_ready(cid, pushed_ms)
    result = {"step": n, "repeat": repeat, "pushed_ms": pushed_ms, **timing, "score": score(n, cid)}
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / "steps.jsonl").open("a") as f:
        f.write(json.dumps(result) + "\n")
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sc = sub.add_parser("score")
    sc.add_argument("--step", type=int, default=0)
    st = sub.add_parser("step")
    st.add_argument("n", type=int)
    rn = sub.add_parser("run", help="steps 1..7, then back to 0, --repeats times")
    rn.add_argument("--repeats", type=int, default=3)
    sub.add_parser("status")
    sub.add_parser("initial")
    args = ap.parse_args()
    cid = connector_id()
    if args.cmd == "score":
        print(json.dumps(score(args.step, cid)["overall"]))
    elif args.cmd == "status":
        print(json.dumps(status(cid)))
    elif args.cmd == "initial":
        # The first sync, timed from when setup enabled the connector.
        enabled_ms = int(json.loads((HERE / "state.json").read_text())["enabled"] * 1000)
        r = {"step": 0, "initial": True, "pushed_ms": enabled_ms, **wait_ready(cid, enabled_ms), "score": score(0, cid)}
        OUT.mkdir(parents=True, exist_ok=True)
        with (OUT / "steps.jsonl").open("a") as f:
            f.write(json.dumps(r) + "\n")
        print(json.dumps({k: r[k] for k in r if k != "score"} | {"overall": r["score"]["overall"]}))
    elif args.cmd == "run":
        for rep in range(1, args.repeats + 1):
            for n in [*range(1, 8), 0]:
                r = step(n, rep)
                print(json.dumps({k: r[k] for k in r if k != "score"} | {"overall": r["score"]["overall"]}), flush=True)
    else:
        r = step(args.n)
        print(json.dumps({k: r[k] for k in r if k != "score"} | {"overall": r["score"]["overall"]}))


if __name__ == "__main__":
    main()
