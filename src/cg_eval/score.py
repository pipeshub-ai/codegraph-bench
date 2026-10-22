"""Compare what the pipeline produced with the answer key."""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path

HERITAGE = {"INHERITS", "IMPLEMENTS", "EXTENDS"}
FAMILY = {"python": "py", "javascript": "js", "typescript": "js", "tsx": "js"}
# Repo files named like a stdlib or third-party module the code really imports.
SHADOW_NAMES = {"logging.py", "json.py", "requests.py", "debounce.ts", "pricing_native.pyi"}


def load_key(key_dir: Path) -> dict[str, list[dict]]:
    out = {}
    for name in ("files", "edges", "symbols", "canaries", "tool_cases"):
        path = key_dir / f"{name}.jsonl"
        out[name] = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return out


def _norm(ep: str) -> str:
    """Drop the kind: ``a.py::method:C.m`` -> ``a.py::C.m``.

    A Java constructor is ``C.<init>`` in SCIP and ``C.C`` in a parser's naming;
    both become ``C.<init>`` so the same constructor is the same endpoint.
    """
    path, sep, q = ep.partition("::")
    if not sep:
        return path
    q = q.partition(":")[2]
    if path.endswith(".java"):
        parts = q.split(".")
        if len(parts) >= 2 and parts[-1] == parts[-2]:
            q = ".".join(parts[:-1] + ["<init>"])
    return f"{path}::{q}"


def _group(rel: str) -> str:
    return "HERITAGE" if rel in HERITAGE else rel


def _path(ep: str) -> str:
    return ep.partition("::")[0]


def _tail_name(ep: str) -> str | None:
    _, sep, q = ep.partition("::")
    return q.rsplit(".", 1)[-1] if sep else None


def _prf(tp: int, fp: int, fn: int) -> dict:
    p = tp / (tp + fp) if tp + fp else None
    r = tp / (tp + fn) if tp + fn else None
    f = 2 * p * r / (p + r) if p and r else (0.0 if p is not None and r is not None else None)
    return {"tp": tp, "fp": fp, "fn": fn, "precision": p, "recall": r, "f1": f}


def score_edges(key: dict, produced: list[dict]) -> dict:
    files = {f["path"]: f for f in key["files"]}
    gold = {}
    for e in key["edges"]:
        gold[(_group(e["rel"]), _norm(e["from"]), _norm(e["to"]))] = e
    got: dict[tuple, dict] = {}
    for e in produced:
        got.setdefault((_group(e["rel"]), _norm(e["from"]), _norm(e["to"])), e)

    tp = set(gold) & set(got)
    fp = set(got) - set(gold)
    fn = set(gold) - set(got)

    def lang_of(ep: str) -> str | None:
        return (files.get(_path(ep)) or {}).get("lang")

    by_rel = defaultdict(Counter)
    by_lang = defaultdict(Counter)
    by_cat = defaultdict(Counter)
    by_conf = defaultdict(Counter)
    for k in tp:
        by_rel[k[0]]["tp"] += 1
        by_lang[lang_of(k[1])]["tp"] += 1
        by_cat[gold[k]["cat"]]["tp"] += 1
        by_conf[got[k].get("confidence")]["tp"] += 1
    for k in fn:
        by_rel[k[0]]["fn"] += 1
        by_lang[lang_of(k[1])]["fn"] += 1
        by_cat[gold[k]["cat"]]["fn"] += 1
    for k in fp:
        by_rel[k[0]]["fp"] += 1
        by_lang[lang_of(k[1])]["fp"] += 1
        by_conf[got[k].get("confidence")]["fp"] += 1

    # Why each wrong edge is wrong, in the review's terms.
    gold_targets_by_from_name = defaultdict(set)
    for g, src, dst in gold:
        gold_targets_by_from_name[(g, src, _tail_name(dst))].add(dst)
    fp_reasons = Counter()
    fp_rows = []
    for k in sorted(fp):
        g, src, dst = k
        target = files.get(_path(dst)) or {}
        src_lang, dst_lang = lang_of(src), target.get("lang")
        if not target.get("expect_index", True):
            reason = "into-excluded-file"
        elif src_lang and dst_lang and FAMILY.get(src_lang, src_lang) != FAMILY.get(dst_lang, dst_lang):
            reason = "cross-language (R-2)"
        elif _path(dst).rsplit("/", 1)[-1] in SHADOW_NAMES:
            reason = "stdlib/third-party shadow (R-1)"
        elif gold_targets_by_from_name.get((g, src, _tail_name(dst))):
            reason = "right name, wrong definition (R-1)"
        else:
            reason = "unexplained"
        fp_reasons[reason] += 1
        fp_rows.append({"rel": g, "from": src, "to": dst, "reason": reason,
                        "confidence": got[k].get("confidence")})
    fn_rows = [{"rel": k[0], "from": k[1], "to": k[2], "cat": gold[k]["cat"],
                "note": gold[k].get("note", "")} for k in sorted(fn)]

    # Heritage: the right pair with the wrong relation name.
    heritage_type = Counter()
    for k in tp:
        if k[0] == "HERITAGE":
            heritage_type["match" if gold[k]["rel"] == got[k]["rel"] else
                          f"{gold[k]['rel']}->{got[k]['rel']}"] += 1

    def table(counter_map):
        return {str(k): _prf(v["tp"], v["fp"], v["fn"]) for k, v in sorted(counter_map.items(), key=lambda kv: str(kv[0]))}

    return {
        "overall": _prf(len(tp), len(fp), len(fn)),
        "by_relation": table(by_rel),
        "by_language": table(by_lang),
        "recall_by_category": {c: v["tp"] / (v["tp"] + v["fn"]) if v["tp"] + v["fn"] else None
                               for c, v in sorted(by_cat.items())},
        "precision_by_confidence": {str(c): v["tp"] / (v["tp"] + v["fp"]) if v["tp"] + v["fp"] else None
                                    for c, v in sorted(by_conf.items(), key=lambda kv: str(kv[0]))},
        "false_positive_reasons": dict(fp_reasons.most_common()),
        "heritage_relation": dict(heritage_type),
        "false_positives": fp_rows,
        "false_negatives": fn_rows,
    }


def score_symbols(key: dict, blocks_by_path: dict[str, set[str]]) -> dict:
    """Recall of declared symbols among projected block names, per language."""
    by_lang = defaultdict(Counter)
    missing = []
    for s in key["symbols"]:
        tail = s["qualified"].partition(":")[2]
        hit = tail in blocks_by_path.get(s["path"], set())
        by_lang[s["lang"]]["hit" if hit else "miss"] += 1
        if not hit:
            missing.append(f"{s['path']}::{s['qualified']}")
    return {
        "recall_by_language": {str(k): v["hit"] / (v["hit"] + v["miss"]) for k, v in sorted(by_lang.items(), key=lambda kv: str(kv[0]))},
        "missing": missing,
    }


def score_files(key: dict, outcomes: dict, roles: dict[str, str]) -> dict:
    """Did the pipeline index what it should, and skip what it shouldn't?"""
    wrongly_indexed, wrongly_skipped = [], []
    role_hits, role_rows = Counter(), []
    for f in key["files"]:
        out = outcomes.get(f["path"])
        indexed = bool(out and out.route in ("code", "text", "delimited", "structured"))
        if out and out.route == "empty" and f["bytes"] == 0:
            continue
        if indexed and not f["expect_index"]:
            wrongly_indexed.append({"path": f["path"], "role": f["role"], "route": out.route,
                                    "blocks": out.blocks, "tags": f["tags"]})
        if f["expect_index"] and f["lang"] and not indexed:
            wrongly_skipped.append({"path": f["path"], "route": out.route if out else "no-record"})
        if f["lang"] and f["role"] in ("source", "test", "migration", "type_definition", "script",
                                       "generated", "build", "config"):
            got = roles.get(f["path"])
            ok = got == f["role"]
            role_hits["right" if ok else "wrong"] += 1
            if not ok:
                role_rows.append({"path": f["path"], "expected": f["role"], "got": got, "tags": f["tags"]})
    total = role_hits["right"] + role_hits["wrong"]
    return {
        "indexed_but_should_skip": wrongly_indexed,
        "skipped_but_should_index": wrongly_skipped,
        "role_accuracy": role_hits["right"] / total if total else None,
        "role_mismatches": role_rows,
    }


def score_canaries(key: dict, indexed_text: dict[str, str]) -> dict:
    leaks = []
    for c in key["canaries"]:
        for path, text in indexed_text.items():
            if c["value"] in text:
                leaks.append({"kind": c["kind"], "value": c["value"], "found_in": path, "note": c["note"]})
    return {"secret_leaks": [x for x in leaks if x["kind"] == "secret"],
            "injection_text_indexed": [x for x in leaks if x["kind"] == "injection"]}
