"""Squawk (Postgres migration linter) over the offline SQL of each new/changed migration.

Squawk knows lock semantics far better than a prompt does. We only decide which
of its rules are noise, which are warnings and which must stop the line
(`checks.squawk.ignore` / `checks.squawk.escalate` in the policy).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

from sg.checks.base import Check
from sg.core.models import CheckResult, Context, Finding, Status
from sg.core.sql import parse, touches


def squawk_cmd() -> list[str]:
    if shutil.which("squawk"):
        return ["squawk"]
    return ["npx", "-y", "squawk-cli"]


class SquawkCheck(Check):
    name = "squawk"
    requires = frozenset({"squawk"})
    order = 20

    def run(self, ctx: Context) -> CheckResult:
        if ctx.dialect.name != "postgres":
            return CheckResult.skipped(self.name, f"squawk only supports postgres, not {ctx.dialect.name}")
        cfg = ctx.policy.check_config(self.name)
        ignore = set(cfg.get("ignore") or [])
        escalate = {k: Status(v) for k, v in (cfg.get("escalate") or {}).items()}

        with tempfile.TemporaryDirectory() as tmp:
            file_to_mid: dict[str, str] = {}
            # (file, line) -> statement text, so evidence shows the statement, not a line number
            line_to_stmt: dict[str, list[tuple[int, str]]] = {}
            for mid, sql in ctx.pending_sql.items():
                f = Path(tmp) / f"{mid}.sql"
                body, starts = self._lintable(ctx, sql)
                f.write_text(body)
                file_to_mid[str(f)] = mid
                line_to_stmt[str(f)] = starts
            if not file_to_mid:
                return CheckResult.skipped(self.name, "nothing to lint")
            proc = subprocess.run(
                [*squawk_cmd(), "--reporter", "json", *file_to_mid],
                capture_output=True, text=True, timeout=180,
            )
        try:
            violations = json.loads(proc.stdout or "[]")
        except json.JSONDecodeError:
            return CheckResult.skipped(self.name, f"could not parse squawk output: {proc.stderr.strip()[:200]}")

        findings: list[Finding] = []
        for v in violations:
            rule = v.get("rule_name", "unknown")
            if rule in ignore:
                continue
            fname = v.get("file", "")
            mid = file_to_mid.get(fname, "?")
            stmt = _statement_at(line_to_stmt.get(fname, []), int(v.get("line", 0)))
            findings.append(Finding(
                rule=f"squawk:{rule}",
                severity=escalate.get(rule, Status.WARN),
                message=v.get("message", ""),
                evidence=f"{mid}: {stmt[:120]}",
                fix=v.get("help") or "",
            ))
        status = Status.worst([f.severity for f in findings]) if findings else Status.PASS
        counts = {s.value: sum(f.severity is s for f in findings) for s in (Status.REFUSE, Status.ASK, Status.WARN)}
        summary = "clean" if not findings else ", ".join(f"{n} {k}" for k, n in counts.items() if n)
        return CheckResult(self.name, status, summary, findings, {"ignored_rules": sorted(ignore)})

    @staticmethod
    def _lintable(ctx: Context, sql: str) -> tuple[str, list[tuple[int, str]]]:
        """Drop statements that only touch scratch tables or tables created earlier
        in the same migration: nobody else can be holding or waiting on those locks.

        Returns the SQL to lint and the 0-based start line of each kept statement.
        """
        kept: list[str] = []
        starts: list[tuple[int, str]] = []
        created: set[str] = set()
        line = 0
        for stmt in parse(sql, ctx.dialect.sqlglot_dialect):
            stmt_touches = touches([stmt])
            tables = {t.table for t in stmt_touches}
            harmless = tables and all(t in created or ctx.policy.table_class(t) == "scratch" for t in tables)
            created |= {t.table for t in stmt_touches if t.op == "create_table"}
            if harmless:
                continue
            starts.append((line, stmt.sql))
            kept.append(stmt.sql + ";")
            line += stmt.sql.count("\n") + 1
        return "\n".join(kept) + "\n", starts


def _statement_at(starts: list[tuple[int, str]], line: int) -> str:
    current = ""
    for start, text in starts:
        if start > line:
            break
        current = text
    return " ".join(current.split())
