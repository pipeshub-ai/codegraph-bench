"""B4 questions with known answers, drawn from the B1 SCIP truth (compiler-grade, not PipesHub's graph).

  python -m cg_eval.qa_questions   # writes reports/b4/questions.jsonl

Per repo, 25 questions: callers (7), callees (4), impact = callers within two hops (3), a call
path from A to B (3), "nothing calls X" (3, X defined once and named nowhere else), and free-text
call-flow explanations graded by a judge (5). Half the caller/callee targets have a name defined
in several places, so a text search alone cannot tell them apart. Seeded, so reruns ask the same.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict, deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BENCH = ROOT / "bench"
OUT = ROOT / "reports" / "b4"
REPOS = {"requests": "requests", "django": "django", "gin": "gin", "guava": "guava"}
MIX = {"callers": 7, "callees": 4, "impact": 3, "path": 3, "negative": 3, "explain": 5}


def sym(ep: str) -> str:
    """`a.py::method:C.m` -> `a.py::C.m`, the form answers are given and scored in."""
    path, _, q = ep.partition("::")
    return f"{path}::{q.partition(':')[2] or q}" if q else path


def is_test(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return bool(re.search(r"(^|/)(tests?|testing|testlib|guava-tests)/", path)) or name.startswith("test_") \
        or name.endswith(("_test.go", "_test.py", "Test.java", "Tests.java"))


def load(repo: str) -> tuple[dict, dict]:
    out, inn = defaultdict(set), defaultdict(set)
    for line in (BENCH / "truth" / repo / "edges.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e["rel"] == "CALLS" and "::" in e["from"] and "::" in e["to"]:
            a, b = sym(e["from"]), sym(e["to"])
            if a != b:
                out[a].add(b)
                inn[b].add(a)
    return out, inn


def shortest(out: dict, a: str, b: str, limit: int = 4) -> list[str] | None:
    prev, q = {a: None}, deque([a])
    while q:
        x = q.popleft()
        if x == b:
            chain = []
            while x:
                chain.append(x)
                x = prev[x]
            return chain[::-1]
        depth = 0
        y = x
        while prev[y]:
            depth, y = depth + 1, prev[y]
        if depth >= limit:
            continue
        for n in out.get(x, ()):
            if n not in prev:
                prev[n] = x
                q.append(n)
    return None


def questions(repo: str, rng: random.Random) -> list[dict]:
    out, inn = load(repo)
    names = defaultdict(int)
    for s in set(out) | set(inn):
        names[s.rsplit(".", 1)[-1].rsplit("::", 1)[-1]] += 1
    src = [s for s in set(out) | set(inn) if not is_test(s.split("::")[0])]
    common = lambda s: names[s.rsplit(".", 1)[-1].rsplit("::", 1)[-1]] >= 3  # noqa: E731
    qs: list[dict] = []

    def pick(pool: list[str], n: int) -> list[str]:
        """Half with a name defined in several places, half unique; no target twice."""
        pool = sorted(set(pool) - {q["target"] for q in qs if "target" in q})
        rng.shuffle(pool)
        amb = [s for s in pool if common(s)][: n - n // 2]
        uni = [s for s in pool if not common(s)][: n - len(amb)]
        return amb + uni

    for s in pick([s for s in src if 2 <= len(inn[s]) <= 12], MIX["callers"]):
        qs.append({"kind": "callers", "target": s, "answer": sorted(inn[s]),
                   "question": f"Which functions or methods call `{s}`? List every caller."})
    for s in pick([s for s in src if 2 <= len(out[s]) <= 12], MIX["callees"]):
        qs.append({"kind": "callees", "target": s, "answer": sorted(out[s]),
                   "question": f"Which functions or methods in this repository does `{s}` call directly? List every one."})
    for s in pick([s for s in src if 2 <= len(inn[s]) and 4 <= len(inn[s] | {c for p in inn[s] for c in inn[p]}) <= 25],
                  MIX["impact"]):
        two = inn[s] | {c for p in inn[s] for c in inn[p]}
        qs.append({"kind": "impact", "target": s, "answer": sorted(two - {s}),
                   "question": f"If the behaviour of `{s}` changes, which functions are affected: its callers and "
                               f"their callers (two levels up)? List them all."})
    starts = [s for s in src if out.get(s)]
    rng.shuffle(starts)
    for a in starts:
        if sum(q["kind"] == "path" for q in qs) >= MIX["path"]:
            break
        reach = {}
        for b in out[a]:
            for c in out.get(b, ()):
                reach.setdefault(c, None)
                for d in out.get(c, ()):
                    reach.setdefault(d, None)
        far = [t for t in reach if t not in out[a] and not is_test(t.split("::")[0]) and t != a]
        if not far:
            continue
        b = rng.choice(sorted(far))
        chain = shortest(out, a, b)
        if chain and len(chain) >= 3:
            qs.append({"kind": "path", "target": a, "to": b, "answer": chain,
                       "question": f"Does `{a}` reach `{b}` through calls? If so, give one call chain from the "
                                   f"first to the second, in order, starting with `{a}` and ending with `{b}`."})
    words: Counter[str] = Counter()
    for p in (BENCH / "repos" / REPOS[repo]).rglob("*"):
        if p.is_file() and p.suffix in (".py", ".go", ".java") and ".git" not in p.parts:
            words.update(re.findall(r"\w+", p.read_text(errors="ignore")))
    dead = [s for s in src if not inn.get(s) and out.get(s)
            and words[s.rsplit(".", 1)[-1].rsplit("::", 1)[-1]] == 1]
    rng.shuffle(dead)
    for s in dead[: MIX["negative"]]:
        qs.append({"kind": "negative", "target": s, "answer": [],
                   "question": f"Which functions or methods call `{s}`? List every caller (an empty list if none)."})
    for s in pick([s for s in src if 3 <= len(out[s]) <= 10 and sum(len(out.get(c, ())) for c in out[s]) >= 3],
                  MIX["explain"]):
        qs.append({"kind": "explain", "target": s, "answer": sorted(out[s]),
                   "second_level": {c: sorted(out.get(c, ())) for c in sorted(out[s])},
                   "question": f"Explain what `{s}` does, which functions in this repository it calls directly, "
                               f"and what the most important of those do in turn. Be specific."})
    for i, q in enumerate(qs):
        q.update(id=f"{repo}-{q['kind']}-{i:02d}", repo=repo)
    return qs


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(4)
    allq = [q for repo in REPOS for q in questions(repo, rng)]
    (OUT / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in allq))
    counts = defaultdict(int)
    for q in allq:
        counts[(q["repo"], q["kind"])] += 1
    print(len(allq), dict(counts))


if __name__ == "__main__":
    main()
