"""B7: grade both phases' answers and report the shift from code only to code + company data.

  python grade.py            # grades what is there (judge results cached), prints the report

Per answer:
- key-fact recall: the question's checked facts (KIP-N, KAFKA-N, config names, versions, phrases)
  found in the answer, case- and space-insensitive; a numeric fact also matches its value
  (`2 * 60 * 1000L` matches 120000). Facts of one or two characters are left to the judge.
- judge: llm_judge (the same judge as B4) scores the answer against the reference answer and key
  facts as 1 (correct), 0.5 (partly) or 0 (wrong or no answer), listing what is missing or wrong.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "reports" / "b7"
PHASES = ("code-only", "with-company", "graphify")
MODELS = Path(os.environ["MODELS_JSON"])


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("`", "").lower()).strip()


def numeric(fact: str) -> float | None:
    """A fact written as a number or an arithmetic expression of numbers, else None."""
    expr = re.sub(r"(?<=\d)[lLfFdD]\b", "", fact.replace("_", ""))
    if not re.fullmatch(r"[\d\s.*+/()-]+", expr) or not re.search(r"\d", expr):
        return None
    try:
        return float(eval(expr, {"__builtins__": {}}))  # digits and operators only, checked above
    except (SyntaxError, ZeroDivisionError, TypeError):
        return None


def numbers_in(text: str) -> set[float]:
    found = set()
    for m in re.finditer(r"\d[\d,_]*(?:\.\d+)?", text):
        try:
            found.add(float(m.group(0).replace(",", "").replace("_", "")))
        except ValueError:
            pass
    return found


def fact_recall(answer: str, facts: list[str]) -> tuple[float | None, list[str]]:
    text, nums = norm(answer), numbers_in(answer)
    checked, missing = 0, []
    for fact in facts:
        if len(fact.strip()) <= 2:
            continue
        checked += 1
        value = numeric(fact)
        if norm(fact) in text or (value is not None and value in nums):
            continue
        missing.append(fact)
    return ((checked - len(missing)) / checked if checked else None), missing


def judge(q: dict, answer: str) -> dict:
    import anthropic

    cfg = json.loads(MODELS.read_text())["llm_judge"]["configuration"]
    client = anthropic.Anthropic(api_key=cfg["apiKey"], timeout=120, max_retries=4)
    prompt = (
        "Grade an answer to a question about Apache Kafka against a reference.\n\n"
        f"Question: {q['question']}\n\nReference answer: {q['answer']}\n\n"
        f"Key facts the answer should contain: {json.dumps(q['key_facts'])}\n\n"
        f"Answer to grade:\n{answer or '(no answer)'}\n\n"
        "Score 1 if the answer gets the substance right (the key facts, or equivalent wording), 0.5 if it is "
        "partly right (some key facts, or right with a material error), 0 if it is wrong, evasive or empty. "
        "Do not reward length or hedging. Reply with JSON only: "
        '{"score": 1|0.5|0, "missing": [key facts absent], "wrong": [claims that contradict the reference]}'
    )
    for _ in range(4):
        msg = client.messages.create(model=cfg["model"], max_tokens=4000,  # room for a thinking block
                                     messages=[{"role": "user", "content": prompt}])
        text = "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            try:
                got = json.loads(m.group(0))
                if got.get("score") in (0, 0.5, 1):
                    return got
            except json.JSONDecodeError:
                pass
    return {"score": None, "error": "unparsed judge reply"}


def main() -> None:
    qs = {q["id"]: q for q in map(json.loads, (HERE / "questions.jsonl").read_text().splitlines()) if q}
    rows = []
    for phase in PHASES:
        for f in sorted((OUT / phase).glob("*.json")) if (OUT / phase).exists() else []:
            r = json.loads(f.read_text())
            q = qs[r["id"]]
            cache = OUT / "judge" / phase / f.name
            if not cache.exists():
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(judge(q, r["answer"]) if not r.get("error") else {"score": 0, "error": r["error"]}))
            j = json.loads(cache.read_text())
            recall, missing = fact_recall(r["answer"], q["key_facts"])
            sources = sorted({(c.get("connector") or "?") for c in r.get("citations", [])})
            rows.append({"id": q["id"], "type": q["type"], "phase": phase, "judge": j.get("score"),
                         "fact_recall": recall, "missing_facts": missing, "cited_sources": sources,
                         "tools": len(r.get("tools", [])), "seconds": r.get("seconds"), "error": r.get("error")})
    (OUT / "graded.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows))

    def mean(vals):
        vals = [v for v in vals if v is not None]
        return round(sum(vals) / len(vals), 3) if vals else None

    report = {}
    for phase in PHASES:
        ps = [r for r in rows if r["phase"] == phase]
        if not ps:
            continue
        by_type = defaultdict(list)
        for r in ps:
            by_type[r["type"]].append(r)
        report[phase] = {"answered": len(ps),
                         "accuracy": mean([1.0 if r["judge"] == 1 else 0.0 for r in ps]),
                         "judge_mean": mean([r["judge"] for r in ps]),
                         "fact_recall": mean([r["fact_recall"] for r in ps]),
                         "by_type": {t: {"n": len(v), "accuracy": mean([1.0 if r["judge"] == 1 else 0.0 for r in v]),
                                         "judge_mean": mean([r["judge"] for r in v]),
                                         "fact_recall": mean([r["fact_recall"] for r in v])}
                                     for t, v in sorted(by_type.items())}}
    a = {r["id"]: r for r in rows if r["phase"] == "code-only"}
    b = {r["id"]: r for r in rows if r["phase"] == "with-company"}
    both = sorted(set(a) & set(b))
    if both:
        drift = defaultdict(lambda: {"better": 0, "same": 0, "worse": 0})
        for i in both:
            ja, jb = a[i]["judge"] or 0, b[i]["judge"] or 0
            drift[a[i]["type"]]["better" if jb > ja else "worse" if jb < ja else "same"] += 1
        report["drift_per_question"] = dict(drift)
    (OUT / "results.json").write_text(json.dumps(report, indent=1))
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
