"""B7: ask every question through PipesHub's own chat (agent mode), once per phase.

  python ask.py code-only       # phase A: only apache/kafka's code is indexed
  python ask.py with-company    # phase B: code + Jira + Confluence KIPs + docs
  python ask.py <phase> --limit 3
  python ask.py code-only --questions questions_v2.jsonl --out b7v2   # v2: reports/b7v2/r1/agent-<phase>/

v2 runs both phases on the one phase-B instance: code-only scopes the agent to the GitHub
connector with the chat's `filters.apps`, so both phases see the same index and model.

Each question goes to POST /api/v1/conversations/stream with chatMode "agent" and web search
off (agentCapabilities.webSearch = false): Kafka's docs, KIPs and issues are all on the public
web, so with web search on the code-only phase would read company data from the internet.
Recorded per question: the answer text, the tools the agent called, the records it cited (read
back from the saved conversation), and the wall time. Questions already answered are skipped.
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx

import setup

HERE = Path(__file__).resolve().parent
OUT = HERE.parents[1] / "reports" / "b7"


def headers() -> dict:
    return {"Authorization": f"Bearer {setup.PAT_FILE.read_text().strip()}"}


def fetch_citations(conv_id: str) -> list[dict]:
    """The records the final answer cites. Read as the admin: the agent PAT has no conversation:read."""
    try:
        conv = httpx.get(f"{setup.ORIGIN}/api/v1/conversations/{conv_id}/",
                         headers={"Authorization": f"Bearer {setup.admin_token()}"}, timeout=60).json()
    except (httpx.HTTPError, ValueError):
        return []
    msgs = (conv.get("conversation") or conv).get("messages") or []
    bot = [m for m in msgs if m.get("messageType") != "user_query"]
    out = []
    for c in (bot[-1].get("citations") if bot else []) or []:
        meta = (c.get("citationData") or c).get("metadata") or {}
        out.append({"recordName": meta.get("recordName"), "connector": meta.get("connector"),
                    "recordType": meta.get("recordType"), "webUrl": meta.get("webUrl")})
    return out


def backfill_citations(phase: str) -> None:
    for path in sorted((OUT / phase).glob("*.json")):
        rec = json.loads(path.read_text())
        if rec.get("conversation_id") and not rec.get("citations"):
            rec["citations"] = fetch_citations(rec["conversation_id"])
            path.write_text(json.dumps(rec, indent=1))


def ask_one(q: dict, phase: str, out: Path | None = None) -> dict | None:
    path = (out or OUT / phase) / f"{q['id']}.json"
    if path.exists():
        return None
    started = time.time()
    text: dict[str, list[str]] = {}
    order: list[str] = []
    tools: list[str] = []
    finished, error = None, None
    body = {"query": q["question"], "chatMode": "agent", "agentCapabilities": {"webSearch": False}}
    if out is not None and phase == "code-only":
        body["filters"] = {"apps": [json.loads(setup.STATE_FILE.read_text())["connector_id"]]}
        body["strictScope"] = True
    try:
        with httpx.stream("POST", f"{setup.ORIGIN}/api/v1/conversations/stream", json=body,
                          headers={**headers(), "Accept": "text/event-stream"}, timeout=600) as r:
            if r.status_code >= 400:
                error = f"HTTP {r.status_code}: {r.read()[:300]!r}"
            for line in r.iter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    ev = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                kind = ev.get("type")
                if kind == "TEXT_MESSAGE_START":
                    order.append(ev["messageId"])
                elif kind == "TEXT_MESSAGE_CONTENT":
                    text.setdefault(ev["messageId"], []).append(ev.get("delta") or "")
                elif kind == "TOOL_CALL_START":
                    tools.append(ev.get("toolCallName"))
                elif kind == "RUN_FINISHED":
                    finished = ev.get("result")
                elif kind == "RUN_ERROR":
                    error = ev.get("message") or json.dumps(ev)[:300]
    except httpx.HTTPError as exc:
        error = f"{type(exc).__name__}: {exc}"
    # The final assistant message is the answer; earlier ones are interim narration.
    answer = "".join(text.get(order[-1], [])) if order else ""
    conv_id = ((finished or {}).get("conversation") or {}).get("_id")
    citations = fetch_citations(conv_id) if conv_id else []
    rec = {"id": q["id"], "type": q["type"], "phase": phase, "question": q["question"], "answer": answer,
           "tools": tools, "citations": citations, "conversation_id": conv_id, "error": error,
           "seconds": round(time.time() - started, 1)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rec, indent=1))
    return rec


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("phase", choices=("code-only", "with-company"))
    ap.add_argument("--limit", type=int)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--questions", default="questions.jsonl")
    ap.add_argument("--out", help="v2 report folder under reports/ (default: the original reports/b7/<phase>)")
    ap.add_argument("--repeat", type=int, default=1)
    ap.add_argument("--backfill-citations", action="store_true")
    args = ap.parse_args()
    if args.backfill_citations:
        backfill_citations(args.phase)
        return
    qs = [json.loads(line) for line in (HERE / args.questions).read_text().splitlines() if line.strip()]
    qs = qs[: args.limit] if args.limit else qs
    out = HERE.parents[1] / "reports" / args.out / f"r{args.repeat}" / f"agent-{args.phase}" if args.out else None
    with ThreadPoolExecutor(args.jobs) as ex:
        for rec in ex.map(lambda q: ask_one(q, args.phase, out), qs):
            if rec:
                print(rec["id"], rec["type"], f"{rec['seconds']}s", "tools", len(rec["tools"]),
                      "cites", len(rec["citations"]), "ERROR " + rec["error"] if rec["error"] else "", flush=True)


if __name__ == "__main__":
    main()
