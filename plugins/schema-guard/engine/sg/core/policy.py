"""Org/repo policy: which tables matter, which columns hold money, how strict to be.

Resolution order (later wins): engine defaults -> $SG_ORG_POLICY (org-wide file)
-> <repo>/schema-guard.yaml. Everything the checks treat as "business knowledge"
lives here, not in code or in the prompt.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import yaml

POLICY_FILE = "schema-guard.yaml"
DEFAULTS = Path(__file__).resolve().parent.parent / "defaults.yaml"

TABLE_CLASSES = ("ledger", "audit", "hot", "core", "scratch")


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def find_repo_root(start: Path) -> Path:
    """Nearest ancestor with a schema-guard.yaml, else the git root, else `start`."""
    start = start.resolve()
    for p in [start, *start.parents]:
        if (p / POLICY_FILE).exists():
            return p
    for p in [start, *start.parents]:
        if (p / ".git").exists():
            return p
    return start


@dataclass
class ServiceConfig:
    path: str
    adapter: str = "auto"
    seed: str | None = None
    tests: str | None = None


@dataclass
class Policy:
    raw: dict[str, Any]
    source: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, repo_root: Path) -> "Policy":
        data = yaml.safe_load(DEFAULTS.read_text()) or {}
        sources = [str(DEFAULTS)]
        org = os.environ.get("SG_ORG_POLICY")
        if org and Path(org).exists():
            data = _deep_merge(data, yaml.safe_load(Path(org).read_text()) or {})
            sources.append(org)
        repo_file = repo_root / POLICY_FILE
        if repo_file.exists():
            data = _deep_merge(data, yaml.safe_load(repo_file.read_text()) or {})
            sources.append(str(repo_file))
        return cls(raw=data, source=sources)

    # --- services -----------------------------------------------------------
    @property
    def services(self) -> list[ServiceConfig]:
        return [ServiceConfig(**s) for s in self.raw.get("services") or [{"path": "."}]]

    def service_for(self, repo_root: Path, path: Path) -> ServiceConfig:
        """Service whose directory contains `path` (longest match wins)."""
        path = path.resolve()
        best: ServiceConfig | None = None
        for svc in self.services:
            root = (repo_root / svc.path).resolve()
            if path == root or root in path.parents:
                if best is None or len(svc.path) > len(best.path):
                    best = svc
        return best or self.services[0]

    @property
    def dialect(self) -> str:
        return self.raw.get("dialect", "postgres")

    # --- tables & columns ---------------------------------------------------
    def table_class(self, table: str) -> str:
        entry = (self.raw.get("tables") or {}).get(_bare(table)) or {}
        return entry.get("class", "unknown")

    def est_rows(self, table: str) -> int | None:
        entry = (self.raw.get("tables") or {}).get(_bare(table)) or {}
        return entry.get("est_rows")

    def is_hot(self, table: str) -> bool:
        if self.table_class(table) == "hot":
            return True
        rows = self.est_rows(table)
        return rows is not None and rows >= int(self.raw.get("hot_row_threshold", 1_000_000))

    def is_protected(self, table: str) -> bool:
        """Ledger/audit tables: append-only, destructive changes need a contract phase."""
        return self.table_class(table) in ("ledger", "audit")

    def column_kind(self, table: str, column: str) -> str | None:
        """'money' | 'quantity' | None, matched against globs like '*amount*' or 'orders.total'."""
        table, column = _bare(table).lower(), column.lower()
        for kind, patterns in (self.raw.get("columns") or {}).items():
            for pat in patterns or []:
                pat = pat.lower()
                target = f"{table}.{column}" if "." in pat else column
                if fnmatch(target, pat):
                    return kind
        return None

    # --- rules & checks -----------------------------------------------------
    def rule_config(self, rule_name: str) -> dict[str, Any]:
        return (self.raw.get("rules") or {}).get(rule_name) or {}

    def check_config(self, check_name: str) -> dict[str, Any]:
        return (self.raw.get("checks") or {}).get(check_name) or {}

    def invariants_for(self, service_path: str) -> list[dict[str, Any]]:
        """Invariants scoped to this service, plus unscoped ones."""
        return [
            inv for inv in self.raw.get("invariants") or []
            if inv.get("service") in (None, service_path)
        ]

    @property
    def base_ref(self) -> str:
        return os.environ.get("SG_BASE_REF") or self.raw.get("base_ref", "main")


def _bare(table: str) -> str:
    """Strip schema qualifier and quotes: public."Orders" -> Orders."""
    return table.split(".")[-1].strip('"')
