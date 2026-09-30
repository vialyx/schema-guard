"""Contract every registered MigrationAdapter must satisfy.

Parametrised over the registry, so a new adapter is tested automatically once it
is registered and has a sample project in samples.py.
"""

from __future__ import annotations

import re

import pytest
from samples import SAMPLES

from sg.core import registry

ADAPTERS = registry.adapters()
NAMES = sorted(ADAPTERS)


def test_every_adapter_has_a_sample():
    missing = [n for n in NAMES if n not in SAMPLES]
    assert not missing, (
        f"adapter(s) {missing} have no sample project. Add a builder to "
        "tests/contract/samples.py (SAMPLES[<adapter name>]) that creates the standard "
        "two-migration project (widgets table, then a `note text` column)."
    )


def _make(name, tmp_path):
    if name not in SAMPLES:
        pytest.fail(f"no sample for adapter {name!r}; see test_every_adapter_has_a_sample")
    root = SAMPLES[name](tmp_path)
    return ADAPTERS[name](root), root


@pytest.mark.parametrize("name", NAMES)
def test_name_matches_registry(name):
    assert ADAPTERS[name].name == name


@pytest.mark.parametrize("name", NAMES)
def test_detect(name, tmp_path):
    for other in SAMPLES:
        root = SAMPLES[other](tmp_path / other)
        assert ADAPTERS[name].detect(root) is (other == name), (
            f"{name}.detect() on the {other} sample"
        )


@pytest.mark.parametrize("name", NAMES)
def test_list_migrations(name, tmp_path):
    adapter, _ = _make(name, tmp_path)
    migs = adapter.list_migrations()
    assert len(migs) == 2
    first, second = migs
    assert first.parent is None
    assert second.parent == first.id
    assert first.id != second.id
    for m in migs:
        assert m.path.is_file(), m.path
    dirs = adapter.migration_dirs()
    assert dirs and all(d.is_dir() for d in dirs)
    assert all(any(m.path.parent == d for d in dirs) for m in migs)


@pytest.mark.parametrize("name", NAMES)
def test_render_sql_only_this_migration(name, tmp_path):
    adapter, _ = _make(name, tmp_path)
    first, second = adapter.list_migrations()
    sql = adapter.render_sql(second.id)
    assert re.search(r"ADD COLUMN\s+note", sql, re.I), sql
    assert not re.search(r"CREATE TABLE\s+widgets", sql, re.I), sql
    assert re.search(r"CREATE TABLE\s+widgets", adapter.render_sql(first.id), re.I)


@pytest.mark.parametrize("name", NAMES)
def test_conventions_hint(name, tmp_path):
    adapter, _ = _make(name, tmp_path)
    hint = adapter.conventions_hint()
    assert isinstance(hint, str) and hint.strip()


@pytest.mark.parametrize("name", NAMES)
def test_caller_patterns_are_valid_regexes(name, tmp_path):
    adapter, _ = _make(name, tmp_path)
    for p in adapter.caller_patterns("widgets", "note"):
        re.compile(p)


def _columns(snapshot, table):
    t = snapshot["tables"].get(table)
    return None if t is None else {c["name"] for c in t["columns"]}


@pytest.mark.parametrize("name", NAMES)
def test_upgrade_downgrade_roundtrip(name, tmp_path, pg_dialect, pg_dsn):
    adapter, _ = _make(name, tmp_path)
    first, _second = adapter.list_migrations()

    adapter.upgrade(pg_dsn, "head")
    assert _columns(pg_dialect.schema_snapshot(pg_dsn), "widgets") == {"id", "note"}

    adapter.downgrade(pg_dsn, first.id)
    assert _columns(pg_dialect.schema_snapshot(pg_dsn), "widgets") == {"id"}

    adapter.downgrade(pg_dsn, "base")
    assert _columns(pg_dialect.schema_snapshot(pg_dsn), "widgets") is None

    adapter.upgrade(pg_dsn, "head")
    assert _columns(pg_dialect.schema_snapshot(pg_dsn), "widgets") == {"id", "note"}


@pytest.mark.parametrize("name", NAMES)
def test_upgrade_failure_raises(name, tmp_path, pg_dsn):
    from sqlalchemy.engine import make_url

    adapter, _ = _make(name, tmp_path)
    bad = make_url(pg_dsn).set(database="sg_does_not_exist")
    with pytest.raises(Exception):
        adapter.upgrade(bad.render_as_string(hide_password=False), "head")
