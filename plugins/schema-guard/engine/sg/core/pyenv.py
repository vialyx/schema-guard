"""Which Python runs a service's own code (its tests, its Alembic env.py)?

The engine's interpreter only has the engine's dependencies, so a service that imports
its own packages from env.py or tests needs its own environment. Resolution order:
  1. `python:` for the service in schema-guard.yaml (a command, e.g. "uv run python");
  2. <service>/.venv/bin/python, then <repo>/.venv/bin/python;
  3. the engine's interpreter (fine for services with no extra dependencies).
"""

from __future__ import annotations

import shlex
import sys
from pathlib import Path


def service_python(service_root: Path, repo_root: Path, configured: str | None = None) -> list[str]:
    if configured:
        argv = shlex.split(configured)
        first = service_root / argv[0]
        if ("/" in argv[0]) and first.exists():
            argv[0] = str(first)
        return argv
    for root in (service_root, repo_root):
        venv = root / ".venv" / "bin" / "python"
        if venv.exists():
            return [str(venv)]
    return [sys.executable]
