"""Add NakedTrader columns to trades table

Revision ID: e6f7g8h9i0j1
Revises: d5e6f7g8h9i0
Create Date: 2026-02-18 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e6f7g8h9i0j1"
down_revision: Union[str, None] = "d5e6f7g8h9i0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trades",
        sa.Column("trade_strategy", sa.String(20), nullable=True, index=True),
    )
    op.add_column(
        "trades",
        sa.Column("exit_order_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("stop_order_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "trades",
        sa.Column("bracket_status", sa.String(20), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trades", "bracket_status")
    op.drop_column("trades", "stop_order_id")
    op.drop_column("trades", "exit_order_id")
    op.drop_column("trades", "trade_strategy")
