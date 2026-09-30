"""End to end on the Acme demo repo: `sg init` and the `sg ci` gate, as an adopting team would use them."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from sg.cli import main

ROOT = Path(__file__).resolve().parents[5]
BUILD = ROOT / "examples" / "build_fixture.sh"
TRAP = ROOT / "evals" / "cases" / "03-trap-new-table-review.yaml"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                   check=True, capture_output=True)


@pytest.fixture
def acme(tmp_path: Path) -> Path:
    if not BUILD.exists() or not shutil.which("git"):
        pytest.skip("demo fixture not available")
    repo = tmp_path / "acme"
    subprocess.run(["bash", str(BUILD), str(repo)], check=True, capture_output=True)
    (repo / ".gitignore").write_text(".schema-guard/\n__pycache__/\n")
    git(repo, "add", ".gitignore")
    git(repo, "commit", "-qm", "ignore outputs")
    git(repo, "checkout", "-qb", "feature")
    return repo


def add_trap_migration(repo: Path) -> Path:
    import yaml

    item = yaml.safe_load(TRAP.read_text())["setup"][0]
    (repo / item["path"]).write_text(item["content"])
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "0005")
    return repo / item["path"]


def test_ci_passes_prs_without_migration_changes(acme: Path, capsys):
    (acme / "services/ledger-api/app/models.py").write_text("# refactor\n")
    git(acme, "commit", "-qam", "code only")
    assert main(["ci", "--path", str(acme), "--base", "main"]) == 0
    assert "no migration changes" in capsys.readouterr().out


def test_ci_gates_on_the_worst_verdict(acme: Path, pg_server, capsys):
    path = add_trap_migration(acme)
    assert main(["ci", "--path", str(acme), "--base", "main"]) == 0  # safe new table: GO
    path.write_text(path.read_text().replace('"credit_amount_cents", sa.BigInteger()', '"credit_amount_cents", sa.Float()'))
    git(acme, "commit", "-qam", "float money")
    assert main(["ci", "--path", str(acme), "--base", "main", "--allow-needs-human"]) == 2  # label can't pass REFUSED
    assert "REFUSED" in capsys.readouterr().out


def test_ci_needs_human_passes_only_with_approval(acme: Path, pg_server):
    policy = acme / "schema-guard.yaml"
    policy.write_text(policy.read_text() + "\nhot_row_threshold: 10\n")
    git(acme, "commit", "-qam", "tweak policy")
    assert main(["ci", "--path", str(acme), "--base", "main"]) == 1
    assert main(["ci", "--path", str(acme), "--base", "main", "--allow-needs-human"]) == 0


def test_ci_fails_loudly_without_base_ref(acme: Path, capsys):
    assert main(["ci", "--path", str(acme), "--base", "origin/nope"]) == 3
    assert "fetch-depth: 0" in capsys.readouterr().err


def test_init_sets_up_a_repo_in_one_command(acme: Path, capsys):
    (acme / "schema-guard.yaml").unlink()
    assert main(["init", "--path", str(acme)]) == 0
    out = json.loads(capsys.readouterr().out)
    text = (acme / "schema-guard.yaml").read_text()
    assert "path: services/ledger-api" in text and "adapter: raw_sql" in text
    assert "ledger_entries: {class: ledger}" in text and "audit_log: {class: audit}" in text
    assert (acme / ".github/workflows/schema-guard.yml").exists()
    settings = json.loads((acme / ".claude/settings.json").read_text())
    assert settings["enabledPlugins"]["schema-guard@schema-guard"] is True
    assert ".schema-guard/" in (acme / ".gitignore").read_text()
    assert out["todo"]
    assert main(["init", "--path", str(acme)]) == 0  # idempotent: existing files are kept
    assert "exists" in json.loads(capsys.readouterr().out)["skipped"][0]
