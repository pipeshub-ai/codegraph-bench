"""B1 v2's remaining (repo, tool) jobs in parallel, within the machine's limits.

  python3 run_b1_v2_par.py repo:dir:tool[,tool...] ...

A job starts only while CPU is under 85% and memory under 80% (headroom under the 90%
limit). If memory reaches 90%, the newest job's whole process group is stopped and the
job is queued to run alone later; a job stopped while running alone is the tool's own
limit and is recorded as capped (memory). A repo's tool jobs wait for its PipesHub job,
whose report sets the hand-written scope they are scored on.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

BENCH = Path(__file__).resolve().parent
LOGS = BENCH / "work-v2" / "par"
MAX_JOBS = 6
MAX_CGC = 2  # its index peaks at 10-40 GB


def cpu_busy(seconds: float = 3.0) -> float:
    def sample() -> tuple[int, int]:
        f = [int(x) for x in open("/proc/stat").readline().split()[1:]]
        return sum(f), f[3] + f[4]
    t0, i0 = sample()
    time.sleep(seconds)
    t1, i1 = sample()
    return 100 * (1 - (i1 - i0) / max(t1 - t0, 1))


def mem_used() -> float:
    info = dict(line.split(":", 1) for line in open("/proc/meminfo"))
    total, avail = (int(info[k].split()[0]) for k in ("MemTotal", "MemAvailable"))
    return 100 * (1 - avail / total)


def stop_tree(pid: int) -> None:
    """SIGTERM a job and everything under it: `timeout` moves its command to a process group of its own."""
    kids = subprocess.run(["ps", "-o", "pid=", "--ppid", str(pid)], capture_output=True, text=True).stdout.split()
    for kid in kids:
        stop_tree(int(kid))
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass


def main() -> None:
    LOGS.mkdir(parents=True, exist_ok=True)
    queue: list[dict] = []
    for spec in sys.argv[1:]:
        name, src, tools = spec.split(":")
        for tool in tools.split(","):
            queue.append({"name": name, "dir": src, "tool": tool, "solo": False})
    running: list[dict] = []
    done: set[tuple[str, str]] = set()
    while queue or running:
        for job in list(running):
            if job["proc"].poll() is not None:
                running.remove(job)
                done.add((job["name"], job["tool"]))
                print(f"finished {job['name']} {job['tool']} rc={job['proc'].returncode} "
                      f"in {time.time() - job['started']:.0f}s", flush=True)
        if mem_used() >= 90 and running:
            job = max(running, key=lambda j: j["started"])
            stop_tree(job["proc"].pid)
            job["proc"].wait()
            running.remove(job)
            if job["solo"]:
                (BENCH / "work-v2" / job["tool"] / f"{job['name']}.time").write_text(
                    "Command terminated by signal 15 (memory reached 90% with this job running alone)\n")
                done.add((job["name"], job["tool"]))
                print(f"capped {job['name']} {job['tool']} (memory, alone)", flush=True)
            else:
                queue.insert(0, {**job, "solo": True})
                print(f"memory 90%: stopped {job['name']} {job['tool']}, queued to run alone", flush=True)
            continue
        ready = [j for j in queue
                 if j["tool"] == "pipeshub" or (j["name"], "pipeshub") in done
                 or not any(q["name"] == j["name"] and q["tool"] == "pipeshub" for q in queue + running)]
        solo_running = any(j["solo"] for j in running)
        if ready and not solo_running and len(running) < MAX_JOBS:
            job = ready[0]
            cgc_running = sum(j["tool"] == "cgc" for j in running)
            fits = (not job["solo"] or not running) and (job["tool"] != "cgc" or cgc_running < MAX_CGC)
            if fits and cpu_busy() < 85 and mem_used() < 80:
                queue.remove(job)
                env = {**os.environ, "PROJECTS": f"{job['name']}:{job['dir']}", "TOOLS": job["tool"],
                       "GUARD": "0", "NO_COMPILE": "1"}
                log = open(LOGS / f"{job['name']}-{job['tool']}.log", "w")
                job["proc"] = subprocess.Popen(["bash", str(BENCH / "run_b1_v2.sh")], env=env, stdout=log,
                                               stderr=subprocess.STDOUT, start_new_session=True)
                job["started"] = time.time()
                running.append(job)
                print(f"started {job['name']} {job['tool']}{' (alone)' if job['solo'] else ''}", flush=True)
                continue
        time.sleep(10)
    subprocess.run(["python3", str(BENCH / "compile_b1.py")],
                   stdout=open(BENCH.parent / "reports/b1-v2/COMPARISON.md", "w"), check=False)
    print("b1 v2 par done", flush=True)


if __name__ == "__main__":
    main()
