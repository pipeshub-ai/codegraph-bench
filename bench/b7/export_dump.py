"""B7 v2 baseline: export phase B's indexed company data (Jira, Confluence, docs site) as plain files.

  python export_dump.py <dest dir>

Rebuilds each record's text from its indexed blocks in the stack's Qdrant (ordered by blockIndex)
and names the file after the record (from Neo4j): <dest>/jira/KAFKA-123 <summary>.md,
<dest>/confluence/<page title>.md, <dest>/docs/<page>.md, each starting with its title and URL.
This is the same knowledge PipesHub indexed, as flat files a coding agent can grep, for the
"isn't it just grep over an export?" arm. Secrets come from b7.env and are never printed.

A Jira issue's fields (status, resolution, fix versions, ...) are not in the index: PipesHub
reads them from Jira when it opens a ticket. They are fetched here with one search per 100
issues through asf.py (cached, polite) and written as the same "Ticket Information" header.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent))  # asf.py, under python -I
import asf  # noqa: E402

HERE = Path(__file__).resolve().parent
ENV = dict(line.split("=", 1) for line in (HERE / "b7.env").read_text().splitlines() if "=" in line and not line.startswith("#"))
STATE = json.loads((HERE / "state.json").read_text())
FOLDER = {"jira": "jira", "confluence": "confluence", "docs": "docs"}


def qdrant() -> tuple[str, dict]:
    ip = subprocess.run(["docker", "inspect", "cg-b7-qdrant-1", "--format",
                         "{{range .NetworkSettings.Networks}}{{.IPAddress}} {{end}}"],
                        capture_output=True, text=True, check=True).stdout.split()[0]
    return f"http://{ip}:6333", {"api-key": ENV["QDRANT_API_KEY"]}


def records(connector_id: str) -> dict[str, dict]:
    """virtualRecordId -> {name, url} for the connector's records."""
    query = ("MATCH (r:Record {connectorId: $c}) WHERE r.virtualRecordId IS NOT NULL "
             "RETURN r.virtualRecordId AS v, r.recordName AS n, coalesce(r.webUrl, '') AS u")
    out = subprocess.run(["docker", "exec", "cg-b7-neo4j-1", "cypher-shell", "-u", "neo4j", "-p", ENV["NEO4J_PASSWORD"],
                          "--format", "plain", "-P", f"c => '{connector_id}'", query],
                         capture_output=True, text=True, check=True).stdout.splitlines()[1:]
    got = {}
    for line in out:
        m = re.fullmatch(r'"([^"]+)", "(.*)", "(.*)"', line)  # cypher-shell's plain format, not JSON
        if m:
            got[m.group(1)] = {"name": m.group(2).replace('\\"', '"'), "url": m.group(3)}
    return got


def blocks(base: str, headers: dict, connector_id: str) -> dict[str, list[tuple[int, str]]]:
    by_record: dict[str, list[tuple[int, str]]] = defaultdict(list)
    offset = None
    with httpx.Client(timeout=120) as client:
        while True:
            body = {"limit": 2000, "with_payload": ["page_content", "metadata"], "with_vector": False,
                    "filter": {"must": [{"key": "connectorIds", "match": {"value": connector_id}},
                                        {"key": "metadata.isBlock", "match": {"value": True}}]}}
            if offset is not None:
                body["offset"] = offset
            res = client.post(f"{base}/collections/records/points/scroll", json=body, headers=headers).json()["result"]
            for p in res["points"]:
                meta = p["payload"].get("metadata") or {}
                by_record[meta.get("virtualRecordId")].append((meta.get("blockIndex") or 0, p["payload"].get("page_content") or ""))
            offset = res.get("next_page_offset")
            if offset is None:
                return by_record


JIRA_FIELDS = "status,resolution,resolutiondate,priority,issuetype,fixVersions,versions,components,created"


def jira_fields() -> dict[str, str]:
    """KAFKA-N -> its Ticket Information header, for the issues phase B synced (created >= 2022)."""
    headers, start = {}, 0
    while True:
        page = asf.get_json("https://issues.apache.org/jira/rest/api/2/search", {
            "jql": "project = KAFKA AND created >= 2022-01-01 ORDER BY key ASC", "startAt": start,
            "maxResults": 100, "fields": JIRA_FIELDS})
        for issue in page["issues"]:
            f = issue["fields"]
            names = lambda xs: ", ".join(x["name"] for x in xs or []) or "None"
            headers[issue["key"]] = "\n".join([
                "Ticket Information:",
                f"* Status: {(f.get('status') or {}).get('name')}",
                f"* Resolution: {(f.get('resolution') or {}).get('name') or 'Unresolved'}",
                f"* Resolved: {(f.get('resolutiondate') or 'None')[:10]}",
                f"* Priority: {(f.get('priority') or {}).get('name')}",
                f"* Type: {(f.get('issuetype') or {}).get('name')}",
                f"* Fix Versions: {names(f.get('fixVersions'))}",
                f"* Affects Versions: {names(f.get('versions'))}",
                f"* Components: {names(f.get('components'))}",
                f"* Created: {f.get('created')}",
            ])
        start += len(page["issues"])
        if not page["issues"] or start >= page["total"]:
            return headers


def safe(name: str) -> str:
    return re.sub(r"[^\w.+ -]+", "_", name).strip()[:150] or "untitled"


def main() -> None:
    dest = Path(sys.argv[1])
    base, headers = qdrant()
    tickets = jira_fields()
    print("jira field headers", len(tickets), "ASF requests so far", asf.requests_made(), flush=True)
    for key, folder in FOLDER.items():
        cid = STATE["phase_b"][key]["id"]
        names = records(cid)
        written = 0
        for vrid, parts in blocks(base, headers, cid).items():
            rec = names.get(vrid)
            if not rec:
                continue
            seen, text = set(), []
            for _, chunk in sorted(parts):
                if chunk not in seen:  # a block can be stored once per record version
                    seen.add(chunk)
                    text.append(chunk)
            path = dest / folder / f"{safe(rec['name'])}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                path = path.with_name(f"{path.stem} ({vrid[:8]}).md")
            issue = re.match(r"\[(KAFKA-\d+)\]", rec["name"] or "")
            info = tickets.get(issue.group(1), "") + "\n\n" if issue and folder == "jira" else ""
            path.write_text(f"# {rec['name']}\n\n{rec['url']}\n\n{info}" + "\n\n".join(text) + "\n")
            written += 1
        print(key, "records", len(names), "files", written, flush=True)


if __name__ == "__main__":
    main()
