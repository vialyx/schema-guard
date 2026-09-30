"""create inventory_movements

Revision ID: 0002
Revises: 0001
Create Date: 2026-02-03 09:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column(
            "order_id",
            sa.BigInteger(),
            sa.ForeignKey("orders.id", name="fk_inventory_movements_order_id"),
            nullable=True,
        ),
        sa.Column("yard_code", sa.Text(), nullable=False),
        sa.Column("quantity_kg", sa.Numeric(14, 3), nullable=False),
        sa.Column("moved_at", sa.DateTime(timezone=True), server_default=sa.text("now()")),
    )
    op.create_index("ix_inventory_movements_moved_at", "inventory_movements", ["moved_at"])


def downgrade() -> None:
    op.drop_index("ix_inventory_movements_moved_at", table_name="inventory_movements")
    op.drop_table("inventory_movements")
