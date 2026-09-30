"""Check contract. A check turns a Context into exactly one CheckResult."""

from __future__ import annotations

from abc import ABC, abstractmethod

from sg.core.models import CheckResult, Context


class Check(ABC):
    name: str = ""
    #: capabilities from Context.tools that must be true, e.g. {"db", "squawk"}.
    #: Missing ones make the check SKIPPED (which blocks GO) instead of failing.
    requires: frozenset[str] = frozenset()
    #: run order; cheap static checks first
    order: int = 50

    def run_safely(self, ctx: Context) -> CheckResult:
        missing = sorted(r for r in self.requires if not ctx.tools.get(r))
        if missing:
            return CheckResult.skipped(self.name, f"unavailable: {', '.join(missing)}")
        if not ctx.changed:
            return CheckResult.skipped(self.name, "no new or changed migrations to check")
        try:
            return self.run(ctx)
        except Exception as exc:  # a crashing check must never look like a pass
            return CheckResult.skipped(self.name, f"check crashed: {type(exc).__name__}: {exc}")

    @abstractmethod
    def run(self, ctx: Context) -> CheckResult: ...
