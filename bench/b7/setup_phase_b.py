"""B7 phase B: add Kafka's company data beside its code, then sync it.

  python setup_phase_b.py          # creates and enables the three connectors (run after phase A)

- Jira Data Center Personal: Apache's Jira through the local read-only proxy (jira_proxy.py),
  project KAFKA, issues created since 2022-01-01, attachments off. The personal variant skips
  the user and permission listing the team variant does.
- Confluence Data Center Personal: Apache's wiki, read anonymously (it answers a placeholder token
  as "anonymous"), space KAFKA, pages only: no attachments, comments or blog posts.
- Web: Kafka's 4.3 docs, kept under /43/, at most 150 pages, robots.txt respected.
Connector state goes to state.json beside phase A's; secrets are never printed.
"""
from __future__ import annotations

import json
import time

import setup

SINCE_2022_MS = 1640995200000  # 2022-01-01T00:00:00Z


def bool_filter(value: bool) -> dict:
    return {"operator": "is", "type": "boolean", "value": value}


CONNECTORS = {
    "jira": {
        "create": {"connectorType": "Jira Data Center Personal", "instanceName": "Apache Kafka Jira (B7)",
                   "scope": "personal", "authType": "API_TOKEN"},
        "config": {
            "auth": {"authType": "API_TOKEN", "baseUrl": "http://jira-proxy:8080/jira", "apiToken": "b7-anonymous"},
            "sync": {"strategy": "MANUAL"},
            "filters": {
                "sync": {"values": {
                    "project_keys": {"operator": "in", "type": "list", "value": ["KAFKA"]},
                    "created": {"operator": "is_after", "type": "datetime",
                                "value": {"start": SINCE_2022_MS, "end": None}}}},
                "indexing": {"values": {"issues": bool_filter(True), "issue_attachments": bool_filter(False)}}},
        },
    },
    "confluence": {
        "create": {"connectorType": "Confluence Data Center Personal", "instanceName": "Apache Kafka wiki (B7)",
                   "scope": "personal", "authType": "API_TOKEN"},
        "config": {
            "auth": {"authType": "API_TOKEN", "baseUrl": "https://cwiki.apache.org/confluence", "apiToken": "b7-anonymous"},
            "sync": {"strategy": "MANUAL"},
            "filters": {
                "sync": {"values": {"space_keys": {"operator": "in", "type": "list", "value": ["KAFKA"]}}},
                "indexing": {"values": {
                    "pages": bool_filter(True), "page_attachments": bool_filter(False),
                    "page_comments": bool_filter(False), "blogposts": bool_filter(False),
                    "blogpost_attachments": bool_filter(False), "blogpost_comments": bool_filter(False)}}},
        },
    },
    "docs": {
        "create": {"connectorType": "Web", "instanceName": "Apache Kafka 4.3 docs (B7)", "scope": "team",
                   "authType": "NONE"},
        "config": {
            "auth": {"authType": "NONE"},
            "sync": {"strategy": "MANUAL", "url": "https://kafka.apache.org/43/", "type": "recursive",
                     "restrict_to_start_path": True, "max_pages": 150, "depth": 5,
                     "respect_robots_txt": True, "follow_external": False},
        },
    },
}


def main() -> None:
    state = json.loads(setup.STATE_FILE.read_text())
    phase_b = state.setdefault("phase_b", {})
    for key, spec in CONNECTORS.items():
        if key not in phase_b:
            created = setup.api("POST", "/api/v1/connectors/", spec["create"])
            phase_b[key] = {"id": created["connector"]["connectorId"]}
            setup.STATE_FILE.write_text(json.dumps(state))
        cid = phase_b[key]["id"]
        if not phase_b[key].get("enabled"):
            setup.api("PUT", f"/api/v1/connectors/{cid}/config", spec["config"])
            setup.api("POST", f"/api/v1/connectors/{cid}/toggle", {"type": "sync", "fullSync": True})
            phase_b[key]["enabled"] = time.time()
            setup.STATE_FILE.write_text(json.dumps(state))
        print(key, cid, "enabled; full sync started")


if __name__ == "__main__":
    main()
