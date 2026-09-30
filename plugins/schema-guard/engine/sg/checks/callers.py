"""Who reads or writes the tables/columns this change touches — across the whole repo.

Static text search, deliberately simple and conservative: anything that builds
SQL dynamically (f-strings, .format, concatenation) near the table is reported
as UNRESOLVED, because we cannot prove it doesn't use the column. Unresolved
callers of a destructive change force a human decision.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

from sg.checks.base import Check
from sg.core.models import CheckResult, Context, Finding, Status
from sg.core.sql import parse, touches

SOURCE_EXT = {".py", ".sql", ".ts", ".tsx", ".js", ".go", ".rb", ".java", ".kt", ".cs", ".php", ".scala"}
SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", ".schema-guard", "dist", "build", ".tox"}
MAX_HITS = 40

_SQL_KW = re.compile(r"\b(SELECT|UPDATE|INSERT|DELETE|FROM|JOIN|INTO|SET)\b", re.I)
_DYNAMIC = re.compile(r"""(\bf["']|\.format\(|["']\s*\+|\+\s*["']|%\s*\(|\$\{)""")
_PLACEHOLDER_TABLE = re.compile(r"\b(FROM|JOIN|INTO|UPDATE|TABLE)\s+[\"'`]?\{", re.I)
_PLACEHOLDER_COL = re.compile(r"\b(SET|SELECT|BY|WHERE|AND|OR)\s+\{|,\s*\{", re.I)

# Only these ops can break an existing caller.
BREAKING_OPS = {"drop_column", "rename_column", "alter_type", "drop_table", "rename_table", "set_not_null"}


def _iter_sources(ctx: Context):
    skip = {d.resolve() for d in ctx.adapter.migration_dirs()}
    for root, dirs, files in os.walk(ctx.repo_root):
        rp = Path(root).resolve()
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and (rp / d).resolve() not in skip]
        for f in files:
            p = Path(root) / f
            if p.suffix in SOURCE_EXT:
                yield p


def find_callers(ctx: Context, table: str, column: str | None) -> dict[str, Any]:
    """{'resolved': [...], 'unresolved': [...]} with {'path','line','text'} entries."""
    patterns = [re.compile(p) for p in ctx.adapter.caller_patterns(table, column)] if column else []
    table_re = re.compile(rf"\b{re.escape(table)}\b", re.I)
    col_re = re.compile(rf"\b{re.escape(column)}\b") if column else None
    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    for path in _iter_sources(ctx):
        try:
            lines = path.read_text(errors="ignore").splitlines()
        except OSError:
            continue
        rel = path.resolve().relative_to(ctx.repo_root.resolve()).as_posix()
        for i, line in enumerate(lines, 1):
            hit = {"path": rel, "line": i, "text": line.strip()[:200]}
            is_sql = bool(_SQL_KW.search(line))
            if is_sql and _DYNAMIC.search(line):
                table_dynamic = bool(_PLACEHOLDER_TABLE.search(line))
                col_dynamic = bool(_PLACEHOLDER_COL.search(line))
                mentions_table = bool(table_re.search(line))
                if table_dynamic or (mentions_table and (column is None or col_dynamic)):
                    if len(unresolved) < MAX_HITS:
                        unresolved.append({**hit, "why": "SQL built dynamically; target cannot be resolved statically"})
                    continue
            if column:
                if col_re.search(line) and (any(p.search(line) for p in patterns) or is_sql):
                    if len(resolved) < MAX_HITS:
                        resolved.append(hit)
            elif table_re.search(line) and is_sql:
                if len(resolved) < MAX_HITS:
                    resolved.append(hit)
    return {"resolved": resolved, "unresolved": unresolved}


class CallersCheck(Check):
    name = "callers"
    order = 15

    def run(self, ctx: Context) -> CheckResult:
        findings: list[Finding] = []
        details: dict[str, Any] = {}
        for mid, sql in ctx.pending_sql.items():
            for t in touches(parse(sql, ctx.dialect.sqlglot_dialect)):
                if t.op in ("create_table", "create_index", "other"):
                    continue
                key = f"{t.table}.{t.column}" if t.column else t.table
                if key in details:
                    continue
                callers = find_callers(ctx, t.table, t.column)
                details[key] = {"op": t.op, **callers}
                if t.op not in BREAKING_OPS:
                    continue
                if callers["unresolved"]:
                    u = callers["unresolved"][0]
                    findings.append(Finding(
                        "unresolved-callers", Status.ASK,
                        f"{len(callers['unresolved'])} place(s) build SQL dynamically against `{t.table}`; "
                        f"cannot prove they don't depend on `{key}` ({t.op})",
                        evidence=f"{u['path']}:{u['line']}: {u['text'][:100]}",
                        fix="ask the owners of these files, or migrate them first (expand/contract)",
                    ))
                if callers["resolved"]:
                    r = callers["resolved"][0]
                    findings.append(Finding(
                        "live-callers", Status.ASK,
                        f"{len(callers['resolved'])} caller(s) still reference `{key}` (change: {t.op})",
                        evidence=f"{r['path']}:{r['line']}",
                        fix="update callers in an expand step first; only drop/rename in a later contract migration",
                    ))
        status = Status.worst([f.severity for f in findings]) if findings else Status.PASS
        n = sum(len(v["resolved"]) + len(v["unresolved"]) for v in details.values())
        summary = f"{n} caller reference(s) across {len(details)} target(s)" if details else "no existing tables/columns affected"
        return CheckResult(self.name, status, summary, findings, details)
