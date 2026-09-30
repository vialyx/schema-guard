"""create order_import_staging (scratch table for the order importer)

Revision ID: 0004
Revises: 0003
Create Date: 2026-04-14 09:00:00

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Scratch table: the importer loads raw supplier payloads here before
    # turning them into orders. Rows are disposable.
    op.create_table(
        "order_import_staging",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("raw", postgresql.JSONB(), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("order_import_staging")
