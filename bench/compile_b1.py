"""B1 comparison tables from the reports: PipesHub before/after the recall fixes and each tool, same truth.

usage: python3 compile_b1.py > ../reports/b1/COMPARISON.md
"""
from __future__ import annotations

import json
from pathlib import Path

BENCH = Path(__file__).resolve().parent
REPORTS = BENCH.parent / "reports"
REPOS = ["requests", "django", "pipeshub-py", "excalidraw", "pipeshub-ts", "gin", "client-go",
         "spring-petclinic", "guava"]
# v2: every tool at its best settings, Java truth without annotations (run_b1_v2.sh).
V2 = REPORTS / "b1-v2"
COLUMNS = [("PipesHub", V2 / "pipeshub"), ("Graphify", V2 / "graphify"),
           ("codebase-memory-mcp", V2 / "cbm"), ("CodeGraphContext", V2 / "cgc")]
TOOL_DIR = {"Graphify": "graphify", "codebase-memory-mcp": "cbm", "CodeGraphContext": "cgc"}
RELS = ["CALLS", "IMPORTS", "IMPORTS_FROM", "HERITAGE"]


def report(base: Path, repo: str) -> dict | None:
    p = base / repo / "report.json"
    return json.loads(p.read_text()) if p.exists() else None


def cell(s: dict | None) -> str:
    if not s or s["tp"] + s["fn"] == 0:
        return "–"
    p, r, f = (100 * (s[k] or 0) for k in ("precision", "recall", "f1"))  # None when the tool emits nothing
    return f"{p:.1f} / {r:.1f} ({f:.1f})"


def missing(name: str, repo: str) -> str:
    tool = TOOL_DIR.get(name)
    if not tool:
        return "–"
    log = BENCH / "work-v2" / tool / f"{repo}.time"
    text = log.read_text() if log.exists() else ""
    return "capped (memory)" if "signal" in text else "tool error" if "status" in text else "–"


def table(title: str, pick) -> list[str]:
    out = [f"### {title}", "", "| repo | " + " | ".join(n for n, _ in COLUMNS) + " |",
           "|---" * (len(COLUMNS) + 1) + "|"]
    for repo in REPOS:
        row = []
        for name, base in COLUMNS:
            r = report(base, repo)
            row.append(cell(pick(r)) if r else missing(name, repo))
        out.append(f"| {repo} | " + " | ".join(row) + " |")
    return out + [""]


def seconds(name: str, repo: str) -> str:
    if name in TOOL_DIR:
        t = BENCH / "work-v2" / TOOL_DIR[name] / f"{repo}.time"
        first = t.read_text().split() if t.exists() else []
        if missing(name, repo) != "–":
            try:
                return f"{missing(name, repo)} after {float(first[-2]):.0f} s, {int(first[-1]) / 1048576:.1f} GB"
            except (IndexError, ValueError):  # stopped by the memory guard, so time wrote no figures
                return missing(name, repo)
        try:
            if name == "codebase-memory-mcp":  # indexing runs in its daemon, which /usr/bin/time does not see
                return f"{float(first[-2]):.1f} s, n/m"
            return f"{float(first[-2]):.1f} s, {int(first[-1]) / 1048576:.1f} GB"
        except (IndexError, ValueError):
            return missing(name, repo)
    r = report(dict(COLUMNS)[name], repo)
    if not r:
        return "–"
    t = r["timings"]
    return f"{t['index_seconds'] + t['edge_build_seconds']:.1f} s, {r['peak_rss_mb'] / 1024:.1f} GB"


def main() -> None:
    lines = ["# B1 comparison: same SCIP truth for every tool", "",
             "Cells are precision / recall (F1), in percent. Only relations in the truth are scored; "
             "a tool that emits no edges of a relation scores 0 recall on it.", ""]
    lines += table("All relations", lambda r: r["edges"]["overall"])
    lines += table("Hand-written code (files PipesHub skips by policy left out)",
                   lambda r: r["handwritten"]["overall"])
    lines += table("Hand-written code without test files (tests left out on both sides)",
                   lambda r: r["without_tests"]["overall"])
    for rel in RELS:
        lines += table(rel, lambda r, rel=rel: r["edges"]["by_relation"].get(rel))
    lines += ["### Time and peak memory", "", "| repo | " + " | ".join(n for n, _ in COLUMNS) + " |",
              "|---" * (len(COLUMNS) + 1) + "|"]
    for repo in REPOS:
        lines.append(f"| {repo} | " + " | ".join(seconds(n, repo) for n, _ in COLUMNS) + " |")
    lines += ["", "Time is wall clock for index plus export (PipesHub: parse and project plus edge build). "
              "n/m: not measured; codebase-memory-mcp indexes in a background daemon."]
    print("\n".join(lines))


if __name__ == "__main__":
    main()
