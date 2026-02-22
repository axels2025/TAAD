"""add_position_snapshots_phase_2_6d

Revision ID: 56f454469066
Revises: 0e6f54d2f4ee
Create Date: 2026-01-31 07:32:07.006536

Phase 2.6D - Position Monitoring
Creates position_snapshots table for daily monitoring of open positions.
Captures position state, P&L, Greeks, and path data for learning engine.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '56f454469066'
down_revision: Union[str, None] = '0e6f54d2f4ee'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create position_snapshots table for daily position monitoring."""
    op.create_table(
        "position_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),

        # Position state
        sa.Column("current_premium", sa.Float(), nullable=True),
        sa.Column("current_pnl", sa.Float(), nullable=True),
        sa.Column("current_pnl_pct", sa.Float(), nullable=True),
        sa.Column("dte_remaining", sa.Integer(), nullable=True),

        # Greeks
        sa.Column("delta", sa.Float(), nullable=True),
        sa.Column("theta", sa.Float(), nullable=True),
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("vega", sa.Float(), nullable=True),
        sa.Column("iv", sa.Float(), nullable=True),

        # Underlying
        sa.Column("stock_price", sa.Float(), nullable=True),
        sa.Column("distance_to_strike_pct", sa.Float(), nullable=True),

        # Market
        sa.Column("vix", sa.Float(), nullable=True),
        sa.Column("spy_price", sa.Float(), nullable=True),

        sa.Column("captured_at", sa.DateTime(), nullable=False),

        # Foreign key to trades table
        sa.ForeignKeyConstraint(
            ["trade_id"], ["trades.id"],
            name="fk_position_snapshot_trade",
            ondelete="CASCADE"
        ),

        # Unique constraint: one snapshot per trade per day
        sa.UniqueConstraint("trade_id", "snapshot_date", name="uq_position_snapshot_trade_date"),
    )

    # Indexes for common queries
    op.create_index("ix_position_snapshots_trade_id", "position_snapshots", ["trade_id"])
    op.create_index("ix_position_snapshots_date", "position_snapshots", ["snapshot_date"])
    op.create_index("ix_position_snapshots_dte", "position_snapshots", ["dte_remaining"])


def downgrade() -> None:
    """Remove position_snapshots table."""
    op.drop_index("ix_position_snapshots_dte", table_name="position_snapshots")
    op.drop_index("ix_position_snapshots_date", table_name="position_snapshots")
    op.drop_index("ix_position_snapshots_trade_id", table_name="position_snapshots")
    op.drop_table("position_snapshots")
