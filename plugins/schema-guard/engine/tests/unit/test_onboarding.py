"""Org rollout helpers: layered policy, zero-config discovery, plain-SQL defaults, the card, verdict flags."""

from __future__ import annotations

import argparse
from pathlib import Path

import pytest

from sg.adapters.raw_sql import RawSqlAdapter
from sg.cli import _llm_input
from sg.core import policy as policy_mod
from sg.core.discover import discover_services
from sg.core.models import CheckResult, Finding, Status
from sg.core.policy import Policy, PolicyError
from sg.core.report import card
from sg.core.verdict import build


def test_extends_and_locked_keys(tmp_path: Path):
    (tmp_path / "org.yaml").write_text(
        "columns: {money: ['*_cents']}\nchecks: {roundtrip: {require_downgrade: true}}\nlocked: [checks.roundtrip]\n")
    (tmp_path / "schema-guard.yaml").write_text(
        "extends: org.yaml\ncolumns: {money: ['*_cents', '*_eur']}\nchecks: {roundtrip: {require_downgrade: false}}\n")
    p = Policy.load(tmp_path)
    assert p.raw["columns"]["money"] == ["*_cents", "*_eur"]  # not locked: the repo may extend it
    assert p.check_config("roundtrip")["require_downgrade"] is True  # locked: repo override ignored
    assert any("checks.roundtrip" in n for n in p.notes)
    assert str(tmp_path / "org.yaml") in p.source and "locked" not in p.raw


def test_missing_org_policy_is_an_error_not_silent(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("SG_ORG_POLICY", str(tmp_path / "nope.yaml"))
    with pytest.raises(PolicyError):
        Policy.load(tmp_path)


def test_org_policy_url_falls_back_to_cache(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    url = "https://policies.example.invalid/schema-guard.yaml"

    class Resp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"hot_row_threshold: 5\n"

    monkeypatch.setattr(policy_mod.urllib.request, "urlopen", lambda *a, **k: Resp())
    assert policy_mod._read_layer(url, tmp_path)[0] == {"hot_row_threshold": 5}

    def offline(*a, **k):
        raise OSError("no network")

    monkeypatch.setattr(policy_mod.urllib.request, "urlopen", offline)
    assert policy_mod._read_layer(url, tmp_path)[0] == {"hot_row_threshold": 5}  # served from cache


def test_discovers_services_in_subdirectories(tmp_path: Path):
    (tmp_path / "services" / "api").mkdir(parents=True)
    (tmp_path / "services" / "api" / "alembic.ini").write_text("[alembic]\n")
    flyway = tmp_path / "billing" / "src" / "main" / "resources" / "db" / "migration"
    flyway.mkdir(parents=True)
    (flyway / "V1__init.sql").write_text("CREATE TABLE invoices (id bigint);")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    found = {(s.path, s.adapter) for s in discover_services(tmp_path)}
    assert found == {("services/api", "alembic"), ("billing", "raw_sql")}


def test_raw_sql_is_forward_only_by_default_and_honours_migrations_dir(tmp_path: Path):
    d = tmp_path / "schema" / "changes"
    d.mkdir(parents=True)
    (d / "V1__init.sql").write_text("CREATE TABLE t (id bigint);")
    a = RawSqlAdapter(tmp_path)
    assert a.requires_downgrade is False
    a.migrations_dir = "schema/changes"
    assert [m.id for m in a.list_migrations()] == ["V1"]
    assert "forward-only" in a.conventions_hint()


def _doc(tmp_path: Path, findings: list[Finding], status: Status = Status.WARN) -> dict:
    return build([CheckResult("squawk", status, "x", findings)], {}, repo_root=tmp_path,
                 migrations=["m/0005.py"], tables=[], policy_sources=[])


def test_card_folds_warnings_and_fences_tracebacks(tmp_path: Path):
    tb = "Traceback (most recent call last):\n  File `x.py`\nModuleNotFoundError: no module named app"
    md = card(_doc(tmp_path, [Finding("squawk:require-lock-timeout", Status.WARN, "set lock_timeout", tb)]))
    assert "🟢 GO" in md and "<details><summary>1 warning(s)" in md
    assert "  ```\n  Traceback" in md
    assert "| Check |" not in md and "**Checks:** ⚠️ squawk" in md
    assert "none found (add a CODEOWNERS file" in md


def test_verdict_flags_replace_llm_json(tmp_path: Path):
    llm_file = tmp_path / "llm.json"
    llm_file.write_text('{"intent": "old", "suggestions": ["a"]}')
    args = argparse.Namespace(llm=str(llm_file), intent="add notes column", summary="nullable text",
                              question=["kg or lb?"], suggestion=["b"], escalate=None, reason=None)
    llm = _llm_input(args)
    assert llm["intent"] == "add notes column" and llm["questions"] == ["kg or lb?"]
    assert llm["suggestions"] == ["a", "b"]
