"""Test helpers: a lightweight Context with a stub adapter/dialect and a fixed policy."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import yaml

from sg.core.models import Context, Migration
from sg.core.policy import DEFAULTS, Policy

TABLES = {
    "orders": {"class": "core", "est_rows": 250_000},
    "inventory_movements": {"class": "hot", "est_rows": 40_000_000},
    "ledger_entries": {"class": "ledger"},
    "audit_log": {"class": "audit"},
    "order_import_staging": {"class": "scratch"},
}


def make_policy(**overrides: Any) -> Policy:
    defaults = yaml.safe_load(DEFAULTS.read_text())
    raw = {
        "dialect": "postgres",
        "hot_row_threshold": 1_000_000,
        "tables": TABLES,
        "columns": defaults["columns"],
        "rules": {},
        **overrides,
    }
    return Policy(raw=raw)


def make_ctx(
    sql: str | dict[str, str],
    *,
    modified: bool = False,
    policy: Policy | None = None,
    filename: str = "0042_change.sql",
) -> Context:
    """One pending migration (or one shipped-and-edited one if `modified`)."""
    scripts = sql if isinstance(sql, dict) else {"0042": sql}
    migrations = [
        Migration(id=mid, path=Path("migrations") / (filename if len(scripts) == 1 else f"{mid}.sql"),
                  parent=None, applied=modified, modified=modified)
        for mid in scripts
    ]
    dialect = SimpleNamespace(
        name="postgres",
        sqlglot_dialect="postgres",
        inexact_numeric_types=frozenset({"real", "float", "float4", "float8", "double precision", "double"}),
    )
    return Context(
        repo_root=Path("."), service_root=Path("."), policy=policy or make_policy(),
        adapter=SimpleNamespace(name="stub"), dialect=dialect, base_ref="main",
        migrations=migrations, tools={}, pending_sql=dict(scripts),
    )
