"""add_market_context_phase_2_6c

Revision ID: 0e6f54d2f4ee
Revises: 88c9a0f1e6aa
Create Date: 2026-01-31 07:27:30.339490

Phase 2.6C - Market Context & Events
Adds market context and event data fields to trade_entry_snapshots:
- Additional indices (QQQ, IWM)
- Sector data (sector, sector ETF, performance)
- Regime classification (volatility, market regime)
- Calendar data (day of week, OpEx week, FOMC proximity)
- Enhanced earnings data (timing: BMO/AMC, earnings_in_dte flag)
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e6f54d2f4ee'
down_revision: Union[str, None] = '88c9a0f1e6aa'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add market context and event data fields to trade_entry_snapshots."""

    # Additional indices
    op.add_column("trade_entry_snapshots", sa.Column("qqq_price", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("qqq_change_pct", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("iwm_price", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("iwm_change_pct", sa.Float(), nullable=True))

    # Sector data
    op.add_column("trade_entry_snapshots", sa.Column("sector", sa.String(50), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("sector_etf", sa.String(10), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("sector_change_1d", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("sector_change_5d", sa.Float(), nullable=True))

    # Regime classification
    op.add_column("trade_entry_snapshots", sa.Column("vol_regime", sa.String(20), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("market_regime", sa.String(20), nullable=True))

    # Calendar data
    op.add_column("trade_entry_snapshots", sa.Column("day_of_week", sa.Integer(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("is_opex_week", sa.Boolean(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("days_to_fomc", sa.Integer(), nullable=True))

    # Enhanced earnings timing
    op.add_column("trade_entry_snapshots", sa.Column("earnings_timing", sa.String(10), nullable=True))

    # Create indexes for commonly queried fields
    op.create_index("ix_entry_snapshots_sector", "trade_entry_snapshots", ["sector"])
    op.create_index("ix_entry_snapshots_vol_regime", "trade_entry_snapshots", ["vol_regime"])
    op.create_index("ix_entry_snapshots_market_regime", "trade_entry_snapshots", ["market_regime"])
    op.create_index("ix_entry_snapshots_day_of_week", "trade_entry_snapshots", ["day_of_week"])


def downgrade() -> None:
    """Remove market context and event data fields from trade_entry_snapshots."""

    # Drop indexes first
    op.drop_index("ix_entry_snapshots_day_of_week", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_market_regime", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_vol_regime", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_sector", table_name="trade_entry_snapshots")

    # Drop columns
    op.drop_column("trade_entry_snapshots", "earnings_timing")
    op.drop_column("trade_entry_snapshots", "days_to_fomc")
    op.drop_column("trade_entry_snapshots", "is_opex_week")
    op.drop_column("trade_entry_snapshots", "day_of_week")
    op.drop_column("trade_entry_snapshots", "market_regime")
    op.drop_column("trade_entry_snapshots", "vol_regime")
    op.drop_column("trade_entry_snapshots", "sector_change_5d")
    op.drop_column("trade_entry_snapshots", "sector_change_1d")
    op.drop_column("trade_entry_snapshots", "sector_etf")
    op.drop_column("trade_entry_snapshots", "sector")
    op.drop_column("trade_entry_snapshots", "iwm_change_pct")
    op.drop_column("trade_entry_snapshots", "iwm_price")
    op.drop_column("trade_entry_snapshots", "qqq_change_pct")
    op.drop_column("trade_entry_snapshots", "qqq_price")
