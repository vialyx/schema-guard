"""Find services (directories with migrations) so a repo can run with zero config."""

from __future__ import annotations

from pathlib import Path

from sg.core import registry
from sg.core.policy import ServiceConfig

SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "vendor", "dist", "build", "target", "__pycache__",
             ".schema-guard", ".tox", ".mypy_cache", ".pytest_cache"}
MAX_DEPTH = 5


def discover_services(repo_root: Path) -> list[ServiceConfig]:
    """Every directory that exactly one adapter recognises, outermost first; nested matches are skipped."""
    found: list[ServiceConfig] = []
    adapters = registry.adapters()

    root = repo_root.resolve()

    def walk(d: Path, depth: int) -> None:
        matches = [name for name, cls in adapters.items() if _detects(cls, d)]
        if len(matches) == 1:
            rel = d.relative_to(root).as_posix() or "."
            found.append(ServiceConfig(path=rel, adapter=matches[0]))
            return
        if depth >= MAX_DEPTH:
            return
        try:
            children = sorted(p for p in d.iterdir() if p.is_dir() and p.name not in SKIP_DIRS
                              and not p.name.startswith("."))
        except OSError:
            return
        for child in children:
            walk(child, depth + 1)

    walk(root, 0)
    return found


def _detects(cls, d: Path) -> bool:
    try:
        return bool(cls.detect(d))
    except Exception:
        return False
