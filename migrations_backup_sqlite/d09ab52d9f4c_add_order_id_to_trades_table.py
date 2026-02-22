"""Add order_id to trades table

Revision ID: d09ab52d9f4c
Revises: c4a1b2e3f567
Create Date: 2026-02-03 22:36:21.396466

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import sqlite

# revision identifiers, used by Alembic.
revision: str = 'd09ab52d9f4c'
down_revision: Union[str, None] = 'c4a1b2e3f567'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add order_id column to trades table for order reconciliation
    op.add_column('trades', sa.Column('order_id', sa.Integer(), nullable=True))
    op.create_index(op.f('ix_trades_order_id'), 'trades', ['order_id'], unique=False)


def downgrade() -> None:
    # Remove order_id column from trades table
    op.drop_index(op.f('ix_trades_order_id'), table_name='trades')
    op.drop_column('trades', 'order_id')
