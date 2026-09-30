"""Posted ledger/audit rows are immutable: corrections are new entries."""

from __future__ import annotations

from sg.core.models import Context, Finding, Migration, Status
from sg.core.sql import bare, evidence, finding, new_tables, touches
from sg.rules.base import Rule, Statement

FIX = "ledger rows are append-only; post a compensating/reversing entry instead"


class MutatePostedRows(Rule):
    id = "mutate-ledger-rows"
    default_severity = Status.REFUSE
    description = "UPDATE or DELETE of rows in a ledger or audit table."

    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]:
        created = new_tables(statements)
        out: list[Finding] = []
        for stmt in statements:
            for t in touches([stmt]):
                if t.op not in ("update", "delete") or bare(t.table) in created:
                    continue
                if ctx.policy.is_protected(t.table):
                    verb = "rewrites" if t.op == "update" else "deletes"
                    out.append(finding(
                        self, Status.REFUSE,
                        f"Migration {verb} posted rows in {t.table} ({ctx.policy.table_class(t.table)} table)",
                        evidence(migration, stmt), FIX,
                    ))
        return out
