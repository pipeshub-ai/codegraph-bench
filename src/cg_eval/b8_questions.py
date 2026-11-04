"""B8 questions: structural questions that a text search answers badly, drawn from the B1 SCIP truth.

  python -m cg_eval.b8_questions   # writes reports/b8/candidates.jsonl (questions + extra negatives to audit)

Repos are statically typed (Java, Go, TypeScript): in Python the key misses calls on receivers of
unknown type, so "nothing calls X" cannot be checked there (django was tried and dropped).

Per repo, 30 questions: "nothing calls X" (12), callers (6, worded exactly like the negatives, so
"none" is not a safe guess), impact = callers within two hops (5), a call path A -> B of 3+ hops (4)
and every subclass of a class, at any depth (3; Go and excalidraw have next to no inheritance, so
theirs go to callers and impact). Every target's bare name is a common word in the repo (GREP_MIN
hits or more), so grepping the name returns many lines that are not the answer. Negatives are
oversampled (NEG_POOL per repo) and audited before the set is frozen (questions.jsonl): the key
misses calls made through an interface, a base class, a callback or the runtime, and the filters
below only remove the ones visible in the text.
"""
from __future__ import annotations

import json
import random
import re
from collections import Counter, defaultdict
from pathlib import Path

from .qa_questions import BENCH, is_test as qa_is_test, load, shortest, sym

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "reports" / "b8"
REPOS = {"guava": (".java",), "client-go": (".go",), "excalidraw": (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")}
MIX = {"negative": 9, "unreachable": 3, "callers": 6, "impact": 5, "path": 4, "subclasses": 3}
GO_MIX = {"negative": 9, "unreachable": 3, "callers": 7, "impact": 7, "path": 4}
GREP_MIN = {"guava": 25, "client-go": 25, "excalidraw": 12}  # excalidraw is a fifth the size
NEG_POOL = 18
# Go standard-library interface methods: a type with one is called through the interface, never by name.
GO_STD = {"String", "Error", "RoundTrip", "Read", "Write", "Close", "ServeHTTP", "Len", "Less", "Swap", "Is", "As",
          "Unwrap", "Format", "MarshalJSON", "UnmarshalJSON", "GoString", "Dial", "DialContext", "Reset", "Cancel",
          "Timeout", "Temporary", "Header", "Flush", "Hijack", "Deadline", "Done", "Err", "Value", "Lock", "Unlock"}
# client-go's generated code: thousands of near-identical methods, not what anyone asks about.
GENERATED = re.compile(r"^(applyconfigurations|kubernetes/typed|kubernetes/fake|listers|informers|gentype)/")
# Names the JDK, Go or a framework calls by convention, never visibly in the repo.
CONVENTION = {"equals", "hashCode", "toString", "compareTo", "iterator", "next", "hasNext", "remove", "get",
              "size", "apply", "run", "call", "close", "accept", "test", "compare", "main", "init", "spliterator",
              "comparator", "forEach", "writeObject", "readObject", "readResolve", "writeReplace", "readObjectNoData",
              "render", "constructor", "componentDidMount", "componentDidUpdate", "componentWillUnmount"}
CALLABLE = ("function", "method")


def is_test(path: str) -> bool:
    return qa_is_test(path) or bool(re.search(r"\.(test|spec)\.[jt]sx?$|(^|/)(__tests__|tests)/", path))


def kinds(repo: str) -> dict[str, str]:
    """symbol -> its kind in the key (function, method, class, field)."""
    out = {}
    for line in (BENCH / "truth" / repo / "edges.jsonl").read_text().splitlines():
        e = json.loads(line)
        for ep in (e["from"], e["to"]):
            q = ep.partition("::")[2]
            if q:
                out[sym(ep)] = q.partition(":")[0]
    return out


def uncomment(text: str) -> str:
    return re.sub(r"/\*.*?\*/|//[^\n]*", " ", text, flags=re.S)


def only_calls_and_defs(n: str, texts: dict) -> bool:
    """Every mention of n is a call or a definition: not passed as a value, imported, or referenced."""
    for text in texts.values():
        for m in re.finditer(rf"\b{re.escape(n)}\b", text):
            after = text[m.end():m.end() + 80].lstrip()
            line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.end())]
            if after.startswith("(") or re.match(r"<[^()\n]*>\s*\(", after):
                continue
            if re.search(rf"\b(function|func|const|let|var|async)\s+(\([^)]*\)\s*)?{re.escape(n)}\b", line) \
                    or re.match(rf"\s*(public|private|protected|static|readonly|async|\s)*{re.escape(n)}\s*(=|:)\s*(async\s*)?\(", line):
                continue
            return False
    return True


def name_of(s: str) -> str:
    return s.rsplit("::", 1)[-1].rsplit(".", 1)[-1]


def heritage(repo: str) -> dict[str, set[str]]:
    """class -> its direct subclasses (Java extends/implements, Python bases)."""
    down = defaultdict(set)
    for line in (BENCH / "truth" / repo / "edges.jsonl").read_text().splitlines():
        e = json.loads(line)
        if e["rel"] == "INHERITS":
            down[sym(e["to"])].add(sym(e["from"]))
    return down


def closure(down: dict, c: str) -> set[str]:
    seen, todo = set(), [c]
    while todo:
        for s in down.get(todo.pop(), ()):
            if s not in seen:
                seen.add(s)
                todo.append(s)
    return seen


def scan(repo: str) -> tuple[Counter, Counter, set[str], dict[str, str]]:
    """Word counts, words seen inside string literals, Go interface method names, comment-free file texts."""
    words, strings, iface, texts = Counter(), Counter(), set(), {}
    for p in (BENCH / "repos" / repo).rglob("*"):
        if not p.is_file() or p.suffix not in REPOS[repo]:
            continue
        raw = p.read_text(errors="ignore")
        text = uncomment(raw)
        texts[p.relative_to(BENCH / "repos" / repo).as_posix()] = text
        words.update(re.findall(r"\w+", raw))
        # A name as a whole string literal: reflection, event or command names.
        strings.update(re.findall(r"[\"'`](\w+)[\"'`]", text))
        if p.suffix == ".go":
            for block in re.findall(r"interface\s*\{(.*?)\n\}", text, re.S):
                iface.update(re.findall(r"^\s*(\w+)\s*\(", block, re.M))
    return words, strings, iface, texts


def overrides(repo: str, s: str, texts: dict, down: dict) -> bool:
    """Is s a method some other type declares too (so callers may reach it through that type)?"""
    path, _, q = s.partition("::")
    if "." not in q:
        return False
    cls, n = f"{path}::{q.rsplit('.', 1)[0]}", name_of(s)
    if repo == "guava":
        src = texts.get(path, "")
        ann = r"(?:@\w+(?:\([^)]*\))?\s+)*"
        if re.search(rf"{ann}@Override\s+{ann}[\w<>\[\],.? ]*\b{re.escape(n)}\s*\(", src):
            return True
    up = {a for a, subs in down.items() if cls in closure(down, a)}  # every ancestor in the repo
    related = up | closure(down, cls)
    return any(re.search(rf"[\w>\]]\s+{re.escape(n)}\s*\(|^\s*{re.escape(n)}\s*[(<]", texts.get(c.partition("::")[0], ""), re.M)
               for c in related)


def questions(repo: str, rng: random.Random) -> list[dict]:
    out, inn = load(repo)
    down = heritage(repo)
    kind = kinds(repo)
    words, strings, iface, texts = scan(repo)
    hits = lambda s: words[name_of(s)]  # noqa: E731
    syms = sorted(s for s in set(out) | set(inn) if "::" in s and kind.get(s) in CALLABLE
                  and not is_test(s.split("::")[0]) and not GENERATED.match(s) and "<" not in s)
    rng.shuffle(syms)
    mix = MIX if repo == "guava" else GO_MIX
    qs: list[dict] = []
    used: set[str] = set()

    def add(kind: str, target: str, answer: list[str], question: str, **extra) -> None:
        used.add(target)
        qs.append({"kind": kind, "target": target, "answer": answer, "question": question,
                   "grep_hits": hits(target), **extra})

    ask = "Which functions or methods call `{}`? List every caller (an empty list if none)."
    def go_safe(s: str) -> bool:
        """A Go method named in an interface (the repo's or the standard library's) is called through it."""
        n = name_of(s)
        return "." not in s.partition("::")[2] or (n not in iface and n not in GO_STD and not n.startswith("DeepCopy"))

    grep_min = GREP_MIN[repo]
    neg = [s for s in syms if not inn.get(s) and hits(s) >= grep_min
           and name_of(s) not in CONVENTION and not name_of(s).startswith(("test", "Test", "on", "handle"))
           and strings[name_of(s)] == 0 and (repo != "client-go" or go_safe(s))
           and not (repo == "excalidraw" and name_of(s)[0].isupper())  # components, rendered not called
           and not overrides(repo, s, texts, down) and only_calls_and_defs(name_of(s), texts)]
    for s in neg[:NEG_POOL]:
        add("negative", s, [], ask.format(s))
    # Too few dead functions (Go and TS reach most through interfaces and callbacks): unreachable fills up.
    unreachable_pool = 2 * (mix["negative"] + mix["unreachable"] - min(len(neg), mix["negative"]))
    for s in [s for s in syms if 2 <= len(inn.get(s, ())) <= 10 and hits(s) >= grep_min and s not in used][: mix["callers"]]:
        add("callers", s, sorted(inn[s]), ask.format(s))
    two = lambda s: inn[s] | {c for p in inn[s] for c in inn.get(p, ())}  # noqa: E731
    for s in [s for s in syms if s not in used and inn.get(s) and 5 <= len(two(s) - {s}) <= 25
              and two(s) - inn[s] - {s} and hits(s) >= grep_min // 2][: mix["impact"]]:
        add("impact", s, sorted(two(s) - {s}),
            f"If the behaviour of `{s}` changes, which functions are affected: its callers and their callers "
            f"(two levels up)? List them all.")
    reach_q = ("Does `{a}` reach `{b}` through calls, at any depth? If so, give one call chain from the first to "
               "the second, in order, starting with `{a}` and ending with `{b}`; if it does not, give an empty list.")
    for a in syms:
        if sum(q["kind"] == "path" for q in qs) >= mix["path"]:
            break
        if a in used or not out.get(a):
            continue
        layer, seen = set(out[a]), set(out[a]) | {a}
        for _ in range(2):  # three hops out
            layer = {d for c in layer for d in out.get(c, ())} - seen
            seen |= layer
        far = sorted(t for t in layer if not is_test(t.split("::")[0]) and not GENERATED.match(t))
        if far:
            b = rng.choice(far)
            chain = shortest(out, a, b)
            if chain and len(chain) >= 4:
                add("path", a, chain, reach_q.format(a=a, b=b), to=b)
    # Unreachable: B sits in a file A's call tree enters and is itself called from somewhere, so neither
    # the imports nor a grep settle it; no in-repo function along A's tree shares B's name (an interface
    # method B might implement).
    for a in syms:
        if sum(q["kind"] == "unreachable" for q in qs) >= unreachable_pool:
            break
        if a in used:
            continue
        seen, todo = {a}, [a]
        while todo:
            for d in out.get(todo.pop(), ()):
                if d not in seen:
                    seen.add(d)
                    todo.append(d)
        if not 15 <= len(seen) <= 400 or any(name_of(t) in iface for t in seen):  # Go dispatch could reach B
            continue
        files, names = {t.partition("::")[0] for t in seen}, {name_of(t) for t in seen}
        near = sorted(b for b in syms if b not in seen and b.partition("::")[0] in files and inn.get(b)
                      and name_of(b) not in names and b not in used)
        if near:
            b = rng.choice(near)
            add("unreachable", a, [], reach_q.format(a=a, b=b), to=b)
    classes = [c for c in sorted(down) if not is_test(c.split("::")[0]) and words[name_of(c)] >= grep_min
               and kind.get(c) == "class"
               and 4 <= len(closure(down, c)) <= 20 and any(down.get(s) for s in down[c])]
    rng.shuffle(classes)
    for c in classes[: mix.get("subclasses", 0)]:
        add("subclasses", c, sorted(closure(down, c)),
            f"Which classes in this repository extend or implement `{c}`, directly or indirectly "
            f"(subclasses of subclasses included)? List them all.")
    for i, q in enumerate(qs):
        q.update(id=f"{repo}-{q['kind']}-{i:02d}", repo=repo)
    return qs


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rng = random.Random(8)
    allq = [q for repo in REPOS for q in questions(repo, rng)]
    (OUT / "candidates.jsonl").write_text("".join(json.dumps(q) + "\n" for q in allq))
    print(len(allq), dict(Counter((q["repo"], q["kind"]) for q in allq)))




def freeze() -> None:
    """questions.jsonl: the positives plus the audited negatives (audit-out-<repo>.jsonl), in id order,
    up to the negative quota per repo; nothing calls X first, unreachable fills up."""
    cands = [json.loads(line) for line in (OUT / "candidates.jsonl").read_text().splitlines()]
    ok = {r["id"] for repo in REPOS for r in map(json.loads, (OUT / f"audit-out-{repo}.jsonl").read_text().splitlines())
          if r["verdict"] == "confirmed"}
    keep = []
    for repo in REPOS:
        mix = MIX if repo == "guava" else GO_MIX
        quota = mix["negative"] + mix["unreachable"]
        neg = [q for q in cands if q["repo"] == repo and q["kind"] == "negative" and q["id"] in ok][: mix["negative"]]
        unr = [q for q in cands if q["repo"] == repo and q["kind"] == "unreachable" and q["id"] in ok]
        keep += neg + unr[: quota - len(neg)]
        keep += [q for q in cands if q["repo"] == repo and q["kind"] not in ("negative", "unreachable")]
    (OUT / "questions.jsonl").write_text("".join(json.dumps(q) + "\n" for q in keep))
    print(len(keep), dict(Counter((q["repo"], q["kind"]) for q in keep)))


if __name__ == "__main__":
    import sys

    freeze() if sys.argv[1:] == ["freeze"] else main()
