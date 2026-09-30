"""Discovery of adapters, dialects, checks and rules via Python entry points.

Built-ins are declared in pyproject.toml; external packages can add their own
under the same groups, so extending schema-guard never requires editing the core.
"""

from __future__ import annotations

from functools import cache
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sg.adapters.base import MigrationAdapter
    from sg.checks.base import Check
    from sg.dialects.base import Dialect
    from sg.rules.base import Rule

GROUPS = {
    "adapters": "schema_guard.adapters",
    "dialects": "schema_guard.dialects",
    "checks": "schema_guard.checks",
    "rules": "schema_guard.rules",
}


@cache
def _load(kind: str) -> dict[str, type]:
    loaded: dict[str, type] = {}
    for ep in entry_points(group=GROUPS[kind]):
        loaded[ep.name] = ep.load()
    if not loaded:
        raise RuntimeError(
            f"No {kind} registered. Is schema-guard installed (uv sync / pip install -e)?"
        )
    return loaded


def adapters() -> dict[str, type["MigrationAdapter"]]:
    return _load("adapters")


def dialects() -> dict[str, type["Dialect"]]:
    return _load("dialects")


def checks() -> dict[str, type["Check"]]:
    return _load("checks")


def rules() -> dict[str, type["Rule"]]:
    return _load("rules")


def detect_adapter(service_root: Path, preferred: str | None = None) -> "MigrationAdapter":
    available = adapters()
    if preferred and preferred != "auto":
        if preferred not in available:
            raise ValueError(f"adapter '{preferred}' not registered; have {sorted(available)}")
        return available[preferred](service_root)
    matches = [cls for cls in available.values() if cls.detect(service_root)]
    if len(matches) != 1:
        names = [c.name for c in matches] or "none"
        raise ValueError(
            f"could not pick a migration adapter for {service_root} (matched: {names}); "
            "set `adapter:` for this service in schema-guard.yaml"
        )
    return matches[0](service_root)
