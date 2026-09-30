"""Business invariants on realistic seed data, before and after the migration.

The org declares invariants as plain SQL in schema-guard.yaml, e.g. "ledger
balance per account is unchanged" or "no orphaned movements". This is where a
migration that silently rewrites money or drops rows gets caught.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, text

from sg.checks.base import Check
from sg.core.models import CheckResult, Context, Finding, Status


def run_sql_file(dsn: str, sql: str) -> None:
    engine = create_engine(dsn)
    try:
        with engine.begin() as conn:
            conn.exec_driver_sql(sql)
    finally:
        engine.dispose()


def evaluate(dsn: str, invariants: list[dict[str, Any]]) -> dict[str, Any]:
    results: dict[str, Any] = {}
    engine = create_engine(dsn)
    try:
        with engine.connect() as conn:
            for inv in invariants:
                try:
                    rows = conn.execute(text(inv["sql"])).fetchall()
                    results[inv["name"]] = [tuple(str(v) for v in r) for r in rows]
                except Exception as exc:
                    conn.rollback()
                    results[inv["name"]] = f"ERROR: {str(exc).splitlines()[0]}"
    finally:
        engine.dispose()
    return results


class InvariantsCheck(Check):
    name = "invariants"
    requires = frozenset({"db"})
    order = 40

    def run(self, ctx: Context) -> CheckResult:
        invariants = ctx.policy.invariants_for(ctx.service.path)
        seed_rel = getattr(ctx.service, "seed", None)
        if not invariants:
            return CheckResult(self.name, Status.WARN, "no invariants declared in schema-guard.yaml")
        if not seed_rel:
            return CheckResult.skipped(self.name, "no seed data configured for this service")
        if not ctx.base_revision:
            return CheckResult.skipped(self.name, "no shipped revision to seed against")
        seed = (ctx.service_root / seed_rel).read_text()

        dsn = ctx.db_server().create_database("invariants")
        ctx.adapter.upgrade(dsn, ctx.base_revision)
        run_sql_file(dsn, seed)
        before = evaluate(dsn, invariants)

        try:
            ctx.adapter.upgrade(dsn, "head")
        except Exception as exc:
            return CheckResult(self.name, Status.REFUSE, "migration fails on realistic data", [Finding(
                "fails-on-data", Status.REFUSE,
                "upgrade succeeds on an empty schema but fails once real rows exist",
                evidence=str(exc).strip()[-400:],
                fix="add a default/backfill step, or split into expand (nullable) + validate phases",
            )])
        after = evaluate(dsn, invariants)

        findings = self._compare(invariants, before, after, phase="after upgrade")

        # Idempotence: roll back and re-apply on the same data; business facts must not drift.
        try:
            ctx.adapter.downgrade(dsn, ctx.base_revision)
            ctx.adapter.upgrade(dsn, "head")
            again = evaluate(dsn, invariants)
            findings += self._compare(invariants, after, again, phase="after down+up (re-run)")
        except Exception as exc:
            findings.append(Finding(
                "rerun-not-possible", Status.WARN, "could not re-run the migration on seeded data",
                evidence=str(exc).strip()[-300:],
            ))

        status = Status.worst([f.severity for f in findings]) if findings else Status.PASS
        summary = f"{len(invariants)} invariant(s) hold before/after and on re-run" if not findings else f"{len(findings)} invariant problem(s)"
        return CheckResult(self.name, status, summary, findings, {"before": before, "after": after})

    @staticmethod
    def _compare(invariants, before, after, phase: str) -> list[Finding]:
        out: list[Finding] = []
        for inv in invariants:
            name, expect = inv["name"], inv.get("expect", "unchanged")
            b, a = before.get(name), after.get(name)
            if isinstance(a, str) and a.startswith("ERROR"):
                out.append(Finding("invariant-error", Status.ASK, f"invariant '{name}' cannot be evaluated {phase}", evidence=a,
                                   fix="the change removed/renamed something the invariant depends on; update the invariant in the same PR with owner sign-off"))
            elif expect == "unchanged" and a != b:
                out.append(Finding("invariant-changed", Status.REFUSE, f"invariant '{name}' changed {phase}",
                                   evidence=f"before={str(b)[:150]} after={str(a)[:150]}",
                                   fix="the migration alters existing business data; move data changes to a reviewed, reversible backfill"))
            elif expect == "zero" and a and a[0] and a[0][0] != "0":
                out.append(Finding("invariant-violated", Status.REFUSE, f"invariant '{name}' expected 0 {phase}",
                                   evidence=f"got {a[0][0]}", fix="the migration breaks referential integrity; fix before shipping"))
        return out
