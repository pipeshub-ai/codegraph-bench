"""B7 v2: grade every arm's answers with two judges from different model families and report.

  python grade_v2.py [--questions questions_v2.jsonl] [--out b7v2]

Reads reports/<out>/r*/<arm>/<id>.json (ask_cc.py and ask.py --out). Per answer:
- key-fact recall, as in grade.py;
- judge A: llm_judge (Claude Sonnet) and judge B: llm_2 (GPT, Azure), the same prompt, each
  1 / 0.5 / 0. Cached in reports/<out>/judge-{a,b}/r*/<arm>/<id>.json.
Writes reports/<out>/graded.jsonl and results.json: per arm accuracy (judge A, judge B, and the
stricter of the two) with a Wilson 95% interval, by type, judge agreement (Cohen's kappa), and
median/mean time, tool calls, tokens and cost where the arm records them.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import grade

HERE = Path(__file__).resolve().parent
MODELS = json.loads(Path(os.environ["MODELS_JSON"]).read_text())


def judge_prompt(q: dict, answer: str) -> str:
    return (
        "Grade an answer to a question about Apache Kafka against a reference.\n\n"
        f"Question: {q['question']}\n\nReference answer: {q['answer']}\n\n"
        f"Key facts the answer should contain: {json.dumps(q['key_facts'])}\n\n"
        f"Answer to grade:\n{answer or '(no answer)'}\n\n"
        "Score 1 if the answer gets the substance right (the key facts, or equivalent wording), 0.5 if it is "
        "partly right (some key facts, or right with a material error), 0 if it is wrong, evasive or empty. "
        "Do not reward length or hedging. Reply with JSON only: "
        '{"score": 1|0.5|0, "missing": [key facts absent], "wrong": [claims that contradict the reference]}'
    )


def parse(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        got = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return got if got.get("score") in (0, 0.5, 1) else None


def judge_b(q: dict, answer: str) -> dict:
    import openai

    cfg = MODELS["llm_2"]["configuration"]
    client = openai.OpenAI(api_key=cfg["apiKey"], base_url=cfg["endpoint"].rstrip("/") + "/openai/v1/",
                           timeout=180, max_retries=4)
    for _ in range(4):
        r = client.chat.completions.create(model=cfg["model"], max_completion_tokens=4000,
                                           messages=[{"role": "user", "content": judge_prompt(q, answer)}])
        got = parse(r.choices[0].message.content)
        if got:
            return got
    return {"score": None, "error": "unparsed judge reply"}


def cached(path: Path, fn) -> dict:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(fn()))
    return json.loads(path.read_text())


def wilson(k: float, n: int) -> list[float]:
    if not n:
        return [0.0, 0.0]
    p, z = k / n, 1.96
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(max(0.0, c - h), 3), round(min(1.0, c + h), 3)]


def kappa(pairs: list[tuple]) -> float | None:
    pairs = [(a, b) for a, b in pairs if a is not None and b is not None]
    if not pairs:
        return None
    n = len(pairs)
    po = sum(a == b for a, b in pairs) / n
    ca, cb = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 3) if pe < 1 else 1.0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="questions_v2.jsonl")
    ap.add_argument("--out", default="b7v2")
    ap.add_argument("--jobs", type=int, default=6)
    args = ap.parse_args()
    root = HERE.parents[1] / "reports" / args.out
    qs = {q["id"]: q for q in map(json.loads, (HERE / args.questions).read_text().splitlines()) if q}
    files = sorted(p for p in root.glob("r*/*/*.json") if not p.name.startswith(".") and "stream" not in p.name)

    def one(f: Path) -> dict:
        r = json.loads(f.read_text())
        q, rel = qs[r["id"]], f.relative_to(root)
        err = {"score": 0, "error": r["error"]} if r.get("error") else None
        ja = cached(root / "judge-a" / rel, lambda: err or grade.judge(q, r["answer"]))
        jb = cached(root / "judge-b" / rel, lambda: err or judge_b(q, r["answer"]))
        recall, missing = grade.fact_recall(r["answer"], q["key_facts"])
        a, b = ja.get("score"), jb.get("score")
        tools = r.get("tools") or []
        return {"id": q["id"], "type": q["type"], "arm": f.parent.name, "repeat": f.parent.parent.name,
                "judge_a": a, "judge_b": b, "strict": min(a, b) if a is not None and b is not None else None,
                "fact_recall": recall, "missing_facts": missing, "seconds": r.get("seconds"),
                "tool_calls": len(tools), "mcp_calls": sum(1 for t in tools if t.startswith("mcp__") or t.startswith("knowledgegraph")),
                "input_tokens": r.get("input_tokens"), "output_tokens": r.get("output_tokens"),
                "cost_usd": r.get("cost_usd"), "cited_sources": sorted({c.get("connector") or "?" for c in r.get("citations") or []}),
                "error": r.get("error")}

    with ThreadPoolExecutor(args.jobs) as ex:
        rows = list(ex.map(one, files))
    (root / "graded.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    def median(vals):
        vals = [v for v in vals if v is not None]
        return round(statistics.median(vals), 1) if vals else None

    def summary(rs: list[dict]) -> dict:
        out = {"n": len(rs)}
        for key in ("judge_a", "judge_b", "strict"):
            right = sum(1 for r in rs if r[key] == 1)
            out[f"acc_{key}"] = round(right / len(rs), 3) if rs else None
            out[f"ci_{key}"] = wilson(right, len(rs))
            out[f"mean_{key}"] = mean([r[key] for r in rs])
        out["fact_recall"] = mean([r["fact_recall"] for r in rs])
        for key in ("seconds", "tool_calls", "mcp_calls", "input_tokens", "output_tokens", "cost_usd"):
            out[f"mean_{key}"] = mean([r[key] for r in rs])
            out[f"median_{key}"] = median([r[key] for r in rs])
        return out

    report = {}
    by_arm = defaultdict(list)
    for r in rows:
        by_arm[r["arm"]].append(r)
    for arm, rs in sorted(by_arm.items()):
        by_type = defaultdict(list)
        for r in rs:
            by_type[r["type"]].append(r)
        report[arm] = {**summary(rs), "by_type": {t: summary(v) for t, v in sorted(by_type.items())}}
    report["judge_agreement"] = {"exact": mean([1.0 if r["judge_a"] == r["judge_b"] else 0.0 for r in rows
                                                if r["judge_a"] is not None and r["judge_b"] is not None]),
                                 "kappa": kappa([(r["judge_a"], r["judge_b"]) for r in rows])}
    (root / "results.json").write_text(json.dumps(report, indent=1))
    print(json.dumps({k: ({m: v[m] for m in ("n", "acc_judge_a", "acc_judge_b", "acc_strict", "mean_seconds",
                                              "mean_input_tokens", "mean_cost_usd")} if "n" in v else v)
                      for k, v in report.items()}, indent=1))


if __name__ == "__main__":
    main()
