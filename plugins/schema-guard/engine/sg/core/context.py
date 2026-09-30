"""Builds the Context shared by every check: repo, policy, adapter, dialect, migrations, tools."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

from sg.core import git, registry
from sg.core.models import Context
from sg.core.policy import Policy, find_repo_root


def detect_tools(dialect_cls) -> dict[str, bool | str]:
    db_ok, db_how = dialect_cls.available()
    if os.environ.get("SG_DISABLE_DB"):
        db_ok, db_how = False, "disabled by SG_DISABLE_DB"
    squawk = bool(shutil.which("squawk") or shutil.which("npx"))
    return {"db": db_ok, "db_backend": db_how, "squawk": squawk, "git": bool(shutil.which("git"))}


def build_context(path: Path, base_ref: str | None = None) -> Context:
    repo_root = find_repo_root(path)
    policy = Policy.load(repo_root)
    svc = policy.service_for(repo_root, path if path != repo_root else repo_root / policy.services[0].path)
    service_root = (repo_root / svc.path).resolve()

    adapter = registry.detect_adapter(service_root, svc.adapter)
    dialect_cls = registry.dialects()[policy.dialect]
    dialect = dialect_cls()
    ref = base_ref or policy.base_ref

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
    )
    return ctx


def render_pending(ctx: Context) -> None:
    """Fill ctx.pending_sql. Modified-but-applied migrations are rendered too, so rules can see them."""
    for m in ctx.migrations:
        if not m.applied or m.modified:
            ctx.pending_sql[m.id] = ctx.adapter.render_sql(m.id)
