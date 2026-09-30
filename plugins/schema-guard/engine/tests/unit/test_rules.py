"""Policy rules: each one gets positives, negatives and a fix for every blocking finding."""

from __future__ import annotations

from dataclasses import replace

import pytest

from sg.checks.policy import PolicyCheck
from sg.core.models import Context, Finding, Status
from sg.core.sql import parse
from sg.rules.base import Rule
from sg.rules.destructive_on_ledger import DestructiveOnLedger
from sg.rules.edit_applied_migration import EditAppliedMigration
from sg.rules.float_on_money import FloatOnMoney
from sg.rules.index_not_concurrent import IndexNotConcurrent
from sg.rules.mutate_posted_rows import MutatePostedRows
from sg.rules.notnull_on_hot_table import NotNullOnHotTable
from sg.rules.unbatched_backfill import UnbatchedBackfill

from conftest import make_ctx, make_policy

ALL_RULES = [FloatOnMoney, DestructiveOnLedger, MutatePostedRows, NotNullOnHotTable,
             IndexNotConcurrent, EditAppliedMigration, UnbatchedBackfill]


def run(rule_cls: type[Rule], sql: str, *, regex_only: bool = False, **kw) -> list[Finding]:
    """Run one rule over one migration. `regex_only` simulates sqlglot failing on every statement."""
    ctx = make_ctx(sql, **kw)
    m = ctx.changed[0]
    stmts = parse(ctx.pending_sql[m.id], "postgres")
    if regex_only:
        stmts = [replace(s, tree=None) for s in stmts]
    return rule_cls(ctx.policy.rule_config("")).check(m, stmts, ctx)


def only(findings: list[Finding]) -> Finding:
    assert len(findings) == 1, findings
    return findings[0]


def assert_fix(findings: list[Finding]) -> None:
    for f in findings:
        if f.severity in (Status.REFUSE, Status.ASK):
            assert f.fix.strip(), f"{f.rule} {f.severity.value} without a fix"


# --- float-on-money -------------------------------------------------------------

def test_float_price_per_ton_is_refused():
    f = only(run(FloatOnMoney, "ALTER TABLE orders ADD COLUMN price_per_ton double precision;"))
    assert (f.rule, f.severity) == ("float-on-money", Status.REFUSE)
    assert "NUMERIC" in f.fix and "_cents" in f.fix
    assert f.evidence == "0042_change.sql: ALTER TABLE orders ADD COLUMN price_per_ton double precision"


@pytest.mark.parametrize("sql", [
    "ALTER TABLE orders ALTER COLUMN total_amount TYPE real USING total_amount::real",
    "ALTER TABLE inventory_movements ADD COLUMN weight_kg float8 NOT NULL DEFAULT 0",
    "CREATE TABLE shipments (id bigint PRIMARY KEY, freight_cost float(53), note text)",
])
def test_float_on_money_or_quantity_variants_refused(sql):
    f = only(run(FloatOnMoney, sql))
    assert f.severity is Status.REFUSE and f.fix


def test_float_on_other_column_warns():
    f = only(run(FloatOnMoney, "ALTER TABLE orders ADD COLUMN latitude double precision"))
    assert f.severity is Status.WARN


@pytest.mark.parametrize("sql", [
    "ALTER TABLE orders ADD COLUMN price_per_ton numeric(12,4)",
    "ALTER TABLE orders ADD COLUMN total_cents bigint NOT NULL DEFAULT 0",
    "CREATE TABLE shipments (id bigint, freight_cost numeric(12,2))",
])
def test_exact_types_pass(sql):
    assert run(FloatOnMoney, sql) == []


# --- destructive-change ---------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "ALTER TABLE ledger_entries DROP COLUMN memo",
    "ALTER TABLE ledger_entries ALTER COLUMN amount_cents TYPE numeric(20,0)",
    "ALTER TABLE audit_log RENAME COLUMN actor TO actor_id",
    "ALTER TABLE ledger_entries RENAME TO ledger_entries_old",
    "DROP TABLE audit_log",
    "TRUNCATE ledger_entries",
])
def test_destructive_on_protected_is_refused(sql):
    f = only(run(DestructiveOnLedger, sql))
    assert (f.rule, f.severity) == ("destructive-change", Status.REFUSE)
    assert "expand/contract" in f.fix and "contract migration" in f.fix


@pytest.mark.parametrize("sql", [
    "ALTER TABLE orders DROP COLUMN legacy_ref",
    "ALTER TABLE inventory_movements RENAME COLUMN qty TO quantity",
    "DROP TABLE some_unclassified_table",
])
def test_destructive_elsewhere_asks(sql):
    f = only(run(DestructiveOnLedger, sql))
    assert f.severity is Status.ASK
    assert "contract migration" in f.fix


def test_drop_scratch_table_is_fine():
    assert run(DestructiveOnLedger, "DROP TABLE order_import_staging") == []


def test_additive_change_is_not_destructive():
    assert run(DestructiveOnLedger, "ALTER TABLE ledger_entries ADD COLUMN memo text") == []


def test_drop_table_lists_every_table():
    findings = run(DestructiveOnLedger, "DROP TABLE order_import_staging, ledger_entries CASCADE")
    assert [f.severity for f in findings] == [Status.REFUSE]


def test_drop_and_recreate_is_still_judged():
    sql = "DROP TABLE ledger_entries; CREATE TABLE ledger_entries (id bigint, amount_cents bigint);"
    assert only(run(DestructiveOnLedger, sql)).severity is Status.REFUSE


# --- mutate-ledger-rows ---------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "UPDATE ledger_entries SET amount_cents = amount_cents * 100",
    "UPDATE ledger_entries SET memo = 'x' WHERE id = 7",
    "DELETE FROM audit_log WHERE created_at < now() - interval '7 years'",
])
def test_mutating_protected_rows_is_refused(sql):
    f = only(run(MutatePostedRows, sql))
    assert (f.rule, f.severity) == ("mutate-ledger-rows", Status.REFUSE)
    assert "compensating" in f.fix


def test_insert_into_ledger_is_fine():
    assert run(MutatePostedRows, "INSERT INTO ledger_entries (id, amount_cents) VALUES (1, 100)") == []


def test_update_on_core_table_is_not_a_ledger_mutation():
    assert run(MutatePostedRows, "UPDATE orders SET status = 'open' WHERE id = 1") == []


# --- not-null-on-existing-table -------------------------------------------------

def test_add_not_null_without_default_on_hot_table_asks():
    f = only(run(NotNullOnHotTable, "ALTER TABLE inventory_movements ADD COLUMN source_yard_id bigint NOT NULL"))
    assert (f.rule, f.severity) == ("not-null-on-existing-table", Status.ASK)
    assert "NOT VALID" in f.fix and "VALIDATE CONSTRAINT" in f.fix


@pytest.mark.parametrize("sql", [
    "ALTER TABLE inventory_movements ADD COLUMN source_yard_id bigint NOT NULL DEFAULT 0",
    "ALTER TABLE inventory_movements ADD COLUMN source_yard_id bigint",
    "ALTER TABLE inventory_movements ADD COLUMN seq bigint GENERATED ALWAYS AS IDENTITY NOT NULL",
    "CREATE TABLE yards (id bigint NOT NULL); ALTER TABLE yards ADD COLUMN code text NOT NULL",
    "ALTER TABLE inventory_movements ADD CONSTRAINT yard_nn CHECK (source_yard_id IS NOT NULL) NOT VALID",
])
def test_safe_not_null_patterns_pass(sql):
    assert run(NotNullOnHotTable, sql) == []


@pytest.mark.parametrize("table,expected", [
    ("inventory_movements", Status.ASK),  # hot
    ("ledger_entries", Status.ASK),  # size unknown
    ("mystery_table", Status.ASK),  # not in policy at all
    ("orders", Status.WARN),  # 250k, known
    ("order_import_staging", Status.WARN),  # scratch
])
def test_set_not_null_depends_on_size(table, expected):
    f = only(run(NotNullOnHotTable, f"ALTER TABLE {table} ALTER COLUMN source_yard_id SET NOT NULL"))
    assert f.severity is expected and "CHECK" in f.fix


# --- index-not-concurrent -------------------------------------------------------

def test_plain_index_on_hot_table_asks():
    f = only(run(IndexNotConcurrent, "CREATE INDEX ix_im_yard ON inventory_movements (source_yard_id)"))
    assert (f.rule, f.severity) == ("index-not-concurrent", Status.ASK)
    assert "CONCURRENTLY" in f.fix and "autocommit_block" in f.fix


def test_plain_index_on_small_known_table_warns():
    assert only(run(IndexNotConcurrent, "CREATE UNIQUE INDEX ix_o ON orders (external_ref)")).severity is Status.WARN


def test_concurrent_index_passes():
    assert run(IndexNotConcurrent, "CREATE INDEX CONCURRENTLY ix_im_yard ON inventory_movements (source_yard_id)") == []


def test_index_on_table_created_in_same_migration_passes():
    sql = """
        CREATE TABLE yard_transfers (id bigint PRIMARY KEY, yard_id bigint NOT NULL, weight_kg numeric(12,3));
        CREATE INDEX ix_yt_yard ON yard_transfers (yard_id);
        ALTER TABLE yard_transfers ADD CONSTRAINT fk_yard FOREIGN KEY (yard_id) REFERENCES yards (id);
    """
    assert run(IndexNotConcurrent, sql) == []


def test_foreign_key_without_not_valid_on_hot_table_asks():
    f = only(run(IndexNotConcurrent,
                 "ALTER TABLE inventory_movements ADD CONSTRAINT fk_yard FOREIGN KEY (source_yard_id) REFERENCES yards (id)"))
    assert f.severity is Status.ASK and "NOT VALID" in f.fix


def test_foreign_key_not_valid_passes():
    sql = ("ALTER TABLE inventory_movements ADD CONSTRAINT fk_yard FOREIGN KEY (source_yard_id) "
           "REFERENCES yards (id) NOT VALID")
    assert run(IndexNotConcurrent, sql) == []


# --- edit-shipped-migration -----------------------------------------------------

def test_modified_migration_is_refused_regardless_of_sql():
    f = only(run(EditAppliedMigration, "SELECT 1", modified=True))
    assert (f.rule, f.severity) == ("edit-shipped-migration", Status.REFUSE)
    assert "new migration" in f.fix


def test_unmodified_migration_passes():
    assert run(EditAppliedMigration, "DROP TABLE ledger_entries") == []


# --- unbatched-backfill ---------------------------------------------------------

@pytest.mark.parametrize("sql", [
    "UPDATE inventory_movements SET source_yard_id = 1",
    "UPDATE inventory_movements SET source_yard_id = 1 WHERE source_yard_id IS NULL",
    "DELETE FROM inventory_movements WHERE created_at < '2020-01-01'",
])
def test_unbounded_change_on_hot_table_asks(sql):
    f = only(run(UnbatchedBackfill, sql))
    assert (f.rule, f.severity) == ("unbatched-backfill", Status.ASK)
    assert "batches" in f.fix


@pytest.mark.parametrize("sql", [
    "UPDATE inventory_movements SET source_yard_id = 1 WHERE id BETWEEN 1 AND 10000",
    "UPDATE inventory_movements SET source_yard_id = 1 WHERE id >= 1 AND id < 10001",
    "UPDATE inventory_movements SET source_yard_id = 1 WHERE id IN (SELECT id FROM inventory_movements WHERE source_yard_id IS NULL LIMIT 10000)",
    "UPDATE orders SET status = 'open' WHERE status IS NULL",  # not hot, scoped
    "UPDATE order_import_staging SET done = true",  # scratch
    "UPDATE ledger_entries SET memo = ''",  # mutate-ledger-rows' job, not this rule's
])
def test_bounded_or_out_of_scope_changes_pass(sql):
    assert run(UnbatchedBackfill, sql) == []


def test_update_without_where_on_core_table_warns():
    assert only(run(UnbatchedBackfill, "UPDATE orders SET status = 'open'")).severity is Status.WARN


def test_delete_everything_on_core_table_asks():
    f = only(run(UnbatchedBackfill, "DELETE FROM orders"))
    assert f.severity is Status.ASK and f.fix


def test_subquery_where_is_not_the_statement_where():
    sql = "UPDATE inventory_movements SET yard = (SELECT y.name FROM yards y WHERE y.id = 1)"
    assert only(run(UnbatchedBackfill, sql)).severity is Status.ASK


# --- cross-cutting --------------------------------------------------------------

BAD_SQL = [
    "ALTER TABLE orders ADD COLUMN price_per_ton double precision",
    "ALTER TABLE ledger_entries DROP COLUMN memo",
    "UPDATE ledger_entries SET memo = ''",
    "ALTER TABLE inventory_movements ADD COLUMN source_yard_id bigint NOT NULL",
    "CREATE INDEX ix ON inventory_movements (source_yard_id)",
    "UPDATE inventory_movements SET source_yard_id = 1",
]


@pytest.mark.parametrize("sql", BAD_SQL)
def test_rules_still_work_when_sqlglot_fails(sql):
    for rule_cls in ALL_RULES:
        assert run(rule_cls, sql, regex_only=True) == run(rule_cls, sql)
    assert any(run(rule_cls, sql, regex_only=True) for rule_cls in ALL_RULES)


@pytest.mark.parametrize("sql", BAD_SQL)
def test_every_blocking_finding_has_a_fix(sql):
    for rule_cls in ALL_RULES:
        assert_fix(run(rule_cls, sql))


def test_severity_override_sets_headline_and_caps_the_rest():
    ctx = make_ctx("ALTER TABLE ledger_entries DROP COLUMN memo; ALTER TABLE orders DROP COLUMN x;")
    m = ctx.changed[0]
    rule = DestructiveOnLedger({"severity": "warn"})
    assert {f.severity for f in rule.check(m, parse(ctx.pending_sql[m.id], "postgres"), ctx)} == {Status.WARN}


# --- Alembic offline SQL --------------------------------------------------------

ALEMBIC_SAFE = """\
BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL,
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade 0041 -> 0042

ALTER TABLE orders ADD COLUMN price_per_ton_cents BIGINT;

UPDATE alembic_version SET version_num='0042' WHERE alembic_version.version_num = '0041';

COMMIT;
"""

ALEMBIC_BAD = """\
BEGIN;

-- Running upgrade 0042 -> 0043

ALTER TABLE orders ADD COLUMN price_per_ton DOUBLE PRECISION;

UPDATE ledger_entries SET amount_cents = 0 WHERE id = 1;

ALTER TABLE inventory_movements ADD COLUMN source_yard_id BIGINT NOT NULL;

UPDATE alembic_version SET version_num='0043' WHERE alembic_version.version_num = '0042';

COMMIT;
"""


def _policy_check(ctx: Context):
    return PolicyCheck().run_safely(ctx)


def test_alembic_noise_is_ignored():
    result = _policy_check(make_ctx(ALEMBIC_SAFE))
    assert result.status is Status.PASS, result.findings
    assert result.details["statements"] == 1
    assert result.summary == "no findings across 1 migration"


def test_policy_check_aggregates_rules():
    result = _policy_check(make_ctx(ALEMBIC_BAD))
    rules = sorted(f.rule for f in result.findings)
    assert rules == ["float-on-money", "mutate-ledger-rows", "not-null-on-existing-table"]
    assert result.status is Status.REFUSE
    assert result.summary == "2 refuse, 1 ask across 1 migration"
    assert result.details["statements"] == 3
    assert set(result.details["rules_run"]) == {r.id for r in ALL_RULES}
    assert (PolicyCheck.name, PolicyCheck.order, PolicyCheck.requires) == ("policy", 10, frozenset())
    assert_fix(result.findings)


def test_policy_check_includes_modified_migrations():
    result = _policy_check(make_ctx("ALTER TABLE orders ADD COLUMN note text", modified=True))
    assert [f.rule for f in result.findings] == ["edit-shipped-migration"]
    assert result.status is Status.REFUSE


def test_disabled_rule_is_skipped():
    policy = make_policy(rules={"float_on_money": {"enabled": False}})
    result = _policy_check(make_ctx("ALTER TABLE orders ADD COLUMN price_per_ton real", policy=policy))
    assert result.status is Status.PASS
    assert "float-on-money" not in result.details["rules_run"]


def test_unparsable_statement_warns_only_when_regex_is_blind_too():
    sql = "DO $$ BEGIN PERFORM 1; END $$; ALTER TABLE orders VALIDATE CONSTRAINT fk_x;"
    result = _policy_check(make_ctx(sql))
    assert [f.rule for f in result.findings] == ["unparsed-statement"]
    assert result.findings[0].severity is Status.WARN
    assert result.status is Status.WARN


def test_missing_rendered_sql_is_not_a_pass():
    ctx = make_ctx("SELECT 1")
    ctx.pending_sql.clear()
    result = _policy_check(ctx)
    assert result.status is Status.ASK
