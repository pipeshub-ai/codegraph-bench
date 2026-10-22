"""B1 for another tool: score its edges (jsonl of rel/from/to/confidence) against a SCIP key.

    python -m cg_eval.score_tool --edges edges.jsonl --key ../bench/truth/requests \
        --pipeshub ../reports/b1-fix/requests/report.json --out ../reports/b1-tools/graphify/requests

The hand-written-code score leaves out the same files PipesHub's run did (by policy),
so both tools are measured on the same edges.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from .real import score_against_truth


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--edges", type=Path, required=True)
    ap.add_argument("--key", type=Path, required=True)
    ap.add_argument("--pipeshub", type=Path, help="PipesHub's report for the same repo")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    edges = [json.loads(line) for line in args.edges.read_text().splitlines() if line.strip()]
    policy = set(json.loads(args.pipeshub.read_text()).get("policy_files", [])) if args.pipeshub else set()
    scores, handwritten = score_against_truth(args.key, edges, policy)
    args.out.mkdir(parents=True, exist_ok=True)
    report = {"edges": scores, "handwritten": {"overall": handwritten["overall"],
                                               "by_relation": handwritten["by_relation"]},
              "without_tests": {"overall": handwritten["without_tests"]["overall"],
                                "by_relation": handwritten["without_tests"]["by_relation"]}}
    (args.out / "report.json").write_text(json.dumps(report, indent=1, sort_keys=True))
    print(json.dumps({"overall": scores["overall"], "by_relation": scores["by_relation"],
                      "handwritten": handwritten["overall"]}, indent=1))


if __name__ == "__main__":
    main()
