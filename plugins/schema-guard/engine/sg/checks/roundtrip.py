"""Up -> down -> up on a throwaway database.

Proves three things the LLM cannot prove by reading code:
  1. the migration applies at all on today's schema;
  2. the downgrade restores exactly the schema we started from (rollback works);
  3. re-applying is deterministic (same schema twice).
"""

from __future__ import annotations

from sg.checks.base import Check
from sg.core.models import CheckResult, Context, Finding, Status


def diff_snapshots(a: dict, b: dict, limit: int = 6) -> list[str]:
    """Human-readable differences between two schema snapshots."""
    out: list[str] = []
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            out.append(f"{key}: {str(a.get(key))[:160]} != {str(b.get(key))[:160]}")
        if len(out) >= limit:
            break
    return out


class RoundtripCheck(Check):
    name = "roundtrip"
    requires = frozenset({"db"})
    order = 30

    def run(self, ctx: Context) -> CheckResult:
        require_down = ctx.policy.check_config(self.name).get(
            "require_downgrade", getattr(ctx.adapter, "requires_downgrade", True))
        dsn = ctx.db_server().create_database("roundtrip")
        base = ctx.base_revision
        a, d = ctx.adapter, ctx.dialect

        if base:
            a.upgrade(dsn, base)
        before = d.schema_snapshot(dsn)

        try:
            a.upgrade(dsn, "head")
        except Exception as exc:
            return CheckResult(self.name, Status.REFUSE, "upgrade fails on the current schema", [Finding(
                "migration-fails", Status.REFUSE, "upgrade to head failed on a database at the base revision",
                evidence=_tail(exc), fix="fix the migration; it cannot ship as written",
            )])
        after = d.schema_snapshot(dsn)

        findings: list[Finding] = []
        try:
            a.downgrade(dsn, base or "base")
            restored = d.schema_snapshot(dsn)
            if restored != before:
                findings.append(Finding(
                    "downgrade-not-exact", Status.ASK, "downgrade does not restore the original schema",
                    evidence="; ".join(diff_snapshots(before, restored)),
                    fix="make downgrade() the exact inverse of upgrade(), or document why it cannot be",
                ))
        except Exception as exc:
            findings.append(Finding(
                "downgrade-fails", Status.ASK if require_down else Status.WARN,
                "downgrade failed", evidence=_tail(exc),
                fix="implement a working downgrade (rollback path) or get explicit sign-off for a forward-only migration",
            ))
            status = Status.worst([f.severity for f in findings])
            return CheckResult(self.name, status, "downgrade failed", findings)

        try:
            a.upgrade(dsn, "head")
            again = d.schema_snapshot(dsn)
            if again != after:
                findings.append(Finding(
                    "reapply-differs", Status.ASK, "re-applying after a downgrade produced a different schema",
                    evidence="; ".join(diff_snapshots(after, again)),
                    fix="make the migration deterministic (no environment-dependent DDL)",
                ))
        except Exception as exc:
            findings.append(Finding(
                "reapply-fails", Status.ASK, "upgrade failed after downgrade (downgrade leaves residue)",
                evidence=_tail(exc), fix="downgrade must remove everything upgrade creates (types, indexes, constraints)",
            ))

        status = Status.worst([f.severity for f in findings]) if findings else Status.PASS
        summary = "up/down/up clean; schema restored exactly" if not findings else f"{len(findings)} problem(s)"
        return CheckResult(self.name, status, summary, findings, {"base_revision": base})


def _tail(exc: Exception, n: int = 400) -> str:
    s = str(exc).strip()
    return s[-n:]
