"""Policy rule contract. Rules are small, pure and individually configurable.

A rule sees the parsed statements of one pending migration plus the Context,
and returns findings. Rules never touch a database.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

import sqlglot.expressions as exp

from sg.core.models import Context, Finding, Migration, Status


@dataclass
class Statement:
    sql: str  # original statement text
    tree: exp.Expression | None  # None if sqlglot could not parse it
    index: int  # position in the migration


class Rule(ABC):
    #: stable id used in findings, evals and `rules:` config, e.g. "float-on-money"
    id: str = ""
    default_severity: Status = Status.REFUSE
    description: str = ""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        sev = self.config.get("severity")
        self.severity = Status(sev) if sev else self.default_severity

    @property
    def enabled(self) -> bool:
        return self.config.get("enabled", True)

    @abstractmethod
    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]: ...

    def finding(self, message: str, evidence: str = "", fix: str = "") -> Finding:
        return Finding(rule=self.id, severity=self.severity, message=message, evidence=evidence, fix=fix)
