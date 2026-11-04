"""65 more easy questions per repo for B11 (VS Code) and B12 (Django 3.2): the same kinds, wording and filters as
the first 35 (`b10_questions`, `b12_easy`), no target reused from them.

  python -m cg_eval.more_easy <vscode|django32>          # bench/<b11|b12>/questions/easy2-candidates.jsonl
  python -m cg_eval.more_easy <vscode|django32> freeze   # .../easy2.jsonl, negatives and unreachables only
                                                         # once confirmed in audit-easy2.jsonl

Each kind is drawn on its own (one kind at a time), so an earlier kind does not use up the symbols a later one needs.
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter

from . import b8_questions as b8q
from . import b10_questions as b10q
from . import b12_easy as b12e

DIRS = {"vscode": "b11", "django32": "b12"}
# The first 35's mix, scaled to 65.
QUOTA = {"callers": 9, "negative": 6, "impact3": 9, "impact5": 7, "common": 9, "common3": 4, "path": 9, "path8": 6,
         "unreachable": 6}
# A kind the repo cannot fill takes questions of its nearest kind, in this order (Django has few shared callers).
FALLBACK = {"common3": "common", "common": "callers", "impact5": "impact3", "path8": "path", "negative": "callers",
            "unreachable": "path"}
BASE = ("callers", "negative", "impact3", "common", "path", "unreachable")


def out_dir(repo: str):
    return b8q.ROOT / "bench" / DIRS[repo] / "questions"


def main(repo: str) -> None:
    if repo == "django32":
        b8q.REPOS[repo] = (".py",)
        b10q.CONVENTION = b10q.CONVENTION | b12e.CONVENTION
    old = [json.loads(line) for line in (out_dir(repo) / "easy.jsonl").read_text().splitlines()]
    taken = {q[k] for q in old for k in ("target", "other", "third", "to") if q.get(k)}
    seen: set[str] = set(taken)
    keep: list[dict] = []

    def draw(kinds, seed: int) -> None:
        pool: list[dict] = []
        for i, k in enumerate(kinds):  # 2x what is needed: negatives and unreachables are audited, the rest trimmed
            if k in b10q.HARD_MIX:
                b10q.HARD_MIX.update({x: 2 * QUOTA[k] if x == k else 0 for x in b10q.HARD_MIX})
                pool += b10q.hard_questions(repo, random.Random(seed * 10 + i), seen)
            else:
                b10q.MIX.update({x: 2 * QUOTA[k] if x == k else 0 for x in b10q.MIX})
                b10q.NEG_POOL = 30 if k == "negative" else 0
                pool += b10q.questions(repo, random.Random(seed + i))
        for q in pool:  # no symbol asked about twice, across the old set and the new one
            syms = {q[k] for k in ("target", "other", "third", "to") if q.get(k)}
            if not syms & seen:
                seen.update(syms)
                keep.append(q)

    draw(QUOTA, 40)
    if repo == "django32":
        # Django is a fifth of VS Code: kinds still short are drawn again at b12_easy.common3's grep floor.
        b10q.GREP_MIN, b10q.AMBIG_DEFS = 40, 4
        n = Counter(q["kind"] for q in keep)
        draw([k for k in QUOTA if n[k] < 2 * QUOTA[k] and k not in ("negative", "unreachable", "common3")], 60)
        for q in b12e.common3(seen, want=2 * QUOTA["common3"]):
            seen.update(q[k] for k in ("target", "other", "third"))
            keep.append(q)
    for i, q in enumerate(keep):
        q.update(id=f"{repo}-{q['kind']}-e2{i:03d}", repo=repo)
    (out_dir(repo) / "easy2-candidates.jsonl").write_text("".join(json.dumps(q) + "\n" for q in keep))
    print(len(keep), dict(Counter(q["kind"] for q in keep)))


def freeze(repo: str) -> None:
    d = out_dir(repo)
    cands = [json.loads(line) for line in (d / "easy2-candidates.jsonl").read_text().splitlines()]
    audit = d / "audit-easy2.jsonl"
    ok = {r["id"] for r in map(json.loads, audit.read_text().splitlines()) if r["verdict"] == "confirmed"} \
        if audit.exists() else set()
    # verify/easy2-out-*.jsonl: an independent read of each picked question; "drop" removes it, "fix" corrects the gold.
    verdicts = {r["id"]: r for f in sorted((d / "verify").glob("easy2-out-*.jsonl"))
                for r in map(json.loads, f.read_text().splitlines())}
    for q in cands:
        v = verdicts.get(q["id"], {})
        if v.get("verdict") == "fix":
            q.update({k: v[k] for k in ("answer", "accept") if k in v})

    def clean(q: dict) -> bool:  # reviewed and kept, and no test code in the gold or the question
        syms = q["answer"] + [q[k] for k in ("target", "other", "third", "to") if q.get(k)]
        reviewed = q["id"] in ok or verdicts.get(q["id"], {}).get("verdict") in ("ok", "fix")
        return reviewed and not any(b8q.is_test(s.partition("::")[0]) for s in syms)

    usable = {k: [q for q in cands if q["kind"] == k and clean(q)
                  and (k not in ("negative", "unreachable") or q["id"] in ok)] for k in QUOTA}
    want = dict(QUOTA)
    for k, alt in FALLBACK.items():
        short = want[k] - len(usable[k])
        if short > 0:
            print(f"{k}: {len(usable[k])} of {want[k]}, {short} more {alt}", file=sys.stderr)
            want[k] -= short
            want[alt] += short
    for k in want:  # still short after the fallbacks: the kinds with the most spare candidates take the rest
        while want[k] > len(usable[k]):
            alt = max(want, key=lambda x: len(usable[x]) - want[x])
            assert len(usable[alt]) > want[alt], f"{k}: only {len(usable[k])} of {want[k]}, no spare candidates"
            print(f"{k}: 1 more {alt}", file=sys.stderr)
            want[k] -= 1
            want[alt] += 1
    keep = [q for k, n in want.items() for q in usable[k][:n]]
    for q in keep:
        q.update(level="easy", expected="Compiler-checked: see answer.")
    (d / "easy2.jsonl").write_text("".join(json.dumps(q) + "\n" for q in keep))
    print(len(keep), dict(Counter(q["kind"] for q in keep)))


if __name__ == "__main__":
    freeze(sys.argv[1]) if sys.argv[2:] == ["freeze"] else main(sys.argv[1])
