"""B10 questions: a large repo and question kinds built so that a text search is expensive and error-prone.

  python -m cg_eval.b10_questions          # reports/b10/candidates.jsonl
  python -m cg_eval.b10_questions freeze   # reports/b10/questions.jsonl, negatives from audit-out-<repo>.jsonl

What B8/B9 showed: the graph saves tokens only when the grep route is long (many hits, many hops), and a
graph call costs ~10-15k tokens of tool schemas, so easy questions favour grep. Here every target's bare
name is defined by many types and appears hundreds of times (the grep route has to tell them apart by
reading), and the kinds need several hops or a set operation:
  callers      callers of `C.m` whose name m many other types define (AMBIG_DEFS+)
  negative     nothing calls `C.m`, although other types' `m` is called all over (audited)
  impact3      every function within three caller levels
  common       functions that call both X and Y (two ambiguous names)
  path         a call chain of 4+ hops
  unreachable  A does not reach B, where A's call tree is large and enters B's file
"""
from __future__ import annotations

import json
import random
import sys
from collections import Counter

from . import b8_questions as b8q
from .b8_questions import CALLABLE, heritage, is_test, kinds, name_of, overrides, scan
from .qa_questions import load, shortest

OUT = b8q.ROOT / "reports" / "b10"
REPOS = {"vscode": (".ts",)}
b8q.REPOS.update(REPOS)  # scan() reads the suffixes from there
MIX = {"callers": 8, "negative": 8, "impact3": 6, "common": 6, "path": 6, "unreachable": 6}
NEG_POOL = 20
GREP_MIN = 100   # occurrences of the bare name in the repo
AMBIG_DEFS = 5   # functions and methods with that name
NEG_GREP_MIN = 40
CONVENTION = b8q.CONVENTION | {"dispose", "fire", "toJSON", "event", "handleEvent", "provideCommands"}
ASK = "Which functions or methods call `{}`? List every caller (an empty list if none)."
REACH = ("Does `{a}` reach `{b}` through calls, at any depth? If so, give one call chain from the first to the "
         "second, in order, starting with `{a}` and ending with `{b}`; if it does not, give an empty list.")


def questions(repo: str, rng: random.Random) -> list[dict]:
    out, inn = load(repo)
    down, kind = heritage(repo), kinds(repo)
    words, strings, _, texts = scan(repo)
    defs = Counter(name_of(s) for s, k in kind.items() if k in CALLABLE)
    hard = lambda s: words[name_of(s)] >= GREP_MIN and defs[name_of(s)] >= AMBIG_DEFS  # noqa: E731
    syms = sorted(s for s in set(out) | set(inn) if kind.get(s) in CALLABLE and not is_test(s.split("::")[0])
                  and "<" not in s and name_of(s) not in CONVENTION and not name_of(s).startswith(("_", "$")))
    rng.shuffle(syms)
    qs: list[dict] = []
    used: set[str] = set()

    def add(k: str, target: str, answer: list[str], question: str, **extra) -> None:
        used.add(target)
        qs.append({"kind": k, "target": target, "answer": answer, "question": question,
                   "grep_hits": words[name_of(target)], "same_name_defs": defs[name_of(target)], **extra})

    def up(s: str, levels: int) -> set[str]:
        seen, layer = set(), {s}
        for _ in range(levels):
            layer = {c for x in layer for c in inn.get(x, ())} - seen - {s}
            seen |= layer
        return seen

    def tree(a: str) -> set[str]:
        seen, todo = {a}, [a]
        while todo:
            for d in out.get(todo.pop(), ()):
                if d not in seen:
                    seen.add(d)
                    todo.append(d)
        return seen

    def full(k: str) -> bool:
        return sum(q["kind"] == k for q in qs) >= (NEG_POOL if k == "negative" else MIX[k])

    for s in syms:
        if full("negative"):
            break
        # Looser than callers (dead methods under shared names are rare): the audit checks values and dispatch.
        if not inn.get(s) and words[name_of(s)] >= NEG_GREP_MIN and defs[name_of(s)] >= 3 \
                and not name_of(s).startswith(("on", "handle", "test")) \
                and strings[name_of(s)] == 0 and not overrides(repo, s, texts, down):
            add("negative", s, [], ASK.format(s))
    for s in syms:
        if full("callers"):
            break
        if s not in used and hard(s) and 3 <= len(inn.get(s, ())) <= 12:
            add("callers", s, sorted(inn[s]), ASK.format(s))
    for s in syms:
        if full("impact3"):
            break
        if s not in used and hard(s) and 8 <= len(up(s, 3)) <= 30 and up(s, 3) - up(s, 2):
            add("impact3", s, sorted(up(s, 3)),
                f"If the behaviour of `{s}` changes, which functions are affected: its callers, their callers, "
                f"and their callers (three levels up)? List them all.")
    pool = [s for s in syms if s not in used and hard(s) and inn.get(s)]
    for i, x in enumerate(pool):
        if full("common"):
            break
        for y in pool[i + 1:]:
            both = inn[x] & inn[y]
            if 2 <= len(both) <= 8 and len(inn[x]) >= 4 and len(inn[y]) >= 4 and name_of(x) != name_of(y) \
                    and x not in used and y not in used:
                add("common", x, sorted(both),
                    f"Which functions or methods call both `{x}` and `{y}` (each directly)? List them all.", other=y)
                used.add(y)
                break
    for a in syms:
        if full("path"):
            break
        if a in used or not out.get(a):
            continue
        layer, seen = set(out[a]), set(out[a]) | {a}
        for _ in range(3):  # four hops out
            layer = {d for c in layer for d in out.get(c, ())} - seen
            seen |= layer
        far = sorted(t for t in layer if not is_test(t.split("::")[0]) and hard(t))
        if far:
            b = rng.choice(far)
            chain = shortest(out, a, b, limit=6)
            if chain and len(chain) >= 5:
                add("path", a, chain, REACH.format(a=a, b=b), to=b)
    # A's call tree is large and enters B's file; no function in the tree shares B's name (B might be an
    # implementation reached through it), so neither the imports nor a grep settle it.
    for a in syms:
        if sum(q["kind"] == "unreachable" for q in qs) >= 2 * MIX["unreachable"]:
            break
        if a in used:
            continue
        t = tree(a)
        if not 80 <= len(t) <= 1500:
            continue
        files, names = {x.partition("::")[0] for x in t}, {name_of(x) for x in t}
        near = sorted(b for b in syms if b not in t and b.partition("::")[0] in files and inn.get(b)
                      and name_of(b) not in names and b not in used and hard(b))
        if near:
            b = rng.choice(near)
            add("unreachable", a, [], REACH.format(a=a, b=b), to=b, tree_size=len(t))
    for i, q in enumerate(qs):
        q.update(id=f"{repo}-{q['kind']}-{i:02d}", repo=repo)
    return qs


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(10)
    allq = [q for repo in REPOS for q in questions(repo, rng)]
    (OUT / "candidates.jsonl").write_text("".join(json.dumps(q) + "\n" for q in allq))
    print(len(allq), dict(Counter((q["repo"], q["kind"]) for q in allq)))


def freeze() -> None:
    """questions.jsonl: the positives plus the audited negatives and unreachables, up to quota."""
    cands = [json.loads(line) for line in (OUT / "candidates.jsonl").read_text().splitlines()]
    ok = {r["id"] for repo in REPOS for r in map(json.loads, (OUT / f"audit-out-{repo}.jsonl").read_text().splitlines())
          if r["verdict"] == "confirmed"}
    keep = []
    for k in MIX:
        sel = [q for q in cands if q["kind"] == k and (k not in ("negative", "unreachable") or q["id"] in ok)]
        keep += sel[: MIX[k]]
    (OUT / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in keep))
    print(len(keep), dict(Counter(q["kind"] for q in keep)))




HARD_OUT = b8q.ROOT / "reports" / "b10-50"
HARD_MIX = {"impact5": 4, "path8": 4, "common3": 3}


def hard_questions(repo: str, rng: random.Random, taken: set[str]) -> list[dict]:
    """Kinds a text search cannot finish in a sane number of steps: five caller levels, chains of 7+
    hops, and the callers shared by three ambiguous names."""
    out, inn = load(repo)
    kind = kinds(repo)
    words, _, _, _ = scan(repo)
    defs = Counter(name_of(s) for s, k in kind.items() if k in CALLABLE)
    hard = lambda s: words[name_of(s)] >= GREP_MIN and defs[name_of(s)] >= AMBIG_DEFS  # noqa: E731
    syms = sorted(s for s in set(out) | set(inn) if kind.get(s) in CALLABLE and not is_test(s.split("::")[0])
                  and "<" not in s and name_of(s) not in CONVENTION and not name_of(s).startswith(("_", "$"))
                  and s not in taken)
    rng.shuffle(syms)
    qs: list[dict] = []
    used: set[str] = set()
    n = lambda k: sum(q["kind"] == k for q in qs)  # noqa: E731

    def add(k: str, target: str, answer: list[str], question: str, **extra) -> None:
        used.add(target)
        qs.append({"kind": k, "target": target, "answer": answer, "question": question, "hard": True,
                   "grep_hits": words[name_of(target)], "same_name_defs": defs[name_of(target)], **extra})

    def up(s: str, levels: int) -> list[set[str]]:
        seen, layer, layers = set(), {s}, []
        for _ in range(levels):
            layer = {c for x in layer for c in inn.get(x, ())} - seen - {s}
            seen |= layer
            layers.append(layer)
        return layers

    for s in syms:
        if n("impact5") >= HARD_MIX["impact5"]:
            break
        layers = up(s, 5)
        allx = set().union(*layers)
        seen_names = {name_of(q["target"]) for q in qs}
        if hard(s) and all(layers) and 25 <= len(allx) <= 60 and name_of(s) not in seen_names \
                and all(len(allx & set(q["answer"])) < len(allx) / 2 for q in qs):
            add("impact5", s, sorted(allx),
                f"If the behaviour of `{s}` changes, which functions are affected: every function that reaches it "
                f"through calls within five levels (its callers, their callers, and so on, five levels up)? List them all.")
    for a in syms:
        if n("path8") >= HARD_MIX["path8"]:
            break
        if a in used or not out.get(a):
            continue
        dist, layer = {a: 0}, [a]
        for d in range(1, 9):
            layer = [y for x in layer for y in out.get(x, ()) if y not in dist]
            for y in layer:
                dist.setdefault(y, d)
        far = sorted(t for t, d in dist.items() if d >= 7 and not is_test(t.split("::")[0]) and hard(t)
                     and t not in used)
        if far:
            b = rng.choice(far)
            chain = shortest(out, a, b, limit=9)
            if chain and len(chain) >= 8:
                add("path8", a, chain, REACH.format(a=a, b=b), to=b, hops=len(chain) - 1)
    pool = [s for s in syms if s not in used and hard(s) and len(inn.get(s, ())) >= 4]
    for i, x in enumerate(pool):
        if n("common3") >= HARD_MIX["common3"]:
            break
        if x in used:
            continue
        for j, y in enumerate(pool[i + 1:], i + 1):
            xy = inn[x] & inn[y]
            if len(xy) < 2 or y in used or name_of(y) == name_of(x):
                continue
            z = next((z for z in pool[j + 1:] if z not in used and len({name_of(x), name_of(y), name_of(z)}) == 3
                      and 2 <= len(xy & inn[z]) <= 6), None)
            if z:
                add("common3", x, sorted(xy & inn[z]),
                    f"Which functions or methods call all three of `{x}`, `{y}` and `{z}` (each directly)? "
                    "List them all.", other=y, third=z)
                used |= {y, z}
                break
    for i, q in enumerate(qs):
        q.update(id=f"{repo}-{q['kind']}-h{i:02d}", repo=repo)
    return qs


def build_hard() -> None:
    """reports/b10-50/questions.jsonl: the 39 audited B10 questions plus the hard ones."""
    base = [json.loads(line) for line in (OUT / "questions.jsonl").read_text().splitlines()]
    taken = {q["target"] for q in base} | {q.get(k) for q in base for k in ("other", "to") if q.get(k)}
    rng = random.Random(50)
    extra = [q for repo in REPOS for q in hard_questions(repo, rng, taken)]
    HARD_OUT.mkdir(parents=True, exist_ok=True)
    (HARD_OUT / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in base + extra))
    print(len(base) + len(extra), dict(Counter(q["kind"] for q in extra)))


if __name__ == "__main__":
    {"freeze": freeze, "hard": build_hard}.get(sys.argv[1] if sys.argv[1:] else "", main)()
