"""Money and quantities must never be stored in binary floating point."""

from __future__ import annotations

from sg.core.models import Context, Finding, Migration, Status
from sg.core.sql import alter_table_actions, column_type, create_table_columns, evidence, finding
from sg.rules.base import Rule, Statement

DEFAULT_INEXACT = frozenset({"real", "float", "float4", "float8", "double precision", "double"})

FIX = "use NUMERIC(p,s) (quantities) or BIGINT minor units, e.g. *_cents (money)"


class FloatOnMoney(Rule):
    id = "float-on-money"
    default_severity = Status.REFUSE
    description = "Inexact numeric type (float/real/double) on a money or quantity column."

    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]:
        inexact = getattr(ctx.dialect, "inexact_numeric_types", None) or DEFAULT_INEXACT
        out: list[Finding] = []
        for stmt in statements:
            for table, column, typ in _declared_columns(stmt.sql):
                if typ is None or " ".join(typ.split()) not in inexact:
                    continue
                kind = ctx.policy.column_kind(table, column)
                if kind in ("money", "quantity"):
                    out.append(finding(
                        self, Status.REFUSE,
                        f"{table}.{column} holds {kind} but is declared {typ}; binary floats round "
                        "(0.1 + 0.2 != 0.3) and totals drift",
                        evidence(migration, stmt), FIX,
                    ))
                else:
                    out.append(finding(
                        self, Status.WARN,
                        f"{table}.{column} is declared {typ} (inexact); fine for measurements, "
                        "never for amounts or counts",
                        evidence(migration, stmt), FIX,
                    ))
        return out


def _declared_columns(sql: str) -> list[tuple[str, str, str | None]]:
    """(table, column, type) for every column this statement declares or retypes."""
    out: list[tuple[str, str, str | None]] = []
    for action in alter_table_actions(sql):
        t = action.touch
        if t.op in ("add_column", "alter_type") and t.column:
            out.append((t.table, t.column, column_type(action.text, t.column)))
    if created := create_table_columns(sql):
        table, cols = created
        out.extend((table, col, typ) for col, typ in cols)
    return out
