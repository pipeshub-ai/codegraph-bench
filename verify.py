"""Compare what a reproduction produced (reports/) with the published numbers (expected/reports/).

  python3 verify.py                 # every benchmark that has produced files
  python3 verify.py b1 b3b          # only these (b1 b2 b2-speed b2-tools b3 b3b b5a b5b contextbench)

Every expected file is matched with the same path under reports/. Numbers are compared leaf by leaf with the
benchmark's tolerance below; a file not produced yet is listed as "not run", not as a failure. Exit status 1
when any produced number is outside its tolerance.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
EXPECTED = ROOT / "expected" / "reports"
PRODUCED = Path(os.environ.get("REPORTS", ROOT / "reports"))  # what a run wrote

# bench dir -> (abs tolerance, rel tolerance, leaf paths to check, leaf paths to skip). Fractions for B1/B3b
# accuracy (0.0005 = 0.05 points), points for B5. Timing only where it is the published number.
RULES = {
    "b1-v2": (0.0005, 0.0, r".", r"timings|peak_rss"),
    "b2": (0.0, 0.0, r"^(graph|files_in_tree|code_files|records)\b", r"^tools"),   # latency in tools.* is noisy: read, not checked
    "b2-speed": (0.0, 0.25, r"^graph_seconds$|^code_files$", r"$^"),
    "b2-tools": (0.0, 0.25, r".", r"$^"),
    "b3": (0.01, 0.0, r"(^|/)(fp|precision|recall)$|/score/overall/(fp|precision|recall)$", r"_s$|_ms$"),
    "b3b": (0.0005, 0.0, r".", r"seconds|_s$"),
    "b5a": (0.5, 0.0, r"acc@|recall@", r"$^"),             # embeddings are an API call: tiny drift
    "b5b": (2.0, 0.0, r"acc@", r"$^"),                      # an LLM agent: run-to-run noise
    "contextbench": (0.01, 0.0, r"final_|num_valid", r"traj"),  # coverage/precision as fractions; agent sets drift more
}


def leaves(obj, path=""):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, f"{path}/{k}" if path else str(k))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, f"{path}[{i}]")
    elif isinstance(obj, (int, float)) and not isinstance(obj, bool):
        yield path, float(obj)


def load(p: Path):
    if p.suffix == ".jsonl":
        rows = [json.loads(line) for line in p.read_text().splitlines() if line.strip()]
        key = next((k for k in ("set", "scenario", "step") if rows and k in rows[0]), None)
        return {str(r.get(key, i)) + (f"#{r['step']}" if key == "scenario" else ""): r for i, r in enumerate(rows)}
    if p.name.endswith(".time"):
        return {"seconds": float(p.read_text().split()[0])}
    return json.loads(p.read_text())


def produced_for(exp: Path) -> Path | None:
    rel = exp.relative_to(EXPECTED)
    if rel.parts[0] == "b2-tools":  # published as one file, produced as <tool>.time
        return PRODUCED / "b2-tools" if (PRODUCED / "b2-tools").exists() else None
    p = PRODUCED / rel
    return p if p.exists() else None


def produced_b2_tools(d: Path) -> dict:
    return {t: float((d / f"{t}.time").read_text().split()[0]) for t in ("graphify", "cbm") if (d / f"{t}.time").exists()}


def main(only: list[str]) -> int:
    bad = ran = 0
    for exp in sorted(EXPECTED.rglob("*")):
        if not exp.is_file():
            continue
        bench = exp.relative_to(EXPECTED).parts[0]
        alias = {"b1-v2": "b1", "contextbench": "b5c"}
        if only and bench not in only and alias.get(bench) not in only:
            continue
        got_path = produced_for(exp)
        name = exp.relative_to(EXPECTED)
        if got_path is None:
            print(f"not run   {name}")
            continue
        got = produced_b2_tools(got_path) if bench == "b2-tools" else load(got_path)
        want = load(exp)
        atol, rtol, inc, exc = RULES[bench]
        g = dict(leaves(got))
        fails, checked = [], 0
        for path, w in leaves(want):
            if not re.search(inc, path) or re.search(exc, path):
                continue
            if path not in g:
                continue  # a field the produced run does not write is not a mismatch of its numbers
            checked += 1
            tol = max(atol, rtol * abs(w))
            if abs(g[path] - w) > tol + 1e-12:
                fails.append(f"    {path}: expected {w:g}, got {g[path]:g} (tolerance {tol:g})")
        ran += 1
        status = "ok" if not fails else "DIFF"
        print(f"{status:<9} {name}  ({checked} numbers)")
        if fails:
            bad += 1
            print("\n".join(fails[:20] + ([f"    ... {len(fails) - 20} more"] if len(fails) > 20 else [])))
    print(f"\n{ran} produced files checked, {bad} outside tolerance")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
