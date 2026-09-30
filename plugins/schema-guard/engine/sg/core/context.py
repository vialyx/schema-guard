"""Builds the Context shared by every check: repo, policy, adapter, dialect, migrations, tools."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from sg.core import git, registry
from sg.core.models import CheckResult, Context, Finding, Status
from sg.core.discover import discover_services
from sg.core.policy import POLICY_FILE, Policy, find_repo_root
from sg.core.pyenv import service_python


def detect_tools(dialect_cls) -> dict[str, bool | str]:
    db_ok, db_how = dialect_cls.available()
    if os.environ.get("SG_DISABLE_DB"):
        db_ok, db_how = False, "disabled by SG_DISABLE_DB"
    squawk = bool(shutil.which("squawk") or shutil.which("npx"))
    return {"db": db_ok, "db_backend": db_how, "squawk": squawk, "git": bool(shutil.which("git"))}


def load_policy(repo_root: Path, base_ref: str | None) -> tuple[Policy, str, bool]:
    """(policy, resolved base ref, policy_changed). The base branch's policy file wins when it exists."""
    working = Policy.load(repo_root)
    ref = git.resolve_ref(repo_root, base_ref or working.base_ref)
    policy_file = repo_root / POLICY_FILE
    base_text = git.file_at_ref(repo_root, ref, policy_file) if git.ref_exists(repo_root, ref) else None
    if base_text is None:  # first adoption, or no base ref: nothing older to hold the change to
        policy, changed = working, False
    else:
        current = policy_file.read_text() if policy_file.exists() else ""
        policy, changed = Policy.load(repo_root, base_text, f"{ref}:{POLICY_FILE}"), base_text != current
    return policy.with_services(discover_services(repo_root) if not policy.raw.get("services") else []), ref, changed


def build_context(path: Path, base_ref: str | None = None) -> Context:
    repo_root = find_repo_root(path)
    policy, ref, policy_changed = load_policy(repo_root, base_ref)
    svc = policy.service_for(repo_root, path if path != repo_root else repo_root / policy.services[0].path)
    service_root = (repo_root / svc.path).resolve()

    preferred = "raw_sql" if svc.migrations_dir and svc.adapter == "auto" else svc.adapter
    adapter = registry.detect_adapter(service_root, preferred)
    adapter.python = service_python(service_root, repo_root, svc.python)
    if svc.migrations_dir:
        adapter.migrations_dir = svc.migrations_dir
    dialect_cls = registry.dialects()[policy.dialect]
    dialect = dialect_cls()

    migrations = adapter.list_migrations()
    for m in migrations:
        m.applied, m.modified = git.shipped_state(repo_root, ref, m.path)

    tools = detect_tools(dialect_cls)
    tools["base_ref"] = git.ref_exists(repo_root, ref)

    ctx = Context(
        repo_root=repo_root,
        service_root=service_root,
        policy=policy,
        adapter=adapter,
        dialect=dialect,
        base_ref=ref,
        migrations=migrations,
        tools=tools,
        service=svc,
        policy_changed=policy_changed,
    )
    return ctx


def preflight(ctx: Context) -> list[CheckResult]:
    """Problems with the setup itself; they must show on the card, never pass silently."""
    out: list[CheckResult] = []
    if not ctx.tools.get("base_ref"):
        out.append(CheckResult.skipped(
            "base-ref",
            f"base ref '{ctx.base_ref}' not found, so shipped migrations look new and edits to them "
            "go undetected. CI: actions/checkout with fetch-depth: 0. Locally: git fetch origin "
            f"{ctx.base_ref.removeprefix('origin/')}, or pass --base.",
        ))
    for note in ctx.policy.notes:
        out.append(CheckResult("policy-locked", Status.WARN, note))
    if ctx.policy_changed:
        out.append(CheckResult("policy-file", Status.ASK, "schema-guard.yaml changed; checks used the base branch's policy", [
            Finding("policy-changed", Status.ASK,
                    "this change edits schema-guard.yaml; the new policy applies only after it merges",
                    fix="have the policy owners (CODEOWNERS for schema-guard.yaml) approve the change"),
        ]))
    return out


def render_pending(ctx: Context) -> None:
    """Fill ctx.pending_sql. Modified-but-applied migrations are rendered too, so rules can see them."""
    for m in ctx.migrations:
        if not m.applied or m.modified:
            ctx.pending_sql[m.id] = ctx.adapter.render_sql(m.id)
