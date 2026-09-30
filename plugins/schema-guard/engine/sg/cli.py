"""`sg` — the deterministic half of schema-guard.

  sg detect                      what repo/service/framework/tools are we dealing with?
  sg investigate --tables a,b    DDL, policy class, size, and callers of tables/columns
  sg render                      offline SQL of new/changed migrations
  sg check                       run every applicable check -> .schema-guard/checks.json
  sg verdict [--llm llm.json]    aggregate -> .schema-guard/verdict.json + card.md
  sg guard-edit                  PreToolUse hook: block edits to shipped migrations

Exit codes (check/verdict): 0 GO/pass, 1 needs a human, 2 refused, 3 could not run.
Every command prints JSON (except the card) so both the LLM and CI can consume it.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from sg.core import registry
from sg.core.context import build_context, render_pending
from sg.core.models import CheckResult, Status, Verdict
from sg.core.report import card
from sg.core.sql import parse, touches
from sg.core.verdict import build as build_verdict

OUT_DIR = ".schema-guard"
EXIT = {Verdict.GO: 0, Verdict.NEEDS_HUMAN: 1, Verdict.REFUSED: 2}


def _emit(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _rel(ctx, p: Path) -> str:
    try:
        return p.resolve().relative_to(ctx.repo_root.resolve()).as_posix()
    except ValueError:
        return str(p)


def _touched_tables(ctx) -> list[dict[str, Any]]:
    tables: dict[str, dict[str, Any]] = {}
    for mid, sql in ctx.pending_sql.items():
        for t in touches(parse(sql, ctx.dialect.sqlglot_dialect)):
            tables.setdefault(t.table, {
                "name": t.table,
                "class": ctx.policy.table_class(t.table),
                "est_rows": ctx.policy.est_rows(t.table),
            })
    return list(tables.values())


# --- commands -----------------------------------------------------------------

def cmd_detect(args) -> int:
    ctx = build_context(Path(args.path))
    learnings = ctx.repo_root / "LEARNINGS.md"
    _emit({
        "repo_root": str(ctx.repo_root),
        "service": ctx.service.path,
        "all_services": [{"path": s.path, "adapter": s.adapter} for s in ctx.policy.services],
        "adapter": ctx.adapter.name,
        "dialect": ctx.dialect.name,
        "base_ref": ctx.base_ref,
        "tools": ctx.tools,
        "migrations": [
            {"id": m.id, "path": _rel(ctx, m.path), "parent": m.parent,
             "state": "shipped-EDITED" if m.modified else "shipped" if m.applied else "new"}
            for m in ctx.migrations
        ],
        "migration_dirs": [_rel(ctx, d) for d in ctx.adapter.migration_dirs()],
        "tables_policy": ctx.policy.raw.get("tables", {}),
        "column_kinds": ctx.policy.raw.get("columns", {}),
        "conventions_hint": ctx.adapter.conventions_hint(),
        "learnings": learnings.read_text() if learnings.exists() else "",
        "policy_sources": ctx.policy.source,
    })
    return 0


def cmd_investigate(args) -> int:
    from sg.checks.callers import find_callers

    ctx = build_context(Path(args.path))
    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    cols = [c.strip() for c in (args.columns or "").split(",") if c.strip()]
    report: dict[str, Any] = {"tables": {}, "callers": {}, "ddl": None, "ddl_note": ""}
    for t in tables:
        rows = ctx.policy.est_rows(t)
        report["tables"][t] = {
            "class": ctx.policy.table_class(t),
            "est_rows": rows,
            "hot": ctx.policy.is_hot(t),
            "size_known": rows is not None,
            "protected": ctx.policy.is_protected(t),
        }
    targets = [(c.split(".")[0], c.split(".")[1]) for c in cols if "." in c] or [(t, None) for t in tables]
    for table, col in targets:
        key = f"{table}.{col}" if col else table
        report["callers"][key] = find_callers(ctx, table, col)
    if ctx.tools.get("db"):
        try:
            server = ctx.db_server()
            dsn = server.create_database("sg_investigate")
            ctx.adapter.upgrade(dsn, "head")
            report["ddl"] = ctx.dialect.describe_tables(dsn, tables)
        except Exception as exc:
            report["ddl_note"] = f"could not build schema at head: {exc}"
        finally:
            ctx.close()
    else:
        report["ddl_note"] = f"no database available ({ctx.tools.get('db_backend')}); read the models instead"
    _emit(report)
    return 0


def cmd_render(args) -> int:
    ctx = build_context(Path(args.path))
    render_pending(ctx)
    _emit(ctx.pending_sql)
    return 0


def run_checks(ctx, only: list[str] | None = None) -> list[CheckResult]:
    render_pending(ctx)
    instances = [cls() for name, cls in registry.checks().items() if not only or name in only]
    instances.sort(key=lambda c: (c.order, c.name))
    results: list[CheckResult] = []
    try:
        for chk in instances:
            if ctx.policy.check_config(chk.name).get("enabled", True) is False:
                continue
            results.append(chk.run_safely(ctx))
    finally:
        ctx.close()
    return results


def cmd_check(args) -> int:
    ctx = build_context(Path(args.path), base_ref=args.base)
    if args.no_db:
        ctx.tools["db"] = False
    results = run_checks(ctx, args.only.split(",") if args.only else None)
    out_dir = ctx.repo_root / OUT_DIR
    out_dir.mkdir(exist_ok=True)
    payload = {
        "service": ctx.service.path,
        "migrations": [_rel(ctx, m.path) for m in ctx.changed],
        "tables": _touched_tables(ctx),
        "checks": [r.to_dict() for r in results],
    }
    (out_dir / "checks.json").write_text(json.dumps(payload, indent=2))
    _emit({
        "worst": Status.worst([r.status for r in results]).value if results else "skipped",
        "checks": [{"name": r.name, "status": r.status.value, "summary": r.summary,
                    "findings": [f.to_dict() for f in r.findings]} for r in results],
        "written": _rel(ctx, out_dir / "checks.json"),
    })
    return EXIT[Verdict.from_status(Status.worst([r.status for r in results]) if results else Status.SKIPPED)]


def _load_checks(path: Path) -> tuple[dict[str, Any], list[CheckResult]]:
    from sg.core.models import Finding

    data = json.loads(path.read_text())
    results = []
    for c in data["checks"]:
        results.append(CheckResult(
            name=c["name"], status=Status(c["status"]), summary=c.get("summary", ""),
            findings=[Finding(rule=f["rule"], severity=Status(f["severity"]), message=f["message"],
                              evidence=f.get("evidence", ""), fix=f.get("fix", "")) for f in c["findings"]],
            details=c.get("details", {}),
        ))
    return data, results


def cmd_verdict(args) -> int:
    from sg.core.policy import Policy, find_repo_root

    repo_root = find_repo_root(Path(args.path))
    out_dir = repo_root / OUT_DIR
    out_dir.mkdir(exist_ok=True)
    checks_file = Path(args.checks) if args.checks else out_dir / "checks.json"
    if checks_file.exists():
        data, results = _load_checks(checks_file)
    else:
        data, results = {"migrations": [], "tables": []}, []
    llm: dict[str, Any] = json.loads(Path(args.llm).read_text()) if args.llm else {}
    if llm.get("escalate") and llm["escalate"] not in [v.value for v in Verdict]:
        print(f"invalid escalate value {llm['escalate']!r}", file=sys.stderr)
        return 3
    doc = build_verdict(
        results, llm, repo_root=repo_root, migrations=data.get("migrations", []),
        tables=data.get("tables", []), policy_sources=Policy.load(repo_root).source,
    )
    (out_dir / "verdict.json").write_text(json.dumps(doc, indent=2))
    md = card(doc)
    (out_dir / "card.md").write_text(md)
    print(md)
    return EXIT[Verdict(doc["verdict"])]


def cmd_guard_edit(args) -> int:
    """Claude Code PreToolUse hook. Reads the tool call on stdin; exit 2 blocks it."""
    try:
        event = json.load(sys.stdin)
        tool_input = event.get("tool_input") or {}
        target = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not target:
            return 0
        target_path = Path(target)
        if not target_path.is_absolute():
            target_path = Path(event.get("cwd") or ".") / target_path
        target_path = target_path.resolve()
        if target_path.suffix not in (".py", ".sql") or not target_path.exists():
            return 0  # fast path: new files and non-migration files are always allowed
        ctx = build_context(target_path.parent)
    except Exception:
        return 0  # not our business (not a schema-guard repo, unparsable event, ...)
    for m in ctx.migrations:
        if m.path.resolve() == target_path and m.applied:
            print(
                f"schema-guard: {_rel(ctx, target_path)} is a migration that has already shipped "
                f"(it exists on '{ctx.base_ref}'). Editing it would make environments diverge. "
                "Create a NEW migration that corrects it instead.",
                file=sys.stderr,
            )
            return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--path", default=".", help="repo or service directory (default: cwd); "
                        "in multi-service repos point it at the service")
    p = argparse.ArgumentParser(prog="sg", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("detect", parents=[common])
    inv = sub.add_parser("investigate", parents=[common])
    inv.add_argument("--tables", required=True, help="comma-separated table names")
    inv.add_argument("--columns", help="comma-separated table.column to find callers for")
    sub.add_parser("render", parents=[common])
    chk = sub.add_parser("check", parents=[common])
    chk.add_argument("--base", help="base git ref that represents production (default from policy)")
    chk.add_argument("--only", help="comma-separated check names")
    chk.add_argument("--no-db", action="store_true", help="pretend no database is available")
    ver = sub.add_parser("verdict", parents=[common])
    ver.add_argument("--llm", help="JSON file with intent/summary/plan/questions/escalate from the skill")
    ver.add_argument("--checks", help="checks.json to use (default: .schema-guard/checks.json)")
    sub.add_parser("guard-edit", parents=[common])
    args = p.parse_args(argv)
    handlers = {
        "detect": cmd_detect, "investigate": cmd_investigate, "render": cmd_render,
        "check": cmd_check, "verdict": cmd_verdict, "guard-edit": cmd_guard_edit,
    }
    try:
        return handlers[args.cmd](args)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        _emit({"error": str(exc)})
        return 3


if __name__ == "__main__":
    sys.exit(main())
