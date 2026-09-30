"""The engine must fail closed: a change can't loosen its own policy, stale results can't
be reused, a missing base ref is visible, and Alembic never touches a real database."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from sg.adapters import alembic_runner
from sg.core import git
from sg.core.context import load_policy, preflight
from sg.core.models import Status
from sg.core.pyenv import service_python

from conftest import make_ctx


def sh(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    sh(tmp_path, "init", "-q", "-b", "main")
    sh(tmp_path, "config", "user.email", "t@example.invalid")
    sh(tmp_path, "config", "user.name", "t")
    (tmp_path / "schema-guard.yaml").write_text("tables:\n  ledger_entries: {class: ledger}\n")
    sh(tmp_path, "add", "-A")
    sh(tmp_path, "commit", "-qm", "base")
    sh(tmp_path, "checkout", "-qb", "feature")
    return tmp_path


def test_change_cannot_loosen_its_own_policy(repo: Path):
    (repo / "schema-guard.yaml").write_text("tables:\n  ledger_entries: {class: scratch}\n")
    policy, ref, changed = load_policy(repo, "main")
    assert policy.table_class("ledger_entries") == "ledger"
    assert changed and ref == "main"
    assert policy.source[-1] == "main:schema-guard.yaml"


def test_unchanged_policy_is_not_flagged(repo: Path):
    policy, _, changed = load_policy(repo, "main")
    assert not changed and policy.table_class("ledger_entries") == "ledger"


def test_first_adoption_uses_working_copy(tmp_path: Path):
    sh(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "schema-guard.yaml").write_text("tables:\n  t: {class: hot}\n")
    policy, _, changed = load_policy(tmp_path, "main")  # no commits: no base ref at all
    assert policy.table_class("t") == "hot" and not changed


def test_base_ref_falls_back_to_origin(repo: Path):
    sh(repo, "update-ref", "refs/remotes/origin/release", "main")
    assert git.resolve_ref(repo, "release") == "origin/release"
    assert git.resolve_ref(repo, "main") == "main"
    assert git.resolve_ref(repo, "nope") == "nope"


def test_preflight_reports_missing_base_ref_and_policy_change():
    ctx = make_ctx("SELECT 1")
    ctx.tools = {"base_ref": False}
    ctx.policy_changed = True
    by_name = {r.name: r for r in preflight(ctx)}
    assert by_name["base-ref"].status is Status.SKIPPED and "fetch-depth: 0" in by_name["base-ref"].summary
    assert by_name["policy-file"].status is Status.ASK


def test_fingerprint_tracks_uncommitted_changes_but_not_outputs(repo: Path):
    before = git.worktree_fingerprint(repo)
    (repo / ".schema-guard").mkdir()
    (repo / ".schema-guard" / "llm.json").write_text("{}")
    assert git.worktree_fingerprint(repo) == before
    (repo / "new_migration.sql").write_text("ALTER TABLE t ADD c int;")
    assert git.worktree_fingerprint(repo) != before


def test_service_python_prefers_config_then_venv(tmp_path: Path):
    svc = tmp_path / "svc"
    (svc / ".venv" / "bin").mkdir(parents=True)
    assert service_python(svc, tmp_path) == [sys.executable]
    (svc / ".venv" / "bin" / "python").touch()
    assert service_python(svc, tmp_path) == [str(svc / ".venv" / "bin" / "python")]
    assert service_python(svc, tmp_path, "uv run python") == ["uv", "run", "python"]


ENV_PY = '''
from alembic import context
from sqlalchemy import create_engine

def run():
    engine = create_engine("postgresql+psycopg://app@prod-db.internal:5432/orders")
    with engine.connect() as conn:
        context.configure(connection=conn)

run()
'''


def test_alembic_runner_refuses_a_database_env_py_hardcodes(tmp_path: Path):
    """A stock-ish env.py that ignores our URL must be stopped before it connects."""
    (tmp_path / "alembic.ini").write_text("[alembic]\nscript_location = migrations\n"
                                          "sqlalchemy.url = postgresql+psycopg://dev@localhost/dev\n")
    (tmp_path / "migrations" / "versions").mkdir(parents=True)
    (tmp_path / "migrations" / "env.py").write_text(ENV_PY)
    (tmp_path / "migrations" / "script.py.mako").write_text("")
    proc = subprocess.run(
        [sys.executable, alembic_runner.__file__, "postgresql+psycopg://sg@127.0.0.1:1/throwaway",
         "-c", "alembic.ini", "upgrade", "head"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == alembic_runner.GUARD_EXIT, proc.stderr
    assert "refusing to run migrations against" in proc.stderr and "prod-db.internal" in proc.stderr


def test_alembic_runner_overrides_ini_url():
    from alembic.config import Config

    cfg = Config()
    cfg.set_main_option("sqlalchemy.url", "postgresql://dev@localhost/dev")
    url = "postgresql+psycopg://sg:p%40ss@127.0.0.1:5433/throwaway"
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))  # what the runner does
    assert cfg.get_main_option("sqlalchemy.url") == url
