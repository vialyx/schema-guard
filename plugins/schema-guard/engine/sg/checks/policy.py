"""Runs every enabled policy rule over the SQL of each new or changed migration."""

from __future__ import annotations

from sg.checks.base import Check
from sg.core import registry
from sg.core.models import CheckResult, Context, Finding, Status
from sg.core.sql import evidence, is_unparsed, parse, touches
from sg.rules.base import Rule

_ORDER = (Status.REFUSE, Status.ASK, Status.SKIPPED, Status.WARN)


class PolicyCheck(Check):
    name = "policy"
    order = 10

    def run(self, ctx: Context) -> CheckResult:
        rules = _enabled_rules(ctx)
        dialect = getattr(ctx.dialect, "sqlglot_dialect", "") or ctx.policy.dialect
        findings: list[Finding] = []
        n_statements = 0

        for m in ctx.changed:
            sql = ctx.pending_sql.get(m.id)
            if sql is None:
                # Never let "nothing to inspect" look like "nothing wrong".
                findings.append(Finding(
                    rule="unrendered-migration", severity=Status.ASK,
                    message=f"SQL for {m.path.name} was not rendered, so no policy rule inspected it",
                    evidence=m.path.name, fix="render it with `sg render` and review the SQL by hand",
                ))
                sql = ""
            statements = parse(sql, dialect)
            n_statements += len(statements)
            for rule in rules:
                findings.extend(rule.check(m, statements, ctx))
            for stmt in statements:
                # Rules fall back to regex; only flag what neither sqlglot nor the regexes understood.
                if is_unparsed(stmt) and not touches([stmt]):
                    findings.append(Finding(
                        rule="unparsed-statement", severity=Status.WARN,
                        message="statement could not be parsed, so no policy rule inspected it",
                        evidence=evidence(m, stmt), fix="review this statement by hand",
                    ))

        status = Status.worst([f.severity for f in findings])
        return CheckResult(
            name=self.name,
            status=status,
            summary=_summary(findings, len(ctx.changed)),
            findings=findings,
            details={"rules_run": [r.id for r in rules], "statements": n_statements},
        )


def _enabled_rules(ctx: Context) -> list[Rule]:
    out: list[Rule] = []
    for name, cls in registry.rules().items():
        # Config is keyed by entry-point name; accept the rule id too ("float-on-money").
        rule = cls(ctx.policy.rule_config(name) or ctx.policy.rule_config(cls.id))
        if rule.enabled:
            out.append(rule)
    return out


def _summary(findings: list[Finding], n_migrations: int) -> str:
    across = f"across {n_migrations} migration{'s' if n_migrations != 1 else ''}"
    counts = [(s, sum(f.severity is s for f in findings)) for s in _ORDER]
    parts = [f"{n} {s.value}" for s, n in counts if n]
    return f"{', '.join(parts) or 'no findings'} {across}"
