"""Shared data types. Everything that crosses a module boundary lives here."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sg.adapters.base import MigrationAdapter
    from sg.core.policy import Policy
    from sg.dialects.base import Dialect


class Status(str, Enum):
    """Outcome of a single check or finding, ordered by severity."""

    PASS = "pass"
    WARN = "warn"
    SKIPPED = "skipped"  # a check could not run; never allows GO
    ASK = "ask"  # a human must answer a question or review
    REFUSE = "refuse"  # unsafe; do not proceed as written

    @property
    def rank(self) -> int:
        return _STATUS_RANK[self]

    @classmethod
    def worst(cls, statuses: list["Status"]) -> "Status":
        return max(statuses, key=lambda s: s.rank, default=cls.PASS)


_STATUS_RANK = {Status.PASS: 0, Status.WARN: 1, Status.SKIPPED: 2, Status.ASK: 3, Status.REFUSE: 4}


class Verdict(str, Enum):
    GO = "GO"
    NEEDS_HUMAN = "NEEDS-HUMAN"
    REFUSED = "REFUSED"

    @property
    def rank(self) -> int:
        return [Verdict.GO, Verdict.NEEDS_HUMAN, Verdict.REFUSED].index(self)

    @classmethod
    def from_status(cls, status: Status) -> "Verdict":
        if status is Status.REFUSE:
            return cls.REFUSED
        if status in (Status.ASK, Status.SKIPPED):
            return cls.NEEDS_HUMAN
        return cls.GO


@dataclass
class Finding:
    rule: str  # stable id, e.g. "float-on-money"; used by evals and suppressions
    severity: Status
    message: str
    evidence: str = ""  # "path:line" or a SQL snippet
    fix: str = ""  # the safe alternative, always required for REFUSE

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class CheckResult:
    name: str
    status: Status
    summary: str = ""
    findings: list[Finding] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "summary": self.summary,
            "findings": [f.to_dict() for f in self.findings],
            "details": self.details,
        }

    @classmethod
    def skipped(cls, name: str, reason: str) -> "CheckResult":
        return cls(name=name, status=Status.SKIPPED, summary=reason)


@dataclass
class Migration:
    id: str  # adapter-specific revision id, e.g. "0004" or "V4__add_index"
    path: Path
    parent: str | None  # previous revision id, None for the root
    applied: bool = False  # present on the protected base ref (i.e. shipped)
    modified: bool = False  # applied, but its file differs from the base ref


@dataclass
class Context:
    """Everything a check or rule may need. Built once per `sg check` run."""

    repo_root: Path
    service_root: Path
    policy: "Policy"
    adapter: "MigrationAdapter"
    dialect: "Dialect"
    base_ref: str
    migrations: list[Migration]
    tools: dict[str, bool]
    # Offline SQL for each pending migration, keyed by migration id.
    pending_sql: dict[str, str] = field(default_factory=dict)
    # Lazily started throwaway database server (see Dialect.ephemeral_server).
    _db_server: Any = None

    service: Any = None  # ServiceConfig from the policy

    @property
    def pending(self) -> list[Migration]:
        """Not yet shipped: what this change adds."""
        return [m for m in self.migrations if not m.applied]

    @property
    def changed(self) -> list[Migration]:
        """Pending plus shipped-but-edited (the latter is always a finding)."""
        return [m for m in self.migrations if not m.applied or m.modified]

    @property
    def base_revision(self) -> str | None:
        """Last applied revision: what production looks like today."""
        applied = [m for m in self.migrations if m.applied and not m.modified]
        return applied[-1].id if applied else None

    def db_server(self):
        if self._db_server is None:
            self._db_server = self.dialect.ephemeral_server()
        return self._db_server

    def close(self) -> None:
        if self._db_server is not None:
            self._db_server.stop()
            self._db_server = None
