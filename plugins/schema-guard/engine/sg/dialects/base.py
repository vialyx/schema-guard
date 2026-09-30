"""Database engine contract. One file per engine (Postgres, MySQL, ...)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class EphemeralServer(ABC):
    """A throwaway database server that checks can create scratch databases on."""

    @abstractmethod
    def create_database(self, name: str) -> str:
        """Create an empty database and return its DSN (SQLAlchemy URL)."""

    @abstractmethod
    def stop(self) -> None: ...


class Dialect(ABC):
    name: str = ""
    #: sqlglot dialect used to parse rendered SQL
    sqlglot_dialect: str = ""
    #: lower-case type names that must never hold money/quantities
    inexact_numeric_types: frozenset[str] = frozenset()

    @classmethod
    @abstractmethod
    def available(cls) -> tuple[bool, str]:
        """Can we start an ephemeral server here? Returns (ok, how-or-why-not)."""

    @abstractmethod
    def ephemeral_server(self) -> EphemeralServer:
        """Start a throwaway server. Honours $SG_DATABASE_URL (an existing server) if set."""

    @abstractmethod
    def schema_snapshot(self, dsn: str) -> dict[str, Any]:
        """Normalised description of tables, columns, types, nullability, indexes, constraints.

        Two snapshots of equivalent schemas must compare equal (used by the round-trip check).
        """

    @abstractmethod
    def describe_tables(self, dsn: str, tables: list[str]) -> dict[str, Any]:
        """Human/LLM-readable DDL-ish description of the given tables."""

    def row_estimate(self, dsn: str, table: str) -> int | None:
        """Planner row estimate from a real (read-replica) DSN, if available."""
        return None
