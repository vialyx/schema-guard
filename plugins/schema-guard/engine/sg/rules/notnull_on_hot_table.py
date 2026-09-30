"""NOT NULL on an existing table either fails on existing rows or scans under a lock."""

from __future__ import annotations

import re

from sg.core.models import Context, Finding, Migration, Status
from sg.core.sql import alter_table_actions, bare, evidence, finding, large_or_unknown, new_tables
from sg.rules.base import Rule, Statement

FIX = (
    "add CHECK (col IS NOT NULL) NOT VALID, VALIDATE CONSTRAINT separately, "
    "then SET NOT NULL (PG12+ skips the scan)"
)
ADD_FIX = (
    "add the column nullable (or with a constant DEFAULT), backfill in batches, then " + FIX
)

_NOT_NULL = re.compile(r"\bNOT\s+NULL\b|\bPRIMARY\s+KEY\b", re.I)
# A DEFAULT or an identity/generated column fills existing rows, so NOT NULL holds.
_FILLED = re.compile(r"\bDEFAULT\b|\bGENERATED\b|\b(?:BIG|SMALL)?SERIAL\d?\b", re.I)


class NotNullOnHotTable(Rule):
    id = "not-null-on-existing-table"
    default_severity = Status.ASK
    description = "NOT NULL added to an existing table without the CHECK ... NOT VALID dance."

    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]:
        created = new_tables(statements)
        out: list[Finding] = []
        for stmt in statements:
            for action in alter_table_actions(stmt.sql):
                t = action.touch
                if bare(t.table) in created:
                    continue
                if t.op == "add_column" and _NOT_NULL.search(action.text) and not _FILLED.search(action.text):
                    out.append(finding(
                        self, Status.ASK,
                        f"{t.table}.{t.column} is added NOT NULL without a DEFAULT: fails if {t.table} "
                        "has rows (or needs a rewrite to fill them)",
                        evidence(migration, stmt), ADD_FIX,
                    ))
                elif t.op == "set_not_null":
                    risky = large_or_unknown(ctx.policy, t.table)
                    size = _size(ctx, t.table)
                    out.append(finding(
                        self, Status.ASK if risky else Status.WARN,
                        f"SET NOT NULL on {t.table}.{t.column} scans {size} under ACCESS EXCLUSIVE",
                        evidence(migration, stmt), FIX,
                    ))
        return out


def _size(ctx: Context, table: str) -> str:
    rows = ctx.policy.est_rows(table)
    return f"~{rows:,} rows" if rows is not None else "a table of unknown size"
