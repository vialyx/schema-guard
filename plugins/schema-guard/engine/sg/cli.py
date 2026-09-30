"""`sg` — the deterministic half of schema-guard.

  sg detect                      what repo/service/framework/tools are we dealing with?
  sg investigate --tables a,b    DDL, policy class, size, and callers of tables/columns
  sg render                      offline SQL of new/changed migrations
  sg check                       run every applicable check -> .schema-guard/checks.json
  sg verdict [--llm llm.json]    aggregate -> .schema-guard/verdict.json + card.md
                                 (or --intent/--summary/--question/--suggestion instead of llm.json)
  sg ci [--base origin/main]     CI gate: check + verdict for every service whose migrations changed
  sg init                        write schema-guard.yaml, .gitignore entry, CI workflow, Claude settings
  sg doctor                      is everything sg needs installed and reachable?
  sg guard-edit                  PreToolUse hook: block edits to shipped migrations

Exit codes (check/verdict): 0 GO/pass, 1 needs a human, 2 refused, 3 could not run.
Every command prints JSON (except the card) so both the LLM and CI can consume it.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

from sg import onboard
from sg.core import git, registry
from sg.core.context import build_context, preflight, render_pending
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
    results: list[CheckResult] = preflight(ctx)
    try:
        for chk in instances:
            if ctx.policy.check_config(chk.name).get("enabled", True) is False:
                results.append(CheckResult(chk.name, Status.WARN, "disabled by policy"))
                continue
            results.append(chk.run_safely(ctx))
    finally:
        ctx.close()
    return results


def check_service(path: Path, base: str | None = None, *, no_db: bool = False,
                  only: list[str] | None = None) -> tuple[Any, dict[str, Any], list[CheckResult]]:
    """Run the checks for one service and write .schema-guard/checks.json. Returns (ctx, payload, results)."""
    from sg.core.policy import find_repo_root

    # Never leave an older result behind for `sg verdict` to pick up if this run fails.
    (find_repo_root(path) / OUT_DIR / "checks.json").unlink(missing_ok=True)
    ctx = build_context(path, base_ref=base)
    if no_db:
        ctx.tools["db"] = False
    results = run_checks(ctx, only)
    out_dir = ctx.repo_root / OUT_DIR
    out_dir.mkdir(exist_ok=True)
    payload = {
        "fingerprint": git.worktree_fingerprint(ctx.repo_root),
        "service": ctx.service.path,
        "migrations": [_rel(ctx, m.path) for m in ctx.changed],
        "tables": _touched_tables(ctx),
        "checks": [r.to_dict() for r in results],
        "policy_sources": ctx.policy.source,
    }
    (out_dir / "checks.json").write_text(json.dumps(payload, indent=2))
    return ctx, payload, results


def cmd_check(args) -> int:
    ctx, _, results = check_service(Path(args.path), args.base, no_db=args.no_db,
                                    only=args.only.split(",") if args.only else None)
    _emit({
        "worst": Status.worst([r.status for r in results]).value if results else "skipped",
        "checks": [{"name": r.name, "status": r.status.value, "summary": r.summary,
                    "findings": [f.to_dict() for f in r.findings]} for r in results],
        "written": _rel(ctx, ctx.repo_root / OUT_DIR / "checks.json"),
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


def _llm_input(args) -> dict[str, Any]:
    """llm.json (optional) plus command-line flags, so simple cases need no JSON file."""
    llm: dict[str, Any] = json.loads(Path(args.llm).read_text()) if args.llm else {}
    for key in ("intent", "summary", "escalate"):
        if getattr(args, key, None):
            llm[key] = getattr(args, key)
    if getattr(args, "reason", None):
        llm["escalation_reason"] = args.reason
    for key, flag in (("questions", "question"), ("suggestions", "suggestion")):
        if getattr(args, flag, None):
            llm[key] = [*(llm.get(key) or []), *getattr(args, flag)]
    return llm


def cmd_verdict(args) -> int:
    from sg.core.policy import find_repo_root

    repo_root = find_repo_root(Path(args.path))
    out_dir = repo_root / OUT_DIR
    out_dir.mkdir(exist_ok=True)
    checks_file = Path(args.checks) if args.checks else out_dir / "checks.json"
    if checks_file.exists():
        data, results = _load_checks(checks_file)
        if data.get("fingerprint") != git.worktree_fingerprint(repo_root):
            results.append(CheckResult.skipped(
                "freshness", "files changed since `sg check` ran; these results are stale, re-run `sg check`"))
    else:
        data, results = {"migrations": [], "tables": []}, []
    llm = _llm_input(args)
    if llm.get("escalate") and llm["escalate"] not in [v.value for v in Verdict]:
        print(f"invalid escalate value {llm['escalate']!r}", file=sys.stderr)
        return 3
    doc = build_verdict(
        results, llm, repo_root=repo_root, migrations=data.get("migrations", []),
        tables=data.get("tables", []), policy_sources=data.get("policy_sources", []),
    )
    (out_dir / "verdict.json").write_text(json.dumps(doc, indent=2))
    md = card(doc)
    (out_dir / "card.md").write_text(md)
    print(md)
    return EXIT[Verdict(doc["verdict"])]


def _changed_files(repo_root: Path, base: str) -> list[Path]:
    """Files changed on this branch since it left `base`, plus uncommitted and untracked ones."""
    names: set[str] = set()
    for args in (["diff", "--name-only", f"{base}...HEAD"], ["diff", "--name-only", "HEAD"],
                 ["ls-files", "--others", "--exclude-standard"]):
        names |= set(git._git(repo_root, *args).stdout.split())
    root = git.git_root(repo_root) or repo_root
    return [(root / n).resolve() for n in names]


def cmd_ci(args) -> int:
    """The CI gate: check every service whose migrations changed; exit with the worst verdict."""
    from sg.core.context import load_policy
    from sg.core.policy import POLICY_FILE, find_repo_root

    repo_root = find_repo_root(Path(args.path))
    policy, base, policy_changed = load_policy(repo_root, args.base)
    if not git.ref_exists(repo_root, base):
        print(f"schema-guard: base ref '{base}' not found. Use actions/checkout with fetch-depth: 0 "
              "(or git fetch the base branch) so shipped migrations can be recognised.", file=sys.stderr)
        return 3
    changed = _changed_files(repo_root, base)
    cards: list[str] = []
    worst = Verdict.GO
    checked = 0
    for svc in policy.services:
        ctx = build_context(repo_root / svc.path, base_ref=base)
        dirs = [d.resolve() for d in ctx.adapter.migration_dirs()]
        if not any(d == f.parent or d in f.parents for f in changed for d in dirs):
            continue
        checked += 1
        _, data, results = check_service(repo_root / svc.path, base)
        doc = build_verdict(results, {}, repo_root=repo_root, migrations=data["migrations"],
                            tables=data["tables"], policy_sources=data["policy_sources"])
        v = Verdict(doc["verdict"])
        worst = v if v.rank > worst.rank else worst
        cards.append(f"### `{svc.path}`\n\n" + card(doc))
    if policy_changed and not checked:
        worst = Verdict.NEEDS_HUMAN
        cards.append(f"## schema-guard: 🟡 NEEDS-HUMAN\n\n`{POLICY_FILE}` changed. The new policy applies "
                     "after it merges; the policy owners (CODEOWNERS) must approve it.\n")
    if not cards:
        cards.append("## schema-guard: 🟢 no migration changes\n")
    report = "\n".join(cards)
    if worst is Verdict.NEEDS_HUMAN and args.allow_needs_human:
        report += "\n> NEEDS-HUMAN accepted: the PR carries the approval label.\n"
    print(report)
    summary = args.summary or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a") as fh:
            fh.write(report + "\n")
    if worst is Verdict.NEEDS_HUMAN and args.allow_needs_human:
        return 0
    return EXIT[worst]


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
    ver.add_argument("--intent", help="what the change is for (instead of llm.json)")
    ver.add_argument("--summary", help="1-2 sentence summary of the change")
    ver.add_argument("--question", action="append", help="open question for a human (repeatable; blocks GO)")
    ver.add_argument("--suggestion", action="append", help="non-blocking suggestion (repeatable)")
    ver.add_argument("--escalate", choices=[v.value for v in Verdict], help="raise the verdict")
    ver.add_argument("--reason", help="why you escalated")
    ci = sub.add_parser("ci", parents=[common], help="CI gate: check every service whose migrations changed")
    ci.add_argument("--base", help="base ref, e.g. origin/main (default from policy)")
    ci.add_argument("--allow-needs-human", action="store_true",
                    help="exit 0 on NEEDS-HUMAN (a human approved, e.g. via a PR label); REFUSED still fails")
    ci.add_argument("--summary", help="also append the report to this file (default: $GITHUB_STEP_SUMMARY)")
    init = sub.add_parser("init", parents=[common], help="write schema-guard.yaml and team setup for this repo")
    init.add_argument("--force", action="store_true", help="overwrite existing files")
    sub.add_parser("doctor", parents=[common], help="check that everything sg needs is available")
    sub.add_parser("guard-edit", parents=[common])
    args = p.parse_args(argv)
    handlers = {
        "detect": cmd_detect, "investigate": cmd_investigate, "render": cmd_render,
        "check": cmd_check, "verdict": cmd_verdict, "guard-edit": cmd_guard_edit, "ci": cmd_ci,
        "init": onboard.cmd_init, "doctor": onboard.cmd_doctor,
    }
    try:
        return handlers[args.cmd](args)
    except (ValueError, RuntimeError, FileNotFoundError) as exc:
        _emit({"error": str(exc), "hint": "run `sg doctor` to check the setup"})
        return 3


if __name__ == "__main__":
    sys.exit(main())
