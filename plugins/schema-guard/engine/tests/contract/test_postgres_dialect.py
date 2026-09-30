"""PostgresDialect: availability, snapshots, table descriptions."""

from __future__ import annotations

from samples import build_raw_sql
from sqlalchemy import create_engine

from sg.adapters.raw_sql import RawSqlAdapter
from sg.dialects.postgres import PostgresDialect


def _exec(dsn, *stmts):
    eng = create_engine(dsn)
    with eng.begin() as c:
        for s in stmts:
            c.exec_driver_sql(s.replace("%", "%%"))
    eng.dispose()


def test_available_reports_reason():
    ok, how = PostgresDialect.available()
    assert isinstance(ok, bool) and how


def test_inexact_types():
    assert {"real", "double precision", "float8"} <= PostgresDialect.inexact_numeric_types
    assert "numeric" not in PostgresDialect.inexact_numeric_types


def test_snapshot_stable_across_roundtrip(tmp_path, pg_dialect, pg_dsn):
    adapter = RawSqlAdapter(build_raw_sql(tmp_path))
    adapter.upgrade(pg_dsn)
    _exec(
        pg_dsn,
        "ALTER TABLE widgets ADD COLUMN price numeric(12,2) NOT NULL DEFAULT 0",
        "ALTER TABLE widgets ADD CONSTRAINT price_nonneg CHECK (price >= 0)",
        "CREATE INDEX widgets_note_idx ON widgets (note)",
    )
    before = pg_dialect.schema_snapshot(pg_dsn)
    assert "schema_guard_history" not in before["tables"]
    cols = {c["name"]: c for c in before["tables"]["widgets"]["columns"]}
    assert cols["price"]["data_type"] == "numeric(12,2)"
    assert cols["price"]["is_nullable"] is False
    assert cols["note"]["is_nullable"] is True

    adapter.downgrade(pg_dsn, "base")
    assert pg_dialect.schema_snapshot(pg_dsn) != before
    adapter.upgrade(pg_dsn)
    _exec(
        pg_dsn,
        "ALTER TABLE widgets ADD COLUMN price numeric(12,2) NOT NULL DEFAULT 0",
        "CREATE INDEX widgets_note_idx ON widgets (note)",
        "ALTER TABLE widgets ADD CONSTRAINT price_nonneg CHECK (price >= 0)",
    )
    assert pg_dialect.schema_snapshot(pg_dsn) == before


def test_describe_tables(tmp_path, pg_dialect, pg_dsn):
    RawSqlAdapter(build_raw_sql(tmp_path)).upgrade(pg_dsn)
    _exec(pg_dsn, "INSERT INTO widgets (id, note) VALUES (1, 'a'), (2, NULL), (3, '100%')")
    desc = pg_dialect.describe_tables(pg_dsn, ["widgets", "nope"])
    w = desc["widgets"]
    assert w["exists"] is True
    assert w["row_count"] == 3
    assert [c["name"] for c in w["columns"]] == ["id", "note"]
    assert w["columns"][0]["type"] == "bigint" and w["columns"][0]["nullable"] is False
    assert any("widgets_pkey" in i for i in w["indexes"])
    assert any(k["type"] == "p" for k in w["constraints"])
    assert desc["nope"] == {"exists": False}


def test_row_estimate(tmp_path, pg_dialect, pg_dsn):
    RawSqlAdapter(build_raw_sql(tmp_path)).upgrade(pg_dsn)
    _exec(pg_dsn, "INSERT INTO widgets (id) SELECT g FROM generate_series(1, 50) g", "ANALYZE widgets")
    assert pg_dialect.row_estimate(pg_dsn, "widgets") == 50
    assert pg_dialect.row_estimate(pg_dsn, "missing") is None
