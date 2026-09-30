"""Index builds and constraint validation that block writes on existing tables."""

from __future__ import annotations

import re

from sg.core.models import Context, Finding, Migration, Status
from sg.core.sql import alter_table_actions, bare, evidence, finding, large_or_unknown, new_tables, touches
from sg.rules.base import Rule, Statement

INDEX_FIX = (
    "CREATE INDEX CONCURRENTLY outside a transaction "
    "(Alembic: `with op.get_context().autocommit_block():`)"
)
UNIQUE_FIX = (
    "CREATE UNIQUE INDEX CONCURRENTLY outside a transaction "
    "(Alembic: `with op.get_context().autocommit_block():`), then ADD CONSTRAINT ... USING INDEX"
)
VALIDATE_FIX = (
    "ADD CONSTRAINT ... NOT VALID, then VALIDATE CONSTRAINT in a separate transaction "
    "(it only takes a SHARE UPDATE EXCLUSIVE lock)"
)

_CONCURRENTLY = re.compile(r"\bINDEX\s+CONCURRENTLY\b", re.I)
_VALIDATING = re.compile(r"^ADD (?:CONSTRAINT \S+ )?(FOREIGN KEY|CHECK)\b", re.I)
_INDEX_BACKED = re.compile(r"^ADD (?:CONSTRAINT \S+ )?(UNIQUE|PRIMARY KEY)\b(?!.*\bUSING INDEX\b)", re.I)
_NOT_VALID = re.compile(r"\bNOT VALID\b", re.I)


class IndexNotConcurrent(Rule):
    id = "index-not-concurrent"
    default_severity = Status.ASK
    description = "CREATE INDEX without CONCURRENTLY, or a validating constraint, on an existing table."

    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]:
        created = new_tables(statements)
        out: list[Finding] = []

        def add(table: str, message: str, stmt: Statement, fix: str) -> None:
            risky = large_or_unknown(ctx.policy, table)
            out.append(finding(
                self, Status.ASK if risky else Status.WARN,
                f"{message} ({_size(ctx, table)})", evidence(migration, stmt), fix,
            ))

        for stmt in statements:
            for t in touches([stmt]):
                if t.op == "create_index" and bare(t.table) not in created and not _CONCURRENTLY.search(stmt.sql):
                    add(t.table, f"CREATE INDEX on {t.table} without CONCURRENTLY blocks writes for the whole build",
                        stmt, INDEX_FIX)
            for action in alter_table_actions(stmt.sql):
                table = action.touch.table
                if bare(table) in created:
                    continue
                if (m := _VALIDATING.match(action.text)) and not _NOT_VALID.search(action.text):
                    add(table, f"ADD {m.group(1).upper()} on {table} without NOT VALID scans every row "
                               "while holding a lock that blocks writes", stmt, VALIDATE_FIX)
                elif m := _INDEX_BACKED.match(action.text):
                    add(table, f"ADD {m.group(1).upper()} on {table} builds an index non-concurrently "
                               "and blocks writes meanwhile", stmt, UNIQUE_FIX)
        return out


def _size(ctx: Context, table: str) -> str:
    rows = ctx.policy.est_rows(table)
    if ctx.policy.is_hot(table):
        return "hot table" + (f", ~{rows:,} rows" if rows is not None else "")
    return f"~{rows:,} rows" if rows is not None else "size unknown"
