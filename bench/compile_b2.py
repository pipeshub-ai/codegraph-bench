"""B2 tables from reports/b2: time per stage, graph and store size, cost drivers, tool latency.

usage: python3 compile_b2.py > ../reports/b2/SUMMARY.md
"""
from __future__ import annotations

import json
import re
from pathlib import Path

REPORTS = Path(__file__).resolve().parent.parent / "reports" / "b2"
REPOS = ["spring-petclinic", "requests", "gin", "excalidraw", "pipeshub-ts", "client-go", "guava",
         "pipeshub-py", "django", "home-assistant"]
# Public list prices per million tokens, for scale only: the default local embedding model costs nothing.
EMBED_USD_PER_M = 0.02   # OpenAI text-embedding-3-small
LLM_USD_PER_M = 0.15     # input, a small hosted model (gpt-4o-mini class)


def neo4j_peak_mib(name: str) -> float:
    peak = 0.0
    for line in (REPORTS / name / "neo4j_mem.txt").read_text().splitlines():
        m = re.match(r"([\d.]+)(GiB|MiB)", line.strip())
        if m:
            peak = max(peak, float(m[1]) * (1024 if m[2] == "GiB" else 1))
    return peak


def store_mb(name: str) -> float:
    first = (REPORTS / name / "store_kb.txt").read_text().split()
    return int(first[0]) / 1024


def main() -> None:
    rows = {n: json.loads((REPORTS / n / "report.json").read_text()) for n in REPOS
            if (REPORTS / n / "report.json").exists()}
    out = ["# B2: scale and cost on Neo4j", "",
           "Neo4j 5.26 with the shipped settings (2 GB heap, 1 GB page cache, 3 GB / 4 CPU limit), fresh per repo. "
           "Graph time = plan + records + parse + block projection + edge build.", "",
           "### Time per stage (seconds)", "",
           "| repo | files | code files | records | parse | projection | edge build | graph total |",
           "|---|---|---|---|---|---|---|---|"]
    for n, d in rows.items():
        t = d["timings"]
        out.append(f"| {n} | {d['files_in_tree']:,} | {d['code_files']:,} | {t['records_seconds']:.1f} | "
                   f"{t['parse_seconds']:.1f} | {t['project_seconds']:.1f} | {t['edge_build_seconds']:.1f} | "
                   f"{d['graph_seconds']:.1f} |")
    out += ["", "### Graph and store", "",
            "| repo | blocks | code-graph edges | Neo4j nodes | Neo4j relationships | store MB | Neo4j peak GiB |",
            "|---|---|---|---|---|---|---|"]
    for n, d in rows.items():
        g = d["graph"]
        out.append(f"| {n} | {d['builder']['blocks']:,} | {g['code_graph_edges']:,} | {g['nodes']:,} | "
                   f"{g['relationships']:,} | {store_mb(n):.0f} | {neo4j_peak_mib(n) / 1024:.2f} |")
    out += ["", "Store MB is the on-disk store after a checkpoint. It moves in large steps (inferred: Neo4j grows "
            "its store files in chunks), so every small graph reads about 5 MB.", "",
            "### Cost drivers", "",
            f"Dollars at list prices, for scale: embeddings ${EMBED_USD_PER_M}/M tokens, LLM input ${LLM_USD_PER_M}/M. "
            "The default local embedding model costs nothing. LLM input is estimated for a 128k-context model; "
            "output tokens are not counted.", "",
            "| repo | embedding units | embedding tokens | embed $ | LLM calls | LLM skipped | LLM input tokens | LLM $ |",
            "|---|---|---|---|---|---|---|---|"]
    for n, d in rows.items():
        skipped = sum(d["llm_skipped"].values())
        out.append(f"| {n} | {d['embed_documents']:,} | {d['embed_tokens']:,} | "
                   f"{d['embed_tokens'] / 1e6 * EMBED_USD_PER_M:.2f} | {d['llm_calls']:,} | {skipped:,} | "
                   f"{d['llm_input_tokens']:,} | {d['llm_input_tokens'] / 1e6 * LLM_USD_PER_M:.2f} |")
    out += ["", "### Agent tool latency, 50 symbols each (p50 / p95 ms)", "",
            "| repo | get_neighbour | impact | find_symbol_path |", "|---|---|---|---|"]
    for n, d in rows.items():
        cells = [f"{d['tools'][k]['p50_ms']} / {d['tools'][k]['p95_ms']}"
                 for k in ("get_neighbour", "impact", "find_symbol_path")]
        out.append(f"| {n} | " + " | ".join(cells) + " |")
    out += ["", "### Same graph as B1", "",
            "Edges read back from Neo4j, scored against the B1 truth (precision / recall). The other eight projects "
            "matched B1 exactly in the first B2 pass (same code, before the store-size and LLM-count additions); "
            "home-assistant has no truth. Timings carry about 20% run-to-run noise on this shared machine "
            "(pipeshub-py: 278 s and 335 s).", "",
            "| repo | Neo4j | B1 in memory |", "|---|---|---|"]
    for n, d in rows.items():
        b1 = REPORTS.parent / "b1-fix" / n / "report.json"
        if d.get("accuracy") and b1.exists():
            a, b = d["accuracy"]["overall"], json.loads(b1.read_text())["edges"]["overall"]
            out.append(f"| {n} | {100 * a['precision']:.1f} / {100 * a['recall']:.1f} | "
                       f"{100 * b['precision']:.1f} / {100 * b['recall']:.1f} |")
    print("\n".join(out))


if __name__ == "__main__":
    main()
