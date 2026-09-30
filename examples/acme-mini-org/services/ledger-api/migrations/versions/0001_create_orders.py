"""create orders

Revision ID: 0001
Revises:
Create Date: 2026-01-12 09:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "orders",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("order_no", sa.Text(), nullable=False),
        sa.Column("supplier_id", sa.BigInteger(), nullable=False),
        sa.Column("material_grade", sa.Text(), nullable=False),
        sa.Column("net_weight_kg", sa.Numeric(14, 3), nullable=False),
        sa.Column("price_per_kg_cents", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False, server_default=sa.text("'open'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
        sa.UniqueConstraint("order_no", name="uq_orders_order_no"),
    )


def downgrade() -> None:
    op.drop_table("orders")
