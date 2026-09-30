"""Org/repo policy: which tables matter, which columns hold money, how strict to be.

Resolution order (later wins): engine defaults -> $SG_ORG_POLICY -> the repo file's
`extends:` -> <repo>/schema-guard.yaml. An org layer may list dotted keys under
`locked:` (e.g. `columns`, `checks.roundtrip`); later layers cannot override them.
Org layers can be a local path or an https:// URL (cached for offline runs).
Everything the checks treat as "business knowledge" lives here, not in code or in the prompt.
"""

from __future__ import annotations

import hashlib
import os
import urllib.request
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


class PolicyError(RuntimeError):
    pass


def _read_layer(ref: str, base_dir: Path) -> tuple[dict, str]:
    """(data, source label) for an org policy given as an https URL or a path."""
    if ref.startswith(("https://", "http://")):
        cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "schema-guard"
        cached = cache / (hashlib.sha256(ref.encode()).hexdigest()[:16] + ".yaml")
        try:
            with urllib.request.urlopen(ref, timeout=10) as resp:  # noqa: S310 (org-configured URL)
                text = resp.read().decode()
            cache.mkdir(parents=True, exist_ok=True)
            cached.write_text(text)
        except OSError as exc:
            if not cached.exists():
                raise PolicyError(f"org policy {ref} unreachable and not cached: {exc}") from exc
            text = cached.read_text()
        return yaml.safe_load(text) or {}, ref
    path = Path(ref).expanduser()
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        raise PolicyError(f"org policy file not found: {path}")
    return yaml.safe_load(path.read_text()) or {}, str(path)


def _get(data: dict, dotted: str) -> tuple[bool, Any]:
    cur: Any = data
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False, None
        cur = cur[part]
    return True, cur


def _set(data: dict, dotted: str, value: Any) -> None:
    *parents, last = dotted.split(".")
    cur = data
    for part in parents:
        cur = cur.setdefault(part, {})
    cur[last] = value


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
    python: str | None = None  # command for the service's Python; see core/pyenv.py
    migrations_dir: str | None = None  # plain-SQL only: where the V<n>__*.sql files live


@dataclass
class Policy:
    raw: dict[str, Any]
    source: list[str] = field(default_factory=list)

    notes: list[str] = field(default_factory=list)  # e.g. overrides of locked keys that were ignored

    @classmethod
    def load(cls, repo_root: Path, repo_text: str | None = None, repo_source: str | None = None) -> "Policy":
        """Merge defaults, org layers and the repo file.

        `repo_text` replaces the working-copy file: the checks use the base branch's
        policy, so a change cannot loosen the rules it is judged by.
        """
        data = yaml.safe_load(DEFAULTS.read_text()) or {}
        sources = [str(DEFAULTS)]
        locked: list[str] = []
        notes: list[str] = []

        repo_file = repo_root / POLICY_FILE
        if repo_text is None and repo_file.exists():
            repo_text, repo_source = repo_file.read_text(), str(repo_file)
        repo_data = (yaml.safe_load(repo_text) or {}) if repo_text is not None else {}

        org_refs = [r for r in (os.environ.get("SG_ORG_POLICY"), repo_data.pop("extends", None)) if r]
        for ref in org_refs:
            layer, label = _read_layer(ref, repo_root)
            data = _deep_merge(data, layer)
            locked += layer.get("locked") or []
            sources.append(label)

        if repo_text is not None:
            pinned = {k: _get(data, k) for k in locked}
            data = _deep_merge(data, repo_data)
            for key, (present, value) in pinned.items():
                if present and _get(data, key) != (True, value):
                    notes.append(f"`{key}` is locked by the org policy; the repo override was ignored")
                    _set(data, key, value)
            sources.append(repo_source or POLICY_FILE)
        data.pop("locked", None)
        return cls(raw=data, source=sources, notes=notes)

    def with_services(self, discovered: list["ServiceConfig"]) -> "Policy":
        """Use auto-discovered services when the policy names none."""
        if not self.raw.get("services") and discovered:
            self.raw["services"] = [vars(s) for s in discovered]
        return self

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
