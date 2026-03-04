"""Add stock_positions table and Trade lifecycle columns

Creates stock_positions table for tracking stock positions from option
assignments, and adds lifecycle_status, option_pnl, stock_pnl, total_pnl
columns to trades table.

Backfills existing assignment trades: sets lifecycle_status='stock_held'
and option_pnl=profit_loss for any trade with exit_reason='assignment'.

Revision ID: g8h9i0j1k2l3
Revises: f7g8h9i0j1k2
Create Date: 2026-02-20 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "g8h9i0j1k2l3"
down_revision: Union[str, None] = "f7g8h9i0j1k2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Create stock_positions table ---
    op.create_table(
        "stock_positions",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("symbol", sa.String(10), nullable=False, index=True),
        sa.Column("shares", sa.Integer(), nullable=False),
        sa.Column("cost_basis_per_share", sa.Float(), nullable=False),
        sa.Column("irs_cost_basis_per_share", sa.Float(), nullable=False),
        sa.Column(
            "origin_trade_id",
            sa.String(50),
            sa.ForeignKey("trades.trade_id"),
            nullable=False,
        ),
        sa.Column("assigned_date", sa.DateTime(), nullable=False),
        sa.Column("closed_date", sa.DateTime(), nullable=True),
        sa.Column("sale_price_per_share", sa.Float(), nullable=True),
        sa.Column("close_reason", sa.String(50), nullable=True),
        sa.Column("stock_pnl", sa.Float(), nullable=True),
        sa.Column("option_pnl", sa.Float(), nullable=True),
        sa.Column("total_pnl", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # --- Add lifecycle columns to trades ---
    op.add_column("trades", sa.Column("lifecycle_status", sa.String(20), nullable=True))
    op.add_column("trades", sa.Column("option_pnl", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("stock_pnl", sa.Float(), nullable=True))
    op.add_column("trades", sa.Column("total_pnl", sa.Float(), nullable=True))

    # --- Backfill existing assignment trades ---
    # Any trade with exit_reason='assignment' should be marked stock_held
    # with option_pnl = profit_loss
    op.execute(
        "UPDATE trades SET lifecycle_status = 'stock_held', option_pnl = profit_loss "
        "WHERE exit_reason = 'assignment' AND lifecycle_status IS NULL"
    )


def downgrade() -> None:
    op.drop_column("trades", "total_pnl")
    op.drop_column("trades", "stock_pnl")
    op.drop_column("trades", "option_pnl")
    op.drop_column("trades", "lifecycle_status")
    op.drop_table("stock_positions")
