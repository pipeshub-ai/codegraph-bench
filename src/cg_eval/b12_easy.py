"""B12 easy questions: the B11 easy mix, generated on the Django 3.2 snapshot from its key (SCIP + runtime edges).

  python -m cg_eval.b12_easy            # bench/b12/questions/easy-candidates.jsonl
  python -m cg_eval.b12_easy freeze     # bench/b12/questions/easy.jsonl: the B11 quota per kind,
                                        # negatives and unreachables only once audited (audit-easy.jsonl)

Same kinds, wording and grep-hardness filters as B10/B11 (`b10_questions`): the target's bare name is defined
by many types and appears often, so a text search has to tell the definitions apart by reading.
"""
from __future__ import annotations

import itertools
import json
import random
import sys
from collections import Counter

from . import b8_questions as b8q
from . import b10_questions as b10q
from .qa_questions import load

REPO = "django32"
OUT = b8q.ROOT / "bench" / "b12" / "questions"
# B11's easy set, kind by kind.
# common3 is 1 (B11: 2) and common 6 (B11: 5): Django yields one clean three-way triple.
QUOTA = {"callers": 5, "negative": 3, "impact3": 5, "impact5": 4, "common": 6, "common3": 1, "path": 5,
         "path8": 3, "unreachable": 3}
# Names Django or Python call by convention or by name in a string, never visibly by that name.
CONVENTION = {"get", "post", "put", "patch", "delete", "head", "options", "handle", "add_arguments", "ready",
              "dispatch", "setup", "check", "process_request", "process_response", "process_view",
              "process_exception", "process_template_response", "as_view", "get_queryset", "get_context_data"}


def main() -> None:
    b8q.REPOS[REPO] = (".py",)
    b10q.CONVENTION = b10q.CONVENTION | CONVENTION
    b10q.MIX.update({"callers": 10, "impact3": 10, "common": 10, "path": 10, "unreachable": 4})
    b10q.HARD_MIX.update({"impact5": 8, "path8": 6, "common3": 4})
    rng = random.Random(12)
    base = b10q.questions(REPO, rng)
    taken = {q["target"] for q in base} | {q.get(k) for q in base for k in ("other", "to") if q.get(k)}
    hard = b10q.hard_questions(REPO, random.Random(120), taken)
    if not any(q["kind"] == "common3" for q in hard):
        hard += common3(taken | {q["target"] for q in hard})
    OUT.mkdir(parents=True, exist_ok=True)
    allq = base + hard
    (OUT / "easy-candidates.jsonl").write_text("".join(json.dumps(q) + "\n" for q in allq))
    print(len(allq), dict(Counter(q["kind"] for q in allq)))


def common3(taken: set[str], want: int = 2) -> list[dict]:
    """B10's three-way search finds none in Django (its grep floor is too high for a fifth the code):
    callers shared by three names defined 4+ times and seen 40+ times, no symbol reused across triples."""
    out, inn = load(REPO)
    kind = b8q.kinds(REPO)
    words, _, _, _ = b8q.scan(REPO)
    name = b8q.name_of
    defs = Counter(name(s) for s, k in kind.items() if k in b8q.CALLABLE)
    pool = sorted(s for s in inn if kind.get(s) in b8q.CALLABLE and not name(s).startswith("_") and s not in taken
                  and defs[name(s)] >= 4 and words[name(s)] >= 40 and len(inn[s]) >= 4 and not b8q.is_test(s))
    random.Random(7).shuffle(pool)
    qs: list[dict] = []
    used: set[str] = set()
    for x, y, z in itertools.combinations(pool[:900], 3):
        if len(qs) >= want:
            break
        if {x, y, z} & used or len({name(x), name(y), name(z)}) < 3:
            continue
        both = inn[x] & inn[y] & inn[z]
        if 2 <= len(both) <= 6:
            used |= {x, y, z}
            qs.append({"kind": "common3", "target": x, "other": y, "third": z, "answer": sorted(both), "hard": True,
                       "question": f"Which functions or methods call all three of `{x}`, `{y}` and `{z}` (each "
                                   "directly)? List them all.",
                       "grep_hits": words[name(x)], "same_name_defs": defs[name(x)],
                       "id": f"{REPO}-common3-c{len(qs):02d}", "repo": REPO})
    return qs


def freeze() -> None:
    cands = [json.loads(line) for line in (OUT / "easy-candidates.jsonl").read_text().splitlines()]
    audit = OUT / "audit-easy.jsonl"
    rows = [json.loads(line) for line in audit.read_text().splitlines()] if audit.exists() else []
    ok = {r["id"] for r in rows if r["verdict"] == "confirmed"}
    # A negative found by the audit itself (the generator's are mostly overrides reached by dispatch).
    cands += [r["add"] for r in rows if r.get("add")]
    keep = []
    for k, n in QUOTA.items():
        sel = [q for q in cands if q["kind"] == k and (k not in ("negative", "unreachable") or q["id"] in ok)]
        if len(sel) < n:
            print(f"{k}: only {len(sel)} of {n}", file=sys.stderr)
        keep += sel[:n]
    for q in keep:
        q.update(level="easy", expected="Compiler-checked: see answer.")
    (OUT / "easy.jsonl").write_text("".join(json.dumps(q) + "\n" for q in keep))
    print(len(keep), dict(Counter(q["kind"] for q in keep)))


if __name__ == "__main__":
    freeze() if sys.argv[1:] == ["freeze"] else main()
