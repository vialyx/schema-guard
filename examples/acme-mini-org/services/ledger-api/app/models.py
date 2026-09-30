"""SQLAlchemy 2.0 models. Must match migrations/versions exactly (head = 0004).

Conventions: money is BigInteger cents, weights are Numeric(14, 3) kilograms.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, Index, Numeric, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_no: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    supplier_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    material_grade: Mapped[str] = mapped_column(Text, nullable=False)
    net_weight_kg: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    price_per_kg_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'open'"))
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class InventoryMovement(Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (Index("ix_inventory_movements_moved_at", "moved_at"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    order_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("orders.id", name="fk_inventory_movements_order_id"), nullable=True
    )
    yard_code: Mapped[str] = mapped_column(Text, nullable=False)
    quantity_kg: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    moved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class LedgerEntry(Base):
    """Append-only. Corrections are reversing entries, never UPDATE/DELETE."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'posted')", name="ck_ledger_entries_status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    account_code: Mapped[str] = mapped_column(Text, nullable=False)
    order_id: Mapped[Optional[int]] = mapped_column(
        BigInteger, ForeignKey("orders.id", name="fk_ledger_entries_order_id"), nullable=True
    )
    amount_cents: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    posted_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    memo: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    actor: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    payload: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class OrderImportStaging(Base):
    """Scratch table used by the order importer (and read by legacy-sync)."""

    __tablename__ = "order_import_staging"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    imported_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
