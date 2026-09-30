"""A new constraint breaks no reader, but writers that build SQL at runtime may start failing."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from conftest import make_ctx

from sg.checks.callers import CallersCheck
from sg.core.models import Status
from sg.core.sql import alter_table_actions

SYNC = '''
def pull(conn, field, value, order_id):
    conn.execute(f"UPDATE orders SET {field} = %s WHERE id = %s", (value, order_id))
'''


def test_add_constraint_is_its_own_op():
    ops = [a.touch.op for a in alter_table_actions(
        "ALTER TABLE orders ADD CONSTRAINT ck_d CHECK (d <= net_weight_kg) NOT VALID")]
    assert ops == ["add_constraint"]
    assert alter_table_actions("ALTER TABLE t ADD UNIQUE (a)")[0].touch.op == "add_constraint"
    assert alter_table_actions("ALTER TABLE t ADD COLUMN c int")[0].touch.op == "add_column"


def _ctx(tmp_path: Path, sql: str):
    (tmp_path / "sync.py").write_text(SYNC)
    ctx = make_ctx(sql)
    ctx.repo_root = tmp_path
    ctx.adapter = SimpleNamespace(name="stub", migration_dirs=lambda: [], caller_patterns=lambda t, c: [])
    return ctx


def test_new_constraint_on_table_with_dynamic_writers_asks(tmp_path: Path):
    ctx = _ctx(tmp_path, "ALTER TABLE orders ADD CONSTRAINT ck CHECK (deduction_kg <= net_weight_kg) NOT VALID;")
    result = CallersCheck().run(ctx)
    assert result.status is Status.ASK
    assert [f.rule for f in result.findings] == ["constraint-dynamic-writers"]
    assert "sync.py:3" in result.findings[0].evidence


def test_constraint_on_a_table_created_in_the_same_change_is_fine(tmp_path: Path):
    ctx = _ctx(tmp_path, "CREATE TABLE orders (id bigint, d numeric);\n"
                         "ALTER TABLE orders ADD CONSTRAINT ck CHECK (d >= 0);")
    assert CallersCheck().run(ctx).status is Status.PASS


def test_adding_a_nullable_column_still_passes(tmp_path: Path):
    ctx = _ctx(tmp_path, "ALTER TABLE orders ADD COLUMN notes text;")
    assert CallersCheck().run(ctx).status is Status.PASS
