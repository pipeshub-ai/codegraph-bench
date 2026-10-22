"""Write the fixture repos and their answer keys.

    python -m fixture_gen.generate --out ../ --key gold/            # step 0
    python -m fixture_gen.generate --out ../ --key gold/ --step 3   # after mutation 3
    python -m fixture_gen.generate --history ../pipeshub-codegraph-fixture

``--history`` rebuilds the fixture's git history: ``main`` holds step 0 and the
``mutations`` branch adds one commit per step, tagged ``step-N``.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
from pathlib import Path

from . import admin, backend, frontend, polyglot, toplevel
from .domains import STEPS
from .model import Repo

MAIN = "pipeshub-codegraph-fixture"
ADMIN = "pipeshub-codegraph-fixture-admin"


def build_main(step: int) -> Repo:
    repo = Repo(MAIN)
    for part in (toplevel, backend, frontend, polyglot):
        part.build(repo, step)
    return repo


def build_admin(step: int) -> Repo:
    repo = Repo(ADMIN)
    admin.build(repo, step)
    return repo


def _clear_tree(root: Path) -> None:
    """Remove everything but .git, so files dropped by a step really disappear."""
    if not root.exists():
        return
    for child in root.iterdir():
        if child.name == ".git":
            continue
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()


def write(repo: Repo, tree: Path | None, key: Path | None) -> None:
    if tree is not None:
        _clear_tree(tree)
        tree.mkdir(parents=True, exist_ok=True)
        repo.write_tree(tree)
    if key is not None:
        repo.write_key(key)


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True,
                          text=True).stdout


def history(root: Path) -> None:
    if not (root / ".git").exists():
        _git(root.parent, "init", "-q", "-b", "main", str(root))
    for step, message in STEPS.items():
        if step == 1:
            _git(root, "checkout", "-q", "-B", "mutations")
        write(build_main(step), root, None)
        _git(root, "add", "-A")
        title = "acme-shop: initial import" if step == 0 else f"step {step}: {message}"
        if _git(root, "status", "--porcelain").strip():
            _git(root, "commit", "-q", "-m", title)
        _git(root, "tag", "-f", f"step-{step}")
    _git(root, "checkout", "-q", "main")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=Path, help="parent directory for the fixture repo trees")
    ap.add_argument("--key", type=Path, help="directory for the answer keys")
    ap.add_argument("--step", type=int, default=0, choices=sorted(STEPS))
    ap.add_argument("--history", type=Path, help="rebuild the main fixture's git history here")
    args = ap.parse_args()

    if args.history:
        history(args.history.resolve())
        return
    for repo in (build_main(args.step), build_admin(args.step)):
        tree = args.out / repo.name if args.out else None
        key = args.key / f"step-{args.step}" / repo.name if args.key else None
        write(repo, tree, key)
        roles: dict[str, int] = {}
        for spec in repo.files.values():
            roles[spec.role] = roles.get(spec.role, 0) + 1
        print(f"{repo.name} step {args.step}: {len(repo.files)} files, {len(repo.edges)} edges, "
              f"{len(repo.symbols)} symbols; roles {dict(sorted(roles.items()))}")


if __name__ == "__main__":
    main()
