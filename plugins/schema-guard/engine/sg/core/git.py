"""What has shipped? A migration is "applied" if its file exists on the base ref.

Using git (not a production DB) keeps the answer deterministic, offline and
safe: the skill never needs production credentials.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def is_repo(repo: Path) -> bool:
    return _git(repo, "rev-parse", "--is-inside-work-tree").returncode == 0


def ref_exists(repo: Path, ref: str) -> bool:
    return _git(repo, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").returncode == 0


def git_root(repo: Path) -> Path | None:
    r = _git(repo, "rev-parse", "--show-toplevel")
    return Path(r.stdout.strip()) if r.returncode == 0 else None


def file_at_ref(repo: Path, ref: str, path: Path) -> str | None:
    """Contents of `path` at `ref`, or None if it does not exist there."""
    root = git_root(repo)
    if root is None:
        return None
    rel = path.resolve().relative_to(root.resolve())
    r = _git(root, "show", f"{ref}:{rel.as_posix()}")
    return r.stdout if r.returncode == 0 else None


def shipped_state(repo: Path, ref: str, path: Path) -> tuple[bool, bool]:
    """(applied, modified) for a migration file relative to the base ref."""
    if not ref_exists(repo, ref):
        return False, False
    content = file_at_ref(repo, ref, path)
    if content is None:
        return False, False
    current = path.read_text() if path.exists() else ""
    return True, content != current
