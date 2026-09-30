"""Large data changes inside a migration: one long transaction, one long lock."""

from __future__ import annotations

from sg.core.models import Context, Finding, Migration, Status
from sg.core.sql import bare, evidence, finding, is_bounded, new_tables, touches, where_clause
from sg.rules.base import Rule, Statement

FIX = "backfill in batches (e.g. by id ranges of 10k) outside the migration transaction, idempotently"


class UnbatchedBackfill(Rule):
    id = "unbatched-backfill"
    default_severity = Status.ASK
    description = "UPDATE/DELETE that is not batched on a hot table, or unscoped on any existing table."

    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]:
        created = new_tables(statements)
        policy = ctx.policy
        out: list[Finding] = []
        for stmt in statements:
            for t in touches([stmt]):
                if t.op not in ("update", "delete") or bare(t.table) in created:
                    continue
                # Ledger/audit rows are mutate-ledger-rows' business; scratch is disposable.
                if policy.is_protected(t.table) or policy.table_class(t.table) == "scratch":
                    continue
                verb = t.op.upper()
                if policy.is_hot(t.table):
                    if not is_bounded(stmt):
                        scope = "every row" if where_clause(stmt) is None else "an unbounded set of rows"
                        out.append(finding(
                            self, Status.ASK,
                            f"{verb} on hot table {t.table} touches {scope} in one transaction: "
                            "long row locks, replication lag and a huge rollback if it fails",
                            evidence(migration, stmt), FIX,
                        ))
                elif where_clause(stmt) is None:
                    # Deleting every row of a table someone relies on is data loss, not a style issue.
                    out.append(finding(
                        self, Status.WARN if t.op == "update" else Status.ASK,
                        f"{verb} without WHERE on {t.table} affects every row",
                        evidence(migration, stmt), FIX,
                    ))
        return out
