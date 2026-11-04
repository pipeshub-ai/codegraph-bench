"""Run Django's own tests with a profiler and record which Django function calls which (B12 runtime edges).

  python trace_run.py <out.jsonl> <runtests args...>     # inside the container, cwd = <checkout>/tests

Each line is one edge between two code objects in the `django/` package:
  {"caller": [path, firstlineno, name], "callee": [path, firstlineno, name]}
Paths are repo-relative. Mapping code objects to `path::Class.method` names is done
afterwards from the source (b12_runtime.py), not here.

Only a frame's first entry counts: a resumed generator or coroutine is not a call.
"""
import atexit
import json
import os
import sys
import threading

OUT = sys.argv[1]
ROOT = os.path.abspath(os.path.join(os.getcwd(), "..")) + os.sep
PKG = ROOT + "django" + os.sep
edges = set()


def key(code):
    return (code.co_filename[len(ROOT):], code.co_firstlineno, code.co_name)


def profile(frame, event, arg):
    if event != "call" or frame.f_lasti != -1:
        return
    code = frame.f_code
    if not code.co_filename.startswith(PKG):
        return
    caller = frame.f_back
    if caller is None or not caller.f_code.co_filename.startswith(PKG):
        return
    edges.add((key(caller.f_code), key(code)))


@atexit.register
def dump():
    with open(OUT, "w") as f:
        for a, b in sorted(edges):
            f.write(json.dumps({"caller": a, "callee": b}) + "\n")
    print(f"trace_run: {len(edges)} edges -> {OUT}", file=sys.stderr)


sys.argv = ["runtests.py"] + sys.argv[2:]
sys.path.insert(0, os.getcwd())
threading.setprofile(profile)
sys.setprofile(profile)
try:
    path = os.path.join(os.getcwd(), "runtests.py")
    exec(compile(open(path).read(), path, "exec"), {"__name__": "__main__", "__file__": path})
finally:
    sys.setprofile(None)
