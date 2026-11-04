"""SCIP call graph of the VS Code snapshot, for writing B11 gold answers.

  python -m cg_eval.b11_truth find <name-or-substring>     # symbols whose name contains it
  python -m cg_eval.b11_truth callers <path::Type.method>  # direct SCIP callers
  python -m cg_eval.b11_truth callees <path::Type.method>
  python -m cg_eval.b11_truth impact <path::Type.method> <depth>   # transitive callers, by level
  python -m cg_eval.b11_truth path <from> <to>             # a shortest CALLS chain, or none

Names are `path::Type.method` without a kind prefix, as answers are written.
"""
from __future__ import annotations

import sys
from collections import defaultdict, deque

from .b11_check import truth


def _graphs() -> tuple[dict[str, set[str]], dict[str, set[str]]]:
    out, inc = defaultdict(set), defaultdict(set)
    for a, b in truth()[1]:
        out[a].add(b)
        inc[b].add(a)
    return out, inc


def main() -> None:
    cmd, *args = sys.argv[1:]
    out, inc = _graphs()
    if cmd == "find":
        needle = args[0].lower()
        for s in sorted(s for s in truth()[0] if needle in s.partition("::")[2].lower())[:200]:
            print(s, f"(callers {len(inc.get(s, ()))}, callees {len(out.get(s, ()))})")
    elif cmd in ("callers", "callees"):
        for s in sorted((inc if cmd == "callers" else out).get(args[0], ())):
            print(s)
    elif cmd == "impact":
        seen, frontier = {args[0]}, {args[0]}
        for level in range(1, int(args[1]) + 1):
            frontier = {c for s in frontier for c in inc.get(s, ())} - seen
            seen |= frontier
            for s in sorted(frontier):
                print(level, s)
    elif cmd == "path":
        src, dst = args
        prev, queue = {src: None}, deque([src])
        while queue:
            s = queue.popleft()
            if s == dst:
                chain = []
                while s is not None:
                    chain.append(s)
                    s = prev[s]
                print("\n".join(reversed(chain)))
                return
            for n in out.get(s, ()):
                if n not in prev:
                    prev[n] = s
                    queue.append(n)
        print("no path")
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
