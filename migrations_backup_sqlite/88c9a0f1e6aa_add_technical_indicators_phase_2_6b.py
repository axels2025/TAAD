"""add_technical_indicators_phase_2_6b

Revision ID: 88c9a0f1e6aa
Revises: 1b70df4d97f7
Create Date: 2026-01-31 07:21:49.049230

Phase 2.6B - Technical Indicators
Adds 18 technical indicator fields to trade_entry_snapshots table for pattern detection:
- RSI (2 fields): rsi_14, rsi_7
- MACD (3 fields): macd, macd_signal, macd_histogram
- ADX (3 fields): adx, plus_di, minus_di
- ATR (2 fields): atr_14, atr_pct
- Bollinger Bands (3 fields): bb_upper, bb_lower, bb_position
- Support/Resistance (5 fields): support_1, support_2, resistance_1, resistance_2, distance_to_support_pct
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '88c9a0f1e6aa'
down_revision: Union[str, None] = '1b70df4d97f7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add 18 technical indicator fields to trade_entry_snapshots."""

    # RSI indicators
    op.add_column("trade_entry_snapshots", sa.Column("rsi_14", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("rsi_7", sa.Float(), nullable=True))

    # MACD indicators
    op.add_column("trade_entry_snapshots", sa.Column("macd", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("macd_signal", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("macd_histogram", sa.Float(), nullable=True))

    # ADX indicators
    op.add_column("trade_entry_snapshots", sa.Column("adx", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("plus_di", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("minus_di", sa.Float(), nullable=True))

    # ATR indicators
    op.add_column("trade_entry_snapshots", sa.Column("atr_14", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("atr_pct", sa.Float(), nullable=True))

    # Bollinger Bands
    op.add_column("trade_entry_snapshots", sa.Column("bb_upper", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("bb_lower", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("bb_position", sa.Float(), nullable=True))

    # Support/Resistance levels
    op.add_column("trade_entry_snapshots", sa.Column("support_1", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("support_2", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("resistance_1", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("resistance_2", sa.Float(), nullable=True))
    op.add_column("trade_entry_snapshots", sa.Column("distance_to_support_pct", sa.Float(), nullable=True))

    # Create indexes for commonly queried technical indicators
    op.create_index("ix_entry_snapshots_rsi_14", "trade_entry_snapshots", ["rsi_14"])
    op.create_index("ix_entry_snapshots_adx", "trade_entry_snapshots", ["adx"])
    op.create_index("ix_entry_snapshots_bb_position", "trade_entry_snapshots", ["bb_position"])


def downgrade() -> None:
    """Remove technical indicator fields from trade_entry_snapshots."""

    # Drop indexes first
    op.drop_index("ix_entry_snapshots_bb_position", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_adx", table_name="trade_entry_snapshots")
    op.drop_index("ix_entry_snapshots_rsi_14", table_name="trade_entry_snapshots")

    # Drop columns
    op.drop_column("trade_entry_snapshots", "distance_to_support_pct")
    op.drop_column("trade_entry_snapshots", "resistance_2")
    op.drop_column("trade_entry_snapshots", "resistance_1")
    op.drop_column("trade_entry_snapshots", "support_2")
    op.drop_column("trade_entry_snapshots", "support_1")
    op.drop_column("trade_entry_snapshots", "bb_position")
    op.drop_column("trade_entry_snapshots", "bb_lower")
    op.drop_column("trade_entry_snapshots", "bb_upper")
    op.drop_column("trade_entry_snapshots", "atr_pct")
    op.drop_column("trade_entry_snapshots", "atr_14")
    op.drop_column("trade_entry_snapshots", "minus_di")
    op.drop_column("trade_entry_snapshots", "plus_di")
    op.drop_column("trade_entry_snapshots", "adx")
    op.drop_column("trade_entry_snapshots", "macd_histogram")
    op.drop_column("trade_entry_snapshots", "macd_signal")
    op.drop_column("trade_entry_snapshots", "macd")
    op.drop_column("trade_entry_snapshots", "rsi_7")
    op.drop_column("trade_entry_snapshots", "rsi_14")
