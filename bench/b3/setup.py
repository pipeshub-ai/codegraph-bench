"""B3: bring the fresh cg-b3 stack to a synced GitHub connector, without a browser.

1. The repo's own first-run bootstrap (org, admin, LLM = llm_1, a PAT for the API).
2. embedding_1 as the default embedding model.
3. A GitHub Teams connector (it takes a personal access token; the personal GitHub
   connector is OAuth-only) on the B3 copy of the fixture, manual sync, then enabled.

Secrets are read from $MODELS_JSON (models.example.json) and the harness .env, written only to
0600 files in this folder, and never printed.

usage: python setup.py
"""
from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
ORIGIN = "http://127.0.0.1:3940"
REPO = os.environ.get("B3_REPO", "tushar1245/pipeshub-codegraph-fixture-b3")  # B3 pushes here: use your own copy
BOOTSTRAP = Path(os.environ["PIPESHUB_SRC"]) / "deployment/docker-compose/bootstrap-first-run.sh"
MODELS = json.loads(Path(os.environ["MODELS_JSON"]).read_text())
PAT_FILE, ADMIN_FILE, STATE_FILE = HERE / "pat.token", HERE / "admin.env", HERE / "state.json"


def private(path: Path, text: str) -> None:
    path.touch(mode=0o600, exist_ok=True)
    os.chmod(path, 0o600)
    path.write_text(text)


def github_token() -> str:
    if os.environ.get("GITHUB_FIXTURE_TOKEN"):
        return os.environ["GITHUB_FIXTURE_TOKEN"]
    for line in (HERE.parent.parent / ".env").read_text().splitlines():
        if line.startswith("GITHUB_FIXTURE_TOKEN="):
            return line.split("=", 1)[1].strip().strip("'\"")
    sys.exit("GITHUB_FIXTURE_TOKEN missing from the harness .env")


def wait_for_app() -> None:
    for _ in range(120):
        try:
            if httpx.get(f"{ORIGIN}/api/v1/org/exists", timeout=5).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(5)
    sys.exit("app did not answer on /api/v1/org/exists")


def bootstrap() -> None:
    if PAT_FILE.exists():
        return
    llm = MODELS["llm_1"]["configuration"]
    password = "B3-" + secrets.token_urlsafe(18) + "a1!"
    private(ADMIN_FILE, f"email=b3-admin@example.com\npassword={password}\n")
    env = HERE / "bootstrap.env"
    private(env, "\n".join([
        f"PIPESHUB_ORIGIN={ORIGIN}",
        "PIPESHUB_ACCOUNT_EMAIL=b3-admin@example.com",
        f"PIPESHUB_ACCOUNT_PASSWORD={password}",
        "PIPESHUB_ACCOUNT_FULL_NAME=B3 Benchmark",
        "PIPESHUB_ACCOUNT_TYPE=business",
        "PIPESHUB_REGISTERED_NAME=Code Graph B3",
        "PIPESHUB_LLM_PROVIDER=azureOpenAI",
        f"PIPESHUB_LLM_MODEL={llm['model']}",
        f"PIPESHUB_LLM_API_KEY={llm['apiKey']}",
        f"PIPESHUB_LLM_ENDPOINT={llm['endpoint']}",
        f"PIPESHUB_LLM_DEPLOYMENT={llm.get('deploymentName') or llm['model']}",
    ]) + "\n")
    try:
        subprocess.run(["bash", str(BOOTSTRAP), "--env-file", str(env), "--token-file", str(PAT_FILE)],
                       check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:  # the script never echoes secrets; its stderr says what failed
        sys.exit(f"bootstrap failed: {exc.stderr.strip()[-800:]}")
    finally:
        env.unlink(missing_ok=True)


_SESSION: dict[str, str] = {}


def admin_token() -> str:
    """A session token as the admin: the bootstrap's PAT carries agent scopes only, not config:write."""
    if "jwt" not in _SESSION:
        creds = dict(line.split("=", 1) for line in ADMIN_FILE.read_text().splitlines() if "=" in line)
        init = httpx.post(f"{ORIGIN}/api/v1/userAccount/initAuth", json={"email": creds["email"]}, timeout=30)
        session = init.headers.get("x-session-token") or sys.exit(f"initAuth -> {init.status_code}")
        auth = httpx.post(f"{ORIGIN}/api/v1/userAccount/authenticate", timeout=30,
                          headers={"x-session-token": session},
                          json={"method": "password", "email": creds["email"],
                                "credentials": {"password": creds["password"]}})
        _SESSION["jwt"] = auth.json().get("accessToken") or sys.exit(f"authenticate -> {auth.status_code}")
    return _SESSION["jwt"]


def api(method: str, path: str, body: dict | None = None) -> dict:
    headers = {"Authorization": f"Bearer {admin_token()}"}
    r = httpx.request(method, f"{ORIGIN}{path}", json=body, headers=headers, timeout=60)
    if r.status_code >= 400:
        sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
    return r.json() if r.content else {}


def main() -> None:
    wait_for_app()
    bootstrap()
    state = json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    if not state.get("embedding"):
        emb = MODELS["embedding_1"]["configuration"]
        api("POST", "/api/v1/configurationManager/ai-models/providers", {
            "modelType": "embedding", "provider": "azureOpenAI", "isDefault": True, "isMultimodal": False,
            "configuration": {"model": emb["model"], "apiKey": emb["apiKey"], "endpoint": emb["endpoint"],
                              "deploymentName": emb.get("deploymentName") or emb["model"]}})
        state["embedding"] = True
    if not state.get("connector_id"):
        created = api("POST", "/api/v1/connectors/", {"connectorType": "GitHub Teams",
                                                       "instanceName": "Code graph fixture (B3)",
                                                       "scope": "team", "authType": "API_TOKEN"})
        state["connector_id"] = created["connector"]["connectorId"]
    STATE_FILE.write_text(json.dumps(state))
    cid = state["connector_id"]
    if state.get("enabled"):  # config changes only while the connector is off
        api("POST", f"/api/v1/connectors/{cid}/toggle", {"type": "sync"})
        state.pop("enabled")
        STATE_FILE.write_text(json.dumps(state))
    api("PUT", f"/api/v1/connectors/{cid}/config", {
        "auth": {"authType": "API_TOKEN", "token": github_token()},
        "sync": {"strategy": "MANUAL"},
        "filters": {"sync": {"values": {"repo_ids": {"operator": "in", "type": "list", "value": [REPO]}}},
                    "indexing": {"values": {
                        "issues": {"operator": "is", "type": "boolean", "value": False},
                        "merge_requests": {"operator": "is", "type": "boolean", "value": False}}}},
    })
    if not state.get("enabled"):
        api("POST", f"/api/v1/connectors/{cid}/toggle", {"type": "sync", "fullSync": True})
        state["enabled"] = time.time()
        STATE_FILE.write_text(json.dumps(state))
    print("connector", cid, "enabled; full sync started")


def resync(connector_id: str, *, full: bool = False) -> None:
    """Ask for a sync now: B3 has no webhook, so every measured sync starts here."""
    api("POST", f"/api/v1/connectors/{connector_id}/resync", {"connectorName": "GitHub Teams", "fullSync": full})


if __name__ == "__main__":
    main()
