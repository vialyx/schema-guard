"""A shipped migration is history: environments that ran it will never run the edit."""

from __future__ import annotations

from sg.core.models import Context, Finding, Migration, Status
from sg.core.sql import finding
from sg.rules.base import Rule, Statement

FIX = "never edit a shipped migration; add a new migration that corrects it"


class EditAppliedMigration(Rule):
    id = "edit-shipped-migration"
    default_severity = Status.REFUSE
    description = "A migration that already shipped (exists on the base ref) was modified."

    def check(self, migration: Migration, statements: list[Statement], ctx: Context) -> list[Finding]:
        if not migration.modified:
            return []
        return [finding(
            self, Status.REFUSE,
            f"{migration.path.name} already shipped and was edited; databases that applied it "
            "will silently diverge from new ones",
            f"{migration.path.name}: differs from {ctx.base_ref}",
            FIX,
        )]
