"""Order value and ledger reconciliation. Pure functions, no database access.

Money is integer cents; weights are Decimal kilograms (NUMERIC(14,3) in the DB).
All arithmetic is done in Decimal and rounded half-up to whole cents once.
"""

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable

RECEIVABLE_ACCOUNT = "1200-AR"
KG_QUANTUM = Decimal("0.001")

# Orders in these statuses are not billed.
UNBILLED_STATUSES = frozenset({"cancelled", "rejected"})


@dataclass(frozen=True)
class Mismatch:
    order_no: str
    expected_cents: int
    booked_cents: int

    @property
    def diff_cents(self) -> int:
        return self.booked_cents - self.expected_cents


def accepted_weight_kg(order) -> Decimal:
    """Weight the buyer is billed for: net weight, or zero if the order is not billed."""
    if order.status in UNBILLED_STATUSES:
        return Decimal("0.000")
    return Decimal(order.net_weight_kg).quantize(KG_QUANTUM, rounding=ROUND_HALF_UP)


def order_value_cents(order) -> int:
    """accepted weight x price per kg, rounded half-up to whole cents."""
    value = accepted_weight_kg(order) * Decimal(order.price_per_kg_cents)
    return int(value.quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def reconcile(orders: Iterable, ledger_entries: Iterable) -> list[Mismatch]:
    """Compare each order's value with the posted receivable entries booked for it.

    Pending entries are ignored. Reversing entries (negative amounts) count,
    so a correction that nets out is not a mismatch.
    """
    booked: dict[int, int] = {}
    for entry in ledger_entries:
        if entry.account_code != RECEIVABLE_ACCOUNT or entry.status != "posted":
            continue
        if entry.order_id is None:
            continue
        booked[entry.order_id] = booked.get(entry.order_id, 0) + int(entry.amount_cents)

    mismatches: list[Mismatch] = []
    for order in sorted(orders, key=lambda o: o.order_no):
        expected = order_value_cents(order)
        actual = booked.get(order.id, 0)
        if expected != actual:
            mismatches.append(Mismatch(order.order_no, expected, actual))
    return mismatches
