"""B5a: SWE-bench Lite file localization without an LLM.

For each issue, the repo at its base commit goes through PipesHub's code-graph
pipeline in memory (``offline``: real parser, projection and edge builder, the
path B2 showed builds the same graph as Neo4j). Three signals score every file
PipesHub indexes as code:

  bm25   SWE-bench's own retrieval baseline: the issue text against file text.
  dense  embedding_1 over PipesHub's own embedding units (``_build_code_documents``),
         the issue against each unit; a file scores its best unit.
  graph  symbols and paths the issue names, matched to their definitions in the
         graph, then spread one hop along its edges (calls, heritage, imports).

The scores are saved per issue; ``fuse`` combines them with weights chosen on
the dev split only and reports Recall@k and Acc@k on test. Gold files are the
files the issue's patch modifies (files it creates cannot be retrieved). Test
files are candidates for no method, as in PipesHub's tools by default.

usage: python -m cg_eval.swebench run --split dev|test [--only ID ...] [--no-dense]
       python -m cg_eval.swebench fuse
"""
import os
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import subprocess
import time
from collections import defaultdict
from pathlib import Path

from .run import CONNECTOR  # noqa: F401  (sets PIPESHUB_PY on sys.path)
from .memgraph import MemGraph
from .offline import ORG, IndexedRepo, build_edges, code_edges, index_files

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "bench" / "swebench"
OUT = ROOT / "reports" / "b5a"
MODELS = Path(os.environ.get("MODELS_JSON", Path(__file__).resolve().parents[2] / "models.json"))
KEEP = 300  # scores kept per signal and issue; the metrics stop at 10
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SUBWORD = re.compile(r"[A-Z]+(?![a-z])|[A-Z]?[a-z]+|[0-9]+")
STOP = frozenset("""the and for that this with from not but are was were have has had when then than into
also only should would could does did doing been being there their they them what which while where why how
self none true false return import class def raise else elif pass break continue lambda yield async await
issue error example expected actual output result results using used use test tests code file files line
value values function method object type types call calls called python version""".split())


# -- data ------------------------------------------------------------------

def instances(split: str) -> list[dict]:
    """Lite's dev or test split, or ``fresh``: SWE-bench Verified minus every Lite issue (held out for B5-G1)."""
    import pyarrow.parquet as pq

    lite = lambda s: pq.read_table(DATA / "lite" / "data" / f"{s}-00000-of-00001.parquet").to_pylist()  # noqa: E731
    if split != "fresh":
        return lite(split)
    seen = {r["instance_id"] for s in ("dev", "test") for r in lite(s)}
    verified = pq.read_table(next((DATA / "verified").rglob("*.parquet"))).to_pylist()
    return [r for r in verified if r["instance_id"] not in seen]


def gold_files(patch: str) -> tuple[list[str], int]:
    """(files the patch modifies, files it creates)."""
    modified, created = [], 0
    for block in re.split(r"^diff --git ", patch, flags=re.M)[1:]:
        path = re.match(r"a/(\S+) b/", block)[1]
        if re.search(r"^--- /dev/null", block, flags=re.M):
            created += 1
        else:
            modified.append(path)
    return modified, created


def tree_at(repo: str, commit: str) -> dict[str, bytes]:
    """Every file at *commit*, read from the bare clone (a symlink yields its target, as GitHub's API does)."""
    git = DATA / "git" / f"{repo.replace('/', '__')}.git"
    listing = subprocess.run(["git", "-C", str(git), "ls-tree", "-r", "-z", commit],
                             capture_output=True, check=True).stdout.split(b"\0")
    entries = [(e.split(b"\t", 1)[1].decode(), e.split()[2].decode()) for e in listing
               if e and e.split()[1] == b"blob"]
    proc = subprocess.run(["git", "-C", str(git), "cat-file", "--batch"],
                          input="".join(sha + "\n" for _, sha in entries).encode(),
                          capture_output=True, check=True).stdout
    files, pos = {}, 0
    for path, _ in entries:
        header_end = proc.index(b"\n", pos)
        size = int(proc[pos:header_end].split()[2])
        files[path] = proc[header_end + 1:header_end + 1 + size]
        pos = header_end + 1 + size + 1
    return files


# -- signals ---------------------------------------------------------------

def tokens(text: str) -> list[str]:
    out = []
    for word in WORD.findall(text):
        parts = [p.lower() for p in SUBWORD.findall(word)]
        out.append(word.lower())
        if len(parts) > 1:
            out.extend(parts)
    return out


def top(scores: dict[str, float]) -> dict[str, float]:
    return dict(sorted(((k, v) for k, v in scores.items() if v > 0), key=lambda kv: -kv[1])[:KEEP])


def bm25_scores(query: str, docs: dict[str, str]) -> dict[str, float]:
    from rank_bm25 import BM25Okapi

    paths = list(docs)
    model = BM25Okapi([tokens(p.replace("/", " ") + " " + docs[p]) for p in paths])
    return top(dict(zip(paths, map(float, model.get_scores(tokens(query))))))


class Embedder:
    """embedding_1 with a content-hash cache, so files unchanged between commits embed once."""

    def __init__(self) -> None:
        from openai import AzureOpenAI

        cfg = json.loads(MODELS.read_text())["embedding_1"]["configuration"]
        self.model = cfg.get("deploymentName") or cfg["model"]
        # PipesHub's AZURE_EMBEDDING_API_VERSION.
        self.client = AzureOpenAI(api_key=cfg["apiKey"], api_version="2024-02-01", azure_endpoint=cfg["endpoint"])
        self.db = sqlite3.connect(DATA / "embeddings.sqlite", timeout=120)  # shared by parallel workers
        self.db.execute("CREATE TABLE IF NOT EXISTS e (h TEXT PRIMARY KEY, v BLOB)")
        self.tokens = 0

    def __call__(self, texts: list[str]):
        import numpy as np

        keys = [hashlib.sha1(t.encode()).hexdigest() for t in texts]
        have = {}
        for i in range(0, len(keys), 900):
            chunk = keys[i:i + 900]
            have.update(self.db.execute(f"SELECT h, v FROM e WHERE h IN ({','.join('?' * len(chunk))})", chunk))
        missing = list(dict.fromkeys(k for k in keys if k not in have))
        text_of = dict(zip(keys, texts))
        for i in range(0, len(missing), 256):
            batch = missing[i:i + 256]
            for attempt in range(8):
                try:
                    r = self.client.embeddings.create(model=self.model, input=[text_of[k] for k in batch])
                    break
                except Exception as exc:  # rate limits and transient errors: back off and retry
                    if attempt == 7:
                        raise
                    time.sleep(min(60, 2 ** attempt + 1) if "429" in str(exc) else 2)
            self.tokens += r.usage.total_tokens
            rows = [(k, np.asarray(d.embedding, dtype=np.float32).tobytes()) for k, d in zip(batch, r.data)]
            self.db.executemany("INSERT OR REPLACE INTO e VALUES (?, ?)", rows)
            have.update(rows)
        self.db.commit()
        return np.stack([np.frombuffer(have[k], dtype=np.float32) for k in keys])


def dense_scores(embed: Embedder, query: str, graph: MemGraph, code_paths: dict[str, str]) -> dict[str, float]:
    """Each file's best embedding unit against the issue, units built as the indexer builds them."""
    import numpy as np

    from app.models.blocks import BlocksContainer
    from app.modules.transformers.vectorstore import _build_code_documents, _split_to_token_ceiling

    texts, owners = [], []
    for rid, path in code_paths.items():
        blob = graph.blobs.get(graph.records[rid]["virtualRecordId"])
        if not blob:
            continue
        container = BlocksContainer.model_validate(blob["block_containers"])
        blocks = [b for b in container.blocks if str(getattr(b.type, "value", b.type)).lower() == "code"]
        for doc in _build_code_documents(blocks, rid, ORG, f"{path.rsplit('/', 1)[-1]} — {path}"):
            texts.append(doc.page_content)
            owners.append(path)
    if not texts:
        return {}
    units = embed(texts)
    q = embed([_split_to_token_ceiling(query, 8000)[0]])[0]
    sims = units @ q / (np.linalg.norm(units, axis=1) * np.linalg.norm(q) + 1e-9)
    best: dict[str, float] = {}
    for path, s in zip(owners, sims.tolist()):
        best[path] = max(best.get(path, -1.0), s)
    return top(best)


def graph_scores(issue: str, graph: MemGraph, group_id: str, code: set[str]) -> dict[str, dict[str, float]]:
    """Seeds the issue names, scored at their definitions, and their one-hop graph neighbours.

    Returns {"seed": per-file seed weight, "hop": per-file weight spread one hop along edges}.
    """
    defs: dict[str, set[str]] = defaultdict(set)      # bare symbol name -> files defining it
    nodes_in: dict[str, set[str]] = defaultdict(set)  # bare symbol name -> graph endpoints
    for block in graph.blocks.values():
        q = block.get("qualifiedName") or ""
        kind, _, tail = q.partition(":")
        if kind not in ("function", "method", "class", "interface") or not tail:
            continue
        path = graph.path_of(block.get("recordId"))
        name = tail.rsplit(".", 1)[-1]
        if path in code and len(name) >= 3:
            defs[name].add(path)
            nodes_in[name].add(f"{path}::{q}")

    seed: dict[str, float] = defaultdict(float)
    seed_nodes: dict[str, float] = {}
    for m in re.finditer(r"File \"([^\"]+)\", line \d+, in (\w+)", issue):  # traceback frames
        for path in (p for p in code if m[1].endswith(p) or p.endswith(m[1].lstrip("./"))):
            seed[path] += 1.0
    for m in set(re.findall(r"[\w./-]+\.py\b", issue)):
        for path in (p for p in code if p.endswith(m.lstrip("./"))):
            seed[path] += 1.0
    for m in set(re.findall(r"\b[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*){1,}\b", issue)):  # dotted modules
        base = m.replace(".", "/")
        for path in (p for p in code if p.endswith(base + ".py") or p.endswith(base + "/__init__.py")):
            seed[path] += 1.0
    for name in set(WORD.findall(issue)):
        if name.lower() in STOP or name not in defs:
            continue
        weight = 1.0 / len(defs[name])  # a name defined in one file says more than one defined in thirty
        for path in defs[name]:
            seed[path] += weight
        for node in nodes_in[name]:
            seed_nodes[node] = max(seed_nodes.get(node, 0.0), weight)

    hop: dict[str, float] = defaultdict(float)
    if seed_nodes:
        for e in code_edges(graph, group_id):
            for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
                w = seed_nodes.get(a)
                if w:
                    path = b.split("::", 1)[0]
                    if path in code:
                        hop[path] += w
    return {"seed": top(dict(seed)), "hop": top(dict(hop))}


def graph_features(graph: MemGraph, group_id: str, code: set[str]) -> dict:
    """What any seed weighting needs from the graph, so weightings can be compared without a rebuild:
    each symbol's definition files and its one-hop neighbour files, keyed by its qualified tail."""
    defs: dict[str, set[str]] = defaultdict(set)
    for block in graph.blocks.values():
        q = block.get("qualifiedName") or ""
        kind, _, tail = q.partition(":")
        path = graph.path_of(block.get("recordId"))
        if kind in ("function", "method", "class", "interface") and tail and path in code:
            defs[tail].add(path)
    adj: dict[str, set[str]] = defaultdict(set)
    for e in code_edges(graph, group_id):
        for a, b in ((e["from"], e["to"]), (e["to"], e["from"])):
            a_path, _, a_q = a.partition("::")
            tail = a_q.partition(":")[2]
            b_path = b.split("::", 1)[0]
            if tail in defs and a_path in code and b_path in code and b_path != a_path:
                adj[tail].add(b_path)
    return {"defs": {k: sorted(v) for k, v in defs.items()}, "adj": {k: sorted(v) for k, v in adj.items()}}


# How an issue mentions a symbol or file, strongest first; set before any result was seen (B5-G1).
MENTION_WEIGHTS = {"traceback": 3.0, "path": 3.0, "module": 2.0, "qualified": 2.0, "code": 1.0, "word": 0.3}
MAX_SEED_FILES = 15


def seeds_v2(issue: str, feats: dict, code: set[str]) -> dict[str, dict[str, float]]:
    """Seeds weighted by how the issue mentions them; one hop only from strong seeds."""
    by_name: dict[str, list[str]] = defaultdict(list)  # bare name -> qualified tails
    for tail in feats["defs"]:
        by_name[tail.rsplit(".", 1)[-1]].append(tail)

    def files_of(tails: list[str]) -> set[str]:
        return {p for t in tails for p in feats["defs"].get(t, ())}

    seed: dict[str, float] = defaultdict(float)
    strong: dict[str, float] = {}  # tail -> weight, for the hop

    def add_symbol(tails: list[str], weight: float) -> None:
        files = files_of(tails)
        if not files:
            return
        w = weight / len(files)  # a name defined in one file says more than one defined in thirty
        for p in files:
            seed[p] += w
        if weight >= MENTION_WEIGHTS["code"]:
            for t in tails:
                strong[t] = max(strong.get(t, 0.0), w)

    code_spans = " ".join(re.findall(r"```.*?```|`[^`\n]+`", issue, flags=re.S))
    for m in re.finditer(r"File \"([^\"]+)\", line \d+, in (\w+)", issue):
        for p in (p for p in code if m[1].endswith(p) or p.endswith(m[1].lstrip("./"))):
            seed[p] += MENTION_WEIGHTS["traceback"]
        add_symbol(by_name.get(m[2], []), MENTION_WEIGHTS["traceback"])
    for m in set(re.findall(r"[\w./-]+\.py\b", issue)):
        for p in (p for p in code if p.endswith(m.lstrip("./"))):
            seed[p] += MENTION_WEIGHTS["path"]
    for m in set(re.findall(r"\b[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*){1,}\b", issue)):
        base = m.replace(".", "/")
        for p in (p for p in code if p.endswith(base + ".py") or p.endswith(base + "/__init__.py")):
            seed[p] += MENTION_WEIGHTS["module"]
    for m in set(re.findall(r"\b([A-Za-z_]\w*)\.([A-Za-z_]\w*)\b", issue)):
        tail = f"{m[0]}.{m[1]}"
        if tail in feats["defs"]:
            add_symbol([tail], MENTION_WEIGHTS["qualified"])
    for name in set(WORD.findall(issue)):
        if name.lower() in STOP or name not in by_name:
            continue
        kind = "code" if re.search(rf"\b{re.escape(name)}\b", code_spans) else "word"
        add_symbol(by_name[name], MENTION_WEIGHTS[kind])

    kept = dict(sorted(seed.items(), key=lambda kv: -kv[1])[:MAX_SEED_FILES])
    hop: dict[str, float] = defaultdict(float)
    for tail, w in strong.items():
        for p in feats["adj"].get(tail, ()):
            hop[p] += w
    return {"seed2": kept, "hop2": top(dict(hop))}


# -- per issue ---------------------------------------------------------------

async def localize(inst: dict, embed: Embedder | None, graph_only: bool = False) -> dict:
    started = time.perf_counter()
    files = tree_at(inst["repo"], inst["base_commit"])
    graph = MemGraph(ORG, search_backend="neo4j")
    repo = IndexedRepo(inst["repo"].split("/")[1], CONNECTOR, f"group-{inst['instance_id']}")
    await index_files(graph, repo, files)
    await build_edges(graph, repo)
    index_seconds = time.perf_counter() - started

    from app.modules.parsers.code_parser.file_role import FileRole, classify_file_role

    # Test files are left out for every method, as PipesHub's code tools do by default
    # (include_tests=False); SWE-bench keeps test changes out of the gold patch.
    kept = [o for o in repo.outcomes.values()
            if o.route == "code" and classify_file_role(o.path) is not FileRole.TEST]
    code = {o.path for o in kept}
    code_rids = {o.record_id: o.path for o in kept}
    query = inst["problem_statement"]
    gold, created = gold_files(inst["patch"])
    import gzip

    feats = graph_features(graph, repo.group_id, code)
    (OUT / "features").mkdir(parents=True, exist_ok=True)
    (OUT / "features" / f"{inst['instance_id']}.json.gz").write_bytes(gzip.compress(json.dumps(feats).encode()))
    if graph_only:
        return seeds_v2(query, feats, code)
    out = {
        "instance_id": inst["instance_id"], "repo": inst["repo"], "gold": gold, "gold_created": created,
        "gold_indexed": [g for g in gold if g in code], "code_files": len(code),
        "gold_tests": [g for g in gold if classify_file_role(g) is FileRole.TEST],
        "bm25": bm25_scores(query, {p: repo.indexed[p].decode("utf-8", "replace") for p in code}),
        **graph_scores(query, graph, repo.group_id, code),
        **seeds_v2(query, feats, code),
    }
    if embed is not None:
        before = embed.tokens
        out["dense"] = dense_scores(embed, query, graph, code_rids)
        out["embed_tokens"] = embed.tokens - before
    out["seconds"] = {"index": round(index_seconds, 1), "total": round(time.perf_counter() - started, 1)}
    return out


def run(split: str, only: list[str] | None, dense: bool, repos: list[str] | None = None,
        skip_repos: list[str] | None = None) -> None:
    import asyncio

    embed = Embedder() if dense else None
    target = OUT / split
    target.mkdir(parents=True, exist_ok=True)
    for inst in instances(split):
        if (only and inst["instance_id"] not in only) or (repos and inst["repo"] not in repos) \
                or (skip_repos and inst["repo"] in skip_repos):
            continue
        path = target / f"{inst['instance_id']}.json"
        if path.exists():
            existing = json.loads(path.read_text())
            if "seed2" in existing or "error" in existing:
                continue
            # Scored before the mention-weighted seeds existed: add them, keep bm25 and dense.
            existing.update(asyncio.run(localize(inst, None, graph_only=True)))
            path.write_text(json.dumps(existing))
            print(inst["instance_id"], "seed2 added", flush=True)
            continue
        try:
            result = asyncio.run(localize(inst, embed))
        except Exception as exc:  # one bad checkout must not stop 300 issues; it is reported, not hidden
            result = {"instance_id": inst["instance_id"], "repo": inst["repo"], "error": repr(exc)[:500]}
        path.write_text(json.dumps(result))
        print(inst["instance_id"], result.get("seconds"), "error" if "error" in result else "", flush=True)


# -- fusion and metrics ------------------------------------------------------

def ranking(r: dict, base: tuple[str, ...], alpha: float, beta: float) -> list[str]:
    def norm(scores: dict[str, float]) -> dict[str, float]:
        hi = max(scores.values(), default=0.0)
        return {k: v / hi for k, v in scores.items()} if hi > 0 else {}

    total: dict[str, float] = defaultdict(float)
    for name in base:
        for k, v in norm(r.get(name, {})).items():
            total[k] += v / len(base)
    if alpha:
        seed, hop = norm(r.get("seed", {})), norm(r.get("hop", {}))
        for k in set(seed) | set(hop):
            total[k] += alpha * (seed.get(k, 0.0) + beta * hop.get(k, 0.0))
    return [k for k, _ in sorted(total.items(), key=lambda kv: (-kv[1], kv[0]))]


def metrics(results: list[dict], base: tuple[str, ...], alpha: float, beta: float) -> dict:
    out = {}
    for k in (1, 5, 10):
        recall, acc = [], []
        for r in results:
            gold = set(r["gold"])
            if not gold:
                continue
            got = set(ranking(r, base, alpha, beta)[:k])
            recall.append(len(gold & got) / len(gold))
            acc.append(float(gold <= got))
        out[f"recall@{k}"] = round(100 * sum(recall) / len(recall), 1)
        out[f"acc@{k}"] = round(100 * sum(acc) / len(acc), 1)
    return out


def fuse() -> None:
    load = lambda split: [json.loads(p.read_text()) for p in sorted((OUT / split).glob("*.json"))]  # noqa: E731
    dev, test = load("dev"), load("test")
    dev, test = [r for r in dev if "error" not in r], [r for r in test if "error" not in r]
    has_dense = all("dense" in r for r in dev + test)
    bases = [("bm25",)] + ([("dense",), ("bm25", "dense")] if has_dense else [])
    grid = [(a, b) for a in (0.25, 0.5, 1.0, 2.0) for b in (0.0, 0.25, 0.5, 1.0)]
    report = {"dev_issues": len(dev), "test_issues": len(test), "methods": {}}
    for base in bases:
        name = "+".join(base)
        # Chosen on dev only, by Recall@5, then frozen for test.
        alpha, beta = max(grid, key=lambda ab: (metrics(dev, base, *ab)["recall@5"], -ab[0]))
        report["methods"][name] = {"test": metrics(test, base, 0.0, 0.0)}
        report["methods"][name + " + graph"] = {"test": metrics(test, base, alpha, beta),
                                                 "chosen_on_dev": {"alpha": alpha, "beta": beta}}
    report["methods"]["graph only"] = {"test": metrics(test, (), 1.0, 0.5)}
    (OUT / "results.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def fuse2() -> None:
    """B5-G1 retry: mention-weighted seeds, weights tuned on all of Lite, scored once on the held-out set."""
    def load(split: str) -> list[dict]:
        rows = [json.loads(p.read_text()) for p in sorted((OUT / split).glob("*.json"))]
        return [{**r, "seed": r["seed2"], "hop": r["hop2"]} for r in rows if "error" not in r and "seed2" in r]

    tune, held = load("dev") + load("test"), load("fresh")
    grid = [(a, b) for a in (0.1, 0.25, 0.5, 1.0) for b in (0.0, 0.25, 0.5)]
    report = {"tune_issues": len(tune), "heldout_issues": len(held), "methods": {}}
    for base in (("bm25",), ("dense",), ("bm25", "dense")):
        name = "+".join(base)
        alpha, beta = max(grid, key=lambda ab: (metrics(tune, base, *ab)["acc@1"], -ab[0]))
        report["methods"][name] = {"heldout": metrics(held, base, 0.0, 0.0)}
        report["methods"][name + " + graph v2"] = {"heldout": metrics(held, base, alpha, beta),
                                                    "tuned": metrics(tune, base, alpha, beta),
                                                    "chosen_on_lite": {"alpha": alpha, "beta": beta}}
    report["methods"]["graph v2 only"] = {"heldout": metrics(held, (), 1.0, 0.25)}
    (OUT / "results_v2.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run")
    r.add_argument("--split", default="dev")
    r.add_argument("--only", nargs="*")
    r.add_argument("--no-dense", action="store_true")
    r.add_argument("--repos", nargs="*", help="only these repos (owner/name)")
    r.add_argument("--skip-repos", nargs="*", help="all repos but these")
    sub.add_parser("fuse")
    sub.add_parser("fuse2")
    args = ap.parse_args()
    if args.cmd == "run":
        run(args.split, args.only, not args.no_dense, args.repos, args.skip_repos)
    elif args.cmd == "fuse2":
        fuse2()
    else:
        fuse()


if __name__ == "__main__":
    main()
