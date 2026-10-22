"""Re-score B3b from the edges each run saved (reports/b3b/edges), with the current scorer.

  python rescore.py   # writes reports/b3b/summary.json
"""
import json
import re
from pathlib import Path

from incremental import OUT, score

rows = {}
for f in sorted((OUT / "edges").glob("*.jsonl")):
    tool, mode, step = re.match(r"(\w+)-(full|incremental|fresh)-step(\d)\.jsonl", f.name).groups()
    edges = [json.loads(line) for line in f.read_text().splitlines() if line.strip()]
    rows.setdefault(tool, {}).setdefault(int(step), {})[mode] = score(int(step), edges, f"{tool}-{mode}")
(OUT / "summary.json").write_text(json.dumps(rows, indent=1))
print("rescored", {t: len(s) for t, s in rows.items()})
