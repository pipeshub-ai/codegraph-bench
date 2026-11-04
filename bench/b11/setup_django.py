"""B11: a second GitHub Teams connector on the cg-b11 stack, for your copy of the Django 3.2 snapshot
($B11_DJANGO_REPO, built by snapshot.sh): code and test files, manual sync, then enabled.

Reuses setup.py's admin session; secrets stay where that script keeps them. State: state-django.json.

usage: python setup_django.py   (after setup.py)
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import setup as b11  # noqa: E402

REPO = os.environ["B11_DJANGO_REPO"]  # your-user/codegraph-snapshot-django32 (snapshot.sh)
STATE = Path(__file__).resolve().parent / "state-django.json"


def main() -> None:
    b11.wait_for_app()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if not state.get("connector_id"):
        created = b11.api("POST", "/api/v1/connectors/", {"connectorType": "GitHub Teams",
                                                          "instanceName": "Django 3.2 snapshot (B11)",
                                                          "scope": "team", "authType": "API_TOKEN"})
        state["connector_id"] = created["connector"]["connectorId"]
        STATE.write_text(json.dumps(state))
    cid = state["connector_id"]
    b11.api("PUT", f"/api/v1/connectors/{cid}/config", {
        "auth": {"authType": "API_TOKEN", "token": b11.github_token()},
        "sync": {"strategy": "MANUAL"},
        "filters": {"sync": {"values": {"repo_ids": {"operator": "in", "type": "list", "value": [REPO]}}},
                    "indexing": {"values": {
                        "issues": {"operator": "is", "type": "boolean", "value": False},
                        "merge_requests": {"operator": "is", "type": "boolean", "value": False},
                        "code_files": {"operator": "is", "type": "boolean", "value": True},
                        "test_files": {"operator": "is", "type": "boolean", "value": True}}}},
    })
    if not state.get("enabled"):
        b11.api("POST", f"/api/v1/connectors/{cid}/toggle", {"type": "sync", "fullSync": True})
        state["enabled"] = time.time()
        STATE.write_text(json.dumps(state))
    print("connector", cid, "enabled; full sync started")


if __name__ == "__main__":
    main()
