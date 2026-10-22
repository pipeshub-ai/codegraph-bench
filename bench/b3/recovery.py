"""B3 recovery: kill a service mid-sync or mid-build, then time until the graph is right again.

  python recovery.py            # every scenario, alternating pushes of step 1 and step 0

Each scenario pushes a step, resyncs, waits for its trigger, kills, and then measures with
live.wait_ready (timeout 30 min, so the 600 s reconciler sweep can show). The container's
process monitor restarts a killed service within 20 s. Results append to reports/b3/recovery.jsonl.
"""
from __future__ import annotations

import json
import subprocess
import time

import live
import setup

APP, NEO4J = "cg-b3-pipeshub-ai-1", "cg-b3-neo4j-1"


def kill(module: str) -> None:
    subprocess.run(["docker", "exec", APP, "pkill", "-9", "-f", f"[p]ython -m app.{module}"], check=False)


def restart(container: str) -> None:
    subprocess.run(["docker", "restart", "-t", "0", container], check=True, capture_output=True)
    for _ in range(120):  # wait_ready reads Neo4j, so wait until it answers again
        try:
            live.status(live.connector_id())
            return
        except Exception:  # noqa: BLE001
            time.sleep(2)


def after(seconds: float):
    return lambda cid, t0: time.time() - t0 >= seconds


def building(cid: str, t0: float) -> bool:
    return any(b["st"] == "building" for b in live.status(cid)["builds"])


def indexing(cid: str, t0: float) -> bool:
    return live.status(cid)["waiting"] > 0


SCENARIOS = [
    ("connector killed mid-sync", after(1.0), lambda: kill("connectors_main")),
    ("indexing killed while files index", indexing, lambda: kill("indexing_main")),
    ("indexing killed during the edge build", building, lambda: kill("indexing_main")),
    ("Neo4j restarted during the edge build", building, lambda: restart(NEO4J)),
    ("app container restarted while files index", indexing, lambda: restart(APP)),
]


def run(name, trigger, action, target: int) -> dict:
    cid = live.connector_id()
    subprocess.run(["git", "-C", str(live.FIXTURE), "push", "-q", "--force", "origin",
                    f"step-{target}^{{commit}}:refs/heads/main"], check=True, capture_output=True)
    pushed_ms = int(time.time() * 1000)
    setup.resync(cid)
    t0, fired = time.time(), False
    while time.time() - t0 < 60:
        try:
            fired = trigger(cid, t0)
        except Exception:  # noqa: BLE001  Neo4j may be mid-restart from an earlier scenario
            fired = False
        if fired:
            break
        time.sleep(0.1)
    killed_s = round(time.time() - pushed_ms / 1000, 1)
    action()
    timing = live.wait_ready(cid, pushed_ms, timeout_s=1800)
    s = live.score(target, cid)["overall"]
    r = {"scenario": name, "step": target, "trigger_fired": fired, "killed_at_s": killed_s, **timing,
         "fp": s["fp"], "precision": s["precision"], "recall": s["recall"]}
    with (live.OUT / "recovery.jsonl").open("a") as f:
        f.write(json.dumps(r) + "\n")
    return r


def initial_build_kill() -> None:
    """Kill indexing in the first sync's edge build, which runs for seconds, not milliseconds."""
    cid = live.connector_id()
    enabled_ms = int(json.loads((live.HERE / "state.json").read_text())["enabled"] * 1000)
    t0 = time.time()
    while not building(cid, t0) and time.time() - t0 < 900:
        time.sleep(0.1)
    time.sleep(1.0)  # well inside the build
    killed_s = round(time.time() - enabled_ms / 1000, 1)
    kill("indexing_main")
    timing = live.wait_ready(cid, enabled_ms, timeout_s=1800)
    s = live.score(0, cid)["overall"]
    r = {"scenario": "indexing killed 1 s into the first sync's edge build", "step": 0, "killed_at_s": killed_s,
         **timing, "fp": s["fp"], "precision": s["precision"], "recall": s["recall"]}
    with (live.OUT / "recovery.jsonl").open("a") as f:
        f.write(json.dumps(r) + "\n")
    print(json.dumps(r), flush=True)


def main() -> None:
    # The stack ends a full run at step 0; alternate so every push is a real change.
    for i, (name, trigger, action) in enumerate(SCENARIOS):
        print(json.dumps(run(name, trigger, action, target=1 - i % 2)), flush=True)


if __name__ == "__main__":
    import sys
    initial_build_kill() if sys.argv[1:] == ["initial-build"] else main()
