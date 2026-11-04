"""The second round of hand-written medium/tough questions for B11 (VS Code) and B12 (Django 3.2), and the 300-question set.

  python -m cg_eval.more_hand <vscode|django32>   # bench/<b11|b12>/questions/hand2.jsonl and bench/<b11|b12>/questions-300.jsonl

Inputs: questions/<area>2a.jsonl and <area>2b.jsonl (18 drafts each, two writers per area), and verify/<same name>.jsonl,
an independent reviewer's verdict per draft ("ok", "fix" with corrected gold, or "drop"). 65 medium and 65 tough are
kept, in the first round's mix of kinds, taken in turn from each file. questions-300.jsonl = the first 105 (questions.jsonl)
+ easy2.jsonl (more_easy) + hand2.jsonl.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections import Counter
from itertools import zip_longest

from . import b8_questions as b8q

DIRS = {"vscode": "b11", "django32": "b12"}
AREAS = {"vscode": ("display", "editing", "widgets", "platform"), "django32": ("orm", "models", "apps", "web")}
# The first round's mix (35 medium: 22 locate + 13 callers; 35 tough: 12 flow, 10 debug, 8 impact, 5 locate), scaled to 65.
WANT = {"medium": {"locate": 41, "callers": 24}, "tough": {"flow": 22, "debug": 19, "impact": 15, "locate": 9}}


def tests_only_optional(q: dict, v: dict) -> bool:
    """A drop for test callers when the draft already lists them only in `accept` (not penalised either way)."""
    return "test" in v.get("reason", "").lower() and not any(b8q.is_test(s.partition("::")[0]) for s in q["answer"]) \
        and any(b8q.is_test(s.partition("::")[0]) for s in q.get("accept", []))


def load(repo: str) -> list[list[dict]]:
    d = b8q.ROOT / "bench" / DIRS[repo] / "questions"
    files = []
    for area in AREAS[repo]:
        for half in "ab":
            name = f"{area}2{half}.jsonl"
            verdicts = {r["id"]: r for r in map(json.loads, (d / "verify" / name).read_text().splitlines())}
            keep = []
            for q in map(json.loads, (d / name).read_text().splitlines()):
                v = verdicts[q["id"]]  # every draft was reviewed
                if v["verdict"] == "drop" and not tests_only_optional(q, v):
                    continue
                if v["verdict"] == "fix":
                    q.update({k: v[k] for k in ("answer", "accept", "chain") if k in v})
                if q["kind"] == "flow":  # a correct trace names every hop of the chain
                    q["answer"] += [s for s in q.get("chain", []) if s not in q["answer"]]
                q["accept"] = [s for s in q.get("accept", []) if s not in q["answer"]]
                keep.append(q)
            files.append(keep)
    return files


def pick(files: list[list[dict]]) -> list[dict]:
    out = []
    for level, kinds in WANT.items():
        pools = {k: [q for qs in zip_longest(*files) for q in qs if q and q["level"] == level and q["kind"] == k]
                 for k in kinds}
        want = dict(kinds)
        short = sum(max(0, n - len(pools[k])) for k, n in want.items())
        for k in sorted(want, key=lambda k: len(pools[k]) - want[k], reverse=True):  # roomiest kinds absorb a shortfall
            extra = min(short, len(pools[k]) - want[k]) if len(pools[k]) > want[k] else 0
            want[k], short = want[k] + extra, short - extra
        for k, n in want.items():
            want[k] = min(n, len(pools[k]))
            out += pools[k][:want[k]]
        print(level, sum(want.values()), want, file=sys.stderr)
    return out


def main(repo: str) -> None:
    d = b8q.ROOT / "bench" / DIRS[repo]
    hand = pick(load(repo))
    (d / "questions" / "hand2.jsonl").write_text("".join(json.dumps(q) + "\n" for q in hand))
    env = dict(os.environ, B11_REPO=repo)
    check = subprocess.run([sys.executable, "-m", "cg_eval.b11_check", str(d / "questions" / "hand2.jsonl")],
                           env=env, capture_output=True, text=True, cwd=b8q.ROOT / "src")
    print(check.stdout + check.stderr, end="")
    old = (d / "questions.jsonl").read_text()
    easy = (d / "questions" / "easy2.jsonl").read_text()
    allq = [json.loads(line) for line in (old + easy).splitlines()] + hand
    ids = [q["id"] for q in allq]
    assert len(ids) == len(set(ids)), "duplicate ids"
    (d / "questions-300.jsonl").write_text("".join(json.dumps(q) + "\n" for q in allq))
    print(len(allq), dict(Counter(q["level"] for q in allq)))


if __name__ == "__main__":
    main(sys.argv[1])
