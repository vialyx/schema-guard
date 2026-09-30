"""Destructive or breaking DDL: forbidden on ledger/audit tables, reviewed elsewhere."""

from __future__ import annotations

from sg.core.models import Context, Finding, Migration, Status
from sg.core.sql import bare, evidence, finding, new_tables, touches
from sg.rules.base import Rule, Statement

_WHAT = {
    "drop_column": "drops column {table}.{column}",
    "drop_table": "drops table {table}",
    "truncate": "truncates {table}",
    "alter_type": "changes the type of {table}.{column}",
    "rename_column": "renames column {table}.{column}",
    "rename_table": "renames table {table}",
}

PROTECTED_FIX = (
    "expand/contract: add the new column (or table), dual-write, backfill, switch reads, "
    "then drop the old one in a later contract migration after sign-off; ledger history is kept"
)
OTHER_FIX = "ship as a contract migration after callers stop using it; confirm with the owners"


class DestructiveOnLedger(Rule):
    id = "destructive-change"
    default_severity = Status.REFUSE
    description = "DROP / TRUNCATE / ALTER TYPE / RENAME: refused on ledger and audit tables, reviewed elsewhere."

    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]:
        created = new_tables(statements)
        out: list[Finding] = []
        for stmt in statements:
            for t in touches([stmt]):
                if t.op not in _WHAT or bare(t.table) in created:
                    continue
                cls = ctx.policy.table_class(t.table)
                if cls == "scratch":
                    continue
                what = _WHAT[t.op].format(table=t.table, column=t.column)
                if ctx.policy.is_protected(t.table):
                    out.append(finding(
                        self, Status.REFUSE,
                        f"Migration {what}, a {cls} table: history must stay readable and callers break",
                        evidence(migration, stmt), PROTECTED_FIX,
                    ))
                else:
                    out.append(finding(
                        self, Status.ASK,
                        f"Migration {what} ({cls} table); running code may still read or write it",
                        evidence(migration, stmt), OTHER_FIX,
                    ))
        return out
