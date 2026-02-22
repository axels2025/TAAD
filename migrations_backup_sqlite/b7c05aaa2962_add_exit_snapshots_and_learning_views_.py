"""add_exit_snapshots_and_learning_views_phase_2_6e

Revision ID: b7c05aaa2962
Revises: 56f454469066
Create Date: 2026-01-31 07:33:36.157593

Phase 2.6E - Exit Snapshots & Learning Data Preparation
Creates trade_exit_snapshots table and trade_learning_data view.
Completes the data collection cycle: entry -> position monitoring -> exit.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'b7c05aaa2962'
down_revision: Union[str, None] = '56f454469066'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create exit snapshots table and learning data view."""

    # ============================================================
    # Create trade_exit_snapshots table
    # ============================================================
    op.create_table(
        "trade_exit_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("trade_id", sa.Integer(), nullable=False, unique=True),

        # Exit details
        sa.Column("exit_date", sa.DateTime(), nullable=False),
        sa.Column("exit_premium", sa.Float(), nullable=False),
        sa.Column("exit_reason", sa.String(50), nullable=False),  # profit_target, stop_loss, expiration, manual

        # Outcome
        sa.Column("days_held", sa.Integer(), nullable=True),
        sa.Column("gross_profit", sa.Float(), nullable=True),
        sa.Column("net_profit", sa.Float(), nullable=True),
        sa.Column("roi_pct", sa.Float(), nullable=True),
        sa.Column("roi_on_margin", sa.Float(), nullable=True),
        sa.Column("win", sa.Boolean(), nullable=True),
        sa.Column("max_profit_captured_pct", sa.Float(), nullable=True),

        # Context changes during trade
        sa.Column("exit_iv", sa.Float(), nullable=True),
        sa.Column("iv_change_during_trade", sa.Float(), nullable=True),
        sa.Column("stock_price_at_exit", sa.Float(), nullable=True),
        sa.Column("stock_change_during_trade_pct", sa.Float(), nullable=True),
        sa.Column("vix_at_exit", sa.Float(), nullable=True),
        sa.Column("vix_change_during_trade", sa.Float(), nullable=True),

        # Path analysis (from position snapshots)
        sa.Column("closest_to_strike_pct", sa.Float(), nullable=True),  # Min distance during trade
        sa.Column("max_drawdown_pct", sa.Float(), nullable=True),
        sa.Column("max_profit_pct", sa.Float(), nullable=True),

        # Learning features
        sa.Column("trade_quality_score", sa.Float(), nullable=True),
        sa.Column("risk_adjusted_return", sa.Float(), nullable=True),

        sa.Column("captured_at", sa.DateTime(), nullable=False),

        # Foreign key to trades table
        sa.ForeignKeyConstraint(
            ["trade_id"], ["trades.id"],
            name="fk_exit_snapshot_trade",
            ondelete="CASCADE"
        ),
    )

    # Create indexes
    op.create_index("ix_exit_snapshots_trade_id", "trade_exit_snapshots", ["trade_id"])
    op.create_index("ix_exit_snapshots_win", "trade_exit_snapshots", ["win"])
    op.create_index("ix_exit_snapshots_exit_reason", "trade_exit_snapshots", ["exit_reason"])
    op.create_index("ix_exit_snapshots_exit_date", "trade_exit_snapshots", ["exit_date"])

    # ============================================================
    # Create trade_learning_data view
    # ============================================================
    # This view joins entry, exit, and aggregates position data
    # for easy consumption by the learning engine

    op.execute("""
        CREATE VIEW IF NOT EXISTS trade_learning_data AS
        SELECT
            t.id as trade_id,
            t.symbol,
            t.trade_id as trade_identifier,

            -- Entry features (predictors)
            e.delta as entry_delta,
            e.iv as entry_iv,
            e.iv_rank as entry_iv_rank,
            e.dte as entry_dte,
            e.otm_pct as entry_otm_pct,
            e.margin_efficiency_pct,
            e.trend_direction,
            e.rsi_14,
            e.macd,
            e.adx,
            e.bb_position,
            e.vix as entry_vix,
            e.vol_regime,
            e.market_regime,
            e.days_to_earnings,
            e.earnings_in_dte,
            e.earnings_timing,
            e.sector,
            e.is_opex_week,
            e.day_of_week,
            e.data_quality_score,

            -- Outcome (target variables)
            x.win,
            x.roi_pct,
            x.roi_on_margin,
            x.days_held,
            x.exit_reason,
            x.trade_quality_score,
            x.iv_change_during_trade as iv_crush,
            x.closest_to_strike_pct as min_buffer,
            x.max_drawdown_pct,
            x.max_profit_pct,
            x.gross_profit,

            -- Dates
            t.entry_date,
            x.exit_date

        FROM trades t
        JOIN trade_entry_snapshots e ON t.id = e.trade_id
        JOIN trade_exit_snapshots x ON t.id = x.trade_id
        WHERE e.data_quality_score >= 0.3
    """)


def downgrade() -> None:
    """Remove exit snapshots table and learning view."""

    # Drop view first
    op.execute("DROP VIEW IF EXISTS trade_learning_data")

    # Drop indexes
    op.drop_index("ix_exit_snapshots_exit_date", table_name="trade_exit_snapshots")
    op.drop_index("ix_exit_snapshots_exit_reason", table_name="trade_exit_snapshots")
    op.drop_index("ix_exit_snapshots_win", table_name="trade_exit_snapshots")
    op.drop_index("ix_exit_snapshots_trade_id", table_name="trade_exit_snapshots")

    # Drop table
    op.drop_table("trade_exit_snapshots")
