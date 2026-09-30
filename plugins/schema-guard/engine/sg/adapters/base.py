"""Migration framework adapter contract.

One adapter per migration tool (Alembic, raw SQL/Flyway, Django, Prisma, Rails...).
The core never imports a concrete adapter; it goes through the registry.
Every adapter must pass tests/contract/test_adapter_contract.py.
"""

from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from pathlib import Path

from sg.core.models import Migration


class MigrationAdapter(ABC):
    #: registry key, also accepted as `adapter:` in schema-guard.yaml
    name: str = ""

    def __init__(self, service_root: Path):
        self.service_root = service_root
        #: argv prefix of the service's Python, for adapters that run the service's code
        self.python: list[str] = [sys.executable]

    @classmethod
    @abstractmethod
    def detect(cls, service_root: Path) -> bool:
        """True if this service uses the framework. Must be cheap and side-effect free."""

    @abstractmethod
    def list_migrations(self) -> list[Migration]:
        """All migrations in apply order, root first. `applied`/`modified` are filled by the core."""

    @abstractmethod
    def render_sql(self, migration_id: str) -> str:
        """Offline SQL for upgrading *only* this migration from its parent. No DB needed."""

    @abstractmethod
    def upgrade(self, dsn: str, target: str = "head") -> None:
        """Apply migrations up to `target` ("head" = latest). Raise on failure."""

    @abstractmethod
    def downgrade(self, dsn: str, target: str) -> None:
        """Revert to `target` (a revision id, or "base" for an empty schema). Raise on failure."""

    def migration_dirs(self) -> list[Path]:
        """Directories holding migration files; used by the edit guard hook."""
        return sorted({m.path.parent for m in self.list_migrations()})

    def conventions_hint(self) -> str:
        """Short, framework-specific guidance handed to the LLM before it writes a migration."""
        return ""

    def caller_patterns(self, table: str, column: str | None) -> list[str]:
        """Regexes (Python syntax) that find application code touching table/column.

        The core adds generic raw-SQL patterns on top; override to add ORM-specific ones.
        """
        return []
