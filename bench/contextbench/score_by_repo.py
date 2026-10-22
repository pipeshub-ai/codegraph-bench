"""Official ContextBench scoring, one repository at a time so its checkouts can be removed before the next.

The scorer checks out each task's repo at its commit and keeps every checkout; 500 tasks would hold tens
of GB. Per repo: every prediction set through contextbench.evaluate, then that repo's worktrees and base
clone are deleted. The per-instance rows are combined and aggregated by the scorer's own
aggregate_results (a micro-average of sums, so per-repo runs combine exactly).
"""
import json, os, shutil, subprocess, sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from cb import OUT, tasks  # noqa: E402

BENCH = HERE / "ContextBench" / "Context-Bench"
PY311 = HERE / ".venv311" / "bin" / "python"
WORK = OUT / "scored-by-repo"
TMP = Path(os.environ.get("CB_SCORE_TMP", HERE / "score-tmp"))


def main() -> None:
    tiers = os.environ.get("CB_SCORE_TIERS", "retrieval,agent").split(",")  # e.g. only "retrieval" after a rerun
    sets = [p for tier in tiers for p in sorted((OUT / tier).glob("*")) if p.is_dir()]
    by_repo = defaultdict(list)
    for t in tasks():
        by_repo[t["repo_url"]].append(t["instance_id"])
    WORK.mkdir(parents=True, exist_ok=True)
    for n, (repo, ids) in enumerate(sorted(by_repo.items()), 1):
        idset = set(ids)
        for preds in sets:
            name = f"{preds.parent.name}-{preds.name}"
            out = WORK / f"{name}.{n:03d}.jsonl"
            if out.exists():
                continue
            rows = [json.loads(f.read_text()) for f in sorted(preds.glob("*.json")) if f.stem in idset]
            rows = [r for r in rows if "error" not in r]
            inp = WORK / f"{name}.{n:03d}.pred.jsonl"
            inp.write_text("".join(json.dumps(r) + "\n" for r in rows))
            subprocess.run([str(PY311), "-m", "contextbench.evaluate",
                            "--gold", str(BENCH / "data" / "contextbench_verified.parquet"), "--pred", str(inp),
                            "--cache", str(TMP / "cache"), "--out", str(out)],
                           cwd=BENCH, check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           env={**os.environ, "CONTEXTBENCH_TMP_ROOT": str(TMP / "wt")})
        shutil.rmtree(TMP, ignore_errors=True)
        print(f"[{n}/{len(by_repo)}] {repo}: {len(ids)} tasks", flush=True)
    # combine and aggregate with the scorer's own function
    agg_code = ("import json,sys,glob; from contextbench.evaluate import aggregate_results\n"
                "for name in sys.argv[1:]:\n"
                "    rs=[json.loads(l) for f in sorted(glob.glob(f'%s/{name}.*.jsonl')) if not f.endswith('.pred.jsonl') for l in open(f)]\n"
                "    print(json.dumps({'set': name, **aggregate_results(rs)}))\n" % WORK)
    names = [f"{p.parent.name}-{p.name}" for p in sets]
    res = subprocess.run([str(PY311), "-c", agg_code, *names], cwd=BENCH, capture_output=True, text=True)
    (OUT / "contextbench_results.jsonl").write_text(res.stdout)
    print(res.stdout or res.stderr[-2000:])


if __name__ == "__main__":
    main()
