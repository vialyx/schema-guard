"""Unit tests for app.services.reconciliation. No database needed."""

import sys
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.reconciliation import (  # noqa: E402
    RECEIVABLE_ACCOUNT,
    accepted_weight_kg,
    order_value_cents,
    reconcile,
)


def order(id=1, order_no="ACM-1", weight="1000.000", price=85, status="delivered"):
    return SimpleNamespace(
        id=id, order_no=order_no, net_weight_kg=Decimal(weight),
        price_per_kg_cents=price, status=status,
    )


def entry(order_id, amount, status="posted", account=RECEIVABLE_ACCOUNT):
    return SimpleNamespace(
        order_id=order_id, amount_cents=amount, status=status, account_code=account,
    )


def test_accepted_weight_zero_for_cancelled_order():
    assert accepted_weight_kg(order(status="cancelled")) == Decimal("0")
    assert accepted_weight_kg(order(weight="12.345")) == Decimal("12.345")


def test_order_value_is_exact_in_cents():
    assert order_value_cents(order(weight="1000.000", price=85)) == 85_000


def test_order_value_rounds_half_up():
    # 0.5 kg * 1 cent = 0.5 cent -> 1 cent (half-up, not banker's rounding)
    assert order_value_cents(order(weight="0.500", price=1)) == 1
    # 2.5 kg * 1 cent = 2.5 -> 3
    assert order_value_cents(order(weight="2.500", price=1)) == 3


def test_reconcile_matching_orders_have_no_mismatches():
    orders = [order(1, "ACM-1"), order(2, "ACM-2", weight="500.000", price=120)]
    entries = [entry(1, 85_000), entry(2, 60_000)]
    assert reconcile(orders, entries) == []


def test_reconcile_ignores_pending_and_other_accounts():
    orders = [order(1, "ACM-1")]
    entries = [entry(1, 85_000, status="pending"), entry(1, 85_000, account="4000-REV")]
    [m] = reconcile(orders, entries)
    assert (m.order_no, m.expected_cents, m.booked_cents, m.diff_cents) == ("ACM-1", 85_000, 0, -85_000)


def test_reconcile_counts_reversing_entries():
    orders = [order(1, "ACM-1")]
    entries = [entry(1, 90_000), entry(1, -90_000), entry(1, 85_000)]
    assert reconcile(orders, entries) == []
