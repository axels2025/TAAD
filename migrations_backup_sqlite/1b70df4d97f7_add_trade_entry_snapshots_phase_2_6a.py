"""add_trade_entry_snapshots_phase_2_6a

Revision ID: 1b70df4d97f7
Revises: a1598d212799
Create Date: 2026-01-29 21:27:59.487307

Phase 2.6A - Critical Fields Data Collection
Creates trade_entry_snapshots table with 66 fields for learning engine.
Captures the 8 critical fields with ~80% predictive power:
1. delta (IBKR Greeks)
2. iv (IBKR Greeks)
3. iv_rank (calculated)
4. vix (IBKR market)
5. dte (calculated)
6. trend_direction (calculated)
7. days_to_earnings (external API)
8. margin_efficiency_pct (calculated from actual IBKR margin)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1b70df4d97f7'
down_revision: Union[str, None] = 'a1598d212799'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Create trade_entry_snapshots table with all Phase 2.6A fields."""
    op.create_table(
        "trade_entry_snapshots",
        # Primary key and foreign keys
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trade_id", sa.Integer(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),

        # ============================================================
        # CATEGORY 1: Option Contract Data (13 fields)
        # ============================================================
        sa.Column("symbol", sa.String(10), nullable=False),
        sa.Column("strike", sa.Float(), nullable=False),
        sa.Column("expiration", sa.Date(), nullable=False),
        sa.Column("option_type", sa.String(10), nullable=False),  # PUT/CALL

        # Pricing
        sa.Column("bid", sa.Float(), nullable=True),
        sa.Column("ask", sa.Float(), nullable=True),
        sa.Column("mid", sa.Float(), nullable=True),  # (bid + ask) / 2
        sa.Column("entry_premium", sa.Float(), nullable=False),  # Actual fill price
        sa.Column("spread_pct", sa.Float(), nullable=True),  # (ask - bid) / mid

        # Greeks from IBKR (5 Greeks)
        sa.Column("delta", sa.Float(), nullable=True),  # *** CRITICAL FIELD #1 ***
        sa.Column("gamma", sa.Float(), nullable=True),
        sa.Column("theta", sa.Float(), nullable=True),
        sa.Column("vega", sa.Float(), nullable=True),
        sa.Column("rho", sa.Float(), nullable=True),

        # ============================================================
        # CATEGORY 2: Volatility Data (5 fields)
        # ============================================================
        sa.Column("iv", sa.Float(), nullable=True),  # *** CRITICAL FIELD #2 ***
        sa.Column("iv_rank", sa.Float(), nullable=True),  # *** CRITICAL FIELD #3 ***
        sa.Column("iv_percentile", sa.Float(), nullable=True),
        sa.Column("hv_20", sa.Float(), nullable=True),  # 20-day historical volatility
        sa.Column("iv_hv_ratio", sa.Float(), nullable=True),  # iv / hv_20

        # ============================================================
        # CATEGORY 3: Liquidity (3 fields)
        # ============================================================
        sa.Column("option_volume", sa.Integer(), nullable=True),
        sa.Column("open_interest", sa.Integer(), nullable=True),
        sa.Column("volume_oi_ratio", sa.Float(), nullable=True),  # volume / open_interest

        # ============================================================
        # CATEGORY 4: Underlying - Prices (6 fields)
        # ============================================================
        sa.Column("stock_price", sa.Float(), nullable=False),
        sa.Column("stock_open", sa.Float(), nullable=True),
        sa.Column("stock_high", sa.Float(), nullable=True),
        sa.Column("stock_low", sa.Float(), nullable=True),
        sa.Column("stock_prev_close", sa.Float(), nullable=True),
        sa.Column("stock_change_pct", sa.Float(), nullable=True),  # (price - prev_close) / prev_close

        # ============================================================
        # CATEGORY 5: Underlying - Calculated Metrics (6 fields)
        # ============================================================
        sa.Column("otm_pct", sa.Float(), nullable=True),  # (stock_price - strike) / stock_price
        sa.Column("otm_dollars", sa.Float(), nullable=True),  # stock_price - strike
        sa.Column("dte", sa.Integer(), nullable=False),  # *** CRITICAL FIELD #5 ***
        sa.Column("margin_requirement", sa.Float(), nullable=True),  # ACTUAL from IBKR whatIfOrder
        sa.Column("margin_efficiency_pct", sa.Float(), nullable=True),  # *** CRITICAL FIELD #8 ***
        sa.Column("contracts", sa.Integer(), nullable=False),  # Number of contracts

        # ============================================================
        # CATEGORY 6: Underlying - Trend (6 fields)
        # ============================================================
        sa.Column("sma_20", sa.Float(), nullable=True),  # 20-day simple moving average
        sa.Column("sma_50", sa.Float(), nullable=True),  # 50-day simple moving average
        sa.Column("trend_direction", sa.String(20), nullable=True),  # *** CRITICAL FIELD #6 ***
        sa.Column("trend_strength", sa.Float(), nullable=True),  # Confidence score 0-1
        sa.Column("price_vs_sma20_pct", sa.Float(), nullable=True),  # (price - sma_20) / sma_20
        sa.Column("price_vs_sma50_pct", sa.Float(), nullable=True),  # (price - sma_50) / sma_50

        # ============================================================
        # CATEGORY 7: Market Data (4 fields)
        # ============================================================
        sa.Column("spy_price", sa.Float(), nullable=True),
        sa.Column("spy_change_pct", sa.Float(), nullable=True),
        sa.Column("vix", sa.Float(), nullable=True),  # *** CRITICAL FIELD #4 ***
        sa.Column("vix_change_pct", sa.Float(), nullable=True),

        # ============================================================
        # CATEGORY 8: Event Data (3 fields)
        # ============================================================
        sa.Column("earnings_date", sa.Date(), nullable=True),
        sa.Column("days_to_earnings", sa.Integer(), nullable=True),  # *** CRITICAL FIELD #7 ***
        sa.Column("earnings_in_dte", sa.Boolean(), nullable=True),  # True if earnings before expiration

        # ============================================================
        # CATEGORY 9: Metadata (4 fields)
        # ============================================================
        sa.Column("captured_at", sa.DateTime(), nullable=False),
        sa.Column("data_quality_score", sa.Float(), nullable=True),  # 0.0-1.0, based on field completeness
        sa.Column("source", sa.String(50), nullable=True),  # 'manual', 'scan', 'auto'
        sa.Column("notes", sa.Text(), nullable=True),

        # Constraints
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["trade_id"], ["trades.id"],
            name="fk_entry_snapshot_trade",
            ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["scan_opportunities.id"],
            name="fk_entry_snapshot_opportunity",
            ondelete="SET NULL"
        ),
    )

    # ============================================================
    # Indexes for Learning Engine Queries
    # ============================================================

    # Foreign key indexes
    op.create_index(
        "ix_entry_snapshots_trade_id",
        "trade_entry_snapshots",
        ["trade_id"],
    )
    op.create_index(
        "ix_entry_snapshots_opportunity_id",
        "trade_entry_snapshots",
        ["opportunity_id"],
    )

    # Critical field indexes (for fast pattern queries)
    op.create_index(
        "ix_entry_snapshots_delta",
        "trade_entry_snapshots",
        ["delta"],
    )
    op.create_index(
        "ix_entry_snapshots_iv_rank",
        "trade_entry_snapshots",
        ["iv_rank"],
    )
    op.create_index(
        "ix_entry_snapshots_vix",
        "trade_entry_snapshots",
        ["vix"],
    )
    op.create_index(
        "ix_entry_snapshots_trend_direction",
        "trade_entry_snapshots",
        ["trend_direction"],
    )
    op.create_index(
        "ix_entry_snapshots_dte",
        "trade_entry_snapshots",
        ["dte"],
    )
    op.create_index(
        "ix_entry_snapshots_days_to_earnings",
        "trade_entry_snapshots",
        ["days_to_earnings"],
    )
    op.create_index(
        "ix_entry_snapshots_margin_efficiency",
        "trade_entry_snapshots",
        ["margin_efficiency_pct"],
    )

    # Composite indexes for common learning queries
    op.create_index(
        "ix_entry_snapshots_delta_ivrank",
        "trade_entry_snapshots",
        ["delta", "iv_rank"],
    )
    op.create_index(
        "ix_entry_snapshots_symbol_captured",
        "trade_entry_snapshots",
        ["symbol", "captured_at"],
    )

    # Time-based index
    op.create_index(
        "ix_entry_snapshots_captured_at",
        "trade_entry_snapshots",
        ["captured_at"],
    )


def downgrade() -> None:
    """Remove trade_entry_snapshots table and all indexes."""
    # Drop all indexes
    op.drop_index("ix_entry_snapshots_captured_at", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_symbol_captured", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_delta_ivrank", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_margin_efficiency", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_days_to_earnings", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_dte", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_trend_direction", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_vix", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_iv_rank", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_delta", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_opportunity_id", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_trade_id", table_name="trade_entry_snapshots")

    # Drop table
    op.drop_table("trade_entry_snapshots")
