"""add_staging_and_validation_columns_phase_4_1

Revision ID: c4a1b2e3f567
Revises: b7c05aaa2962
Create Date: 2026-02-02 10:00:00.000000

Phase 4.1 - Sunday-to-Monday Workflow
Adds staging columns and validation tracking for the automated workflow:
- Staging fields: contracts, limit price, margin, priority
- Pre-market validation fields (Stage 1 - 9:15 AM): stock price check
- Market-open validation fields (Stage 2 - 9:30 AM): premium check
- Execution session tracking
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c4a1b2e3f567'
down_revision: Union[str, None] = 'b7c05aaa2962'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add staging and validation columns to scan_opportunities table.

    Note: SQLite doesn't support non-constant defaults in ALTER TABLE,
    so we add columns as nullable.
    """

    # ============================================================
    # Staging Fields
    # ============================================================
    op.add_column('scan_opportunities', sa.Column('staged_at', sa.DateTime(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('staged_contracts', sa.Integer(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('staged_limit_price', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('staged_margin', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('staged_margin_source', sa.String(length=20), nullable=True))
    op.add_column('scan_opportunities', sa.Column('portfolio_rank', sa.Integer(), nullable=True))

    # ============================================================
    # Pre-market Validation Fields (Stage 1 - 9:15 AM)
    # ============================================================
    op.add_column('scan_opportunities', sa.Column('premarket_stock_price', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('premarket_deviation_pct', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('premarket_checked_at', sa.DateTime(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('premarket_new_bid', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('premarket_new_ask', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('adjusted_strike', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('adjusted_limit_price', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('adjustment_reason', sa.Text(), nullable=True))

    # ============================================================
    # Market-open Validation Fields (Stage 2 - 9:30 AM)
    # ============================================================
    op.add_column('scan_opportunities', sa.Column('open_stock_price', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('open_deviation_pct', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('open_checked_at', sa.DateTime(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('open_bid', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('open_ask', sa.Float(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('open_limit_price', sa.Float(), nullable=True))

    # ============================================================
    # Execution Scheduling
    # ============================================================
    op.add_column('scan_opportunities', sa.Column('execution_session', sa.String(length=50), nullable=True))
    op.add_column('scan_opportunities', sa.Column('execution_priority', sa.Integer(), nullable=True))

    # ============================================================
    # Create Indexes for Common Queries
    # ============================================================
    op.create_index(
        'ix_scan_opportunities_staged_at',
        'scan_opportunities',
        ['staged_at'],
        unique=False
    )
    op.create_index(
        'ix_scan_opportunities_execution_session',
        'scan_opportunities',
        ['execution_session'],
        unique=False
    )
    op.create_index(
        'ix_scan_opportunities_portfolio_rank',
        'scan_opportunities',
        ['portfolio_rank'],
        unique=False
    )


def downgrade() -> None:
    """Remove staging and validation columns from scan_opportunities table."""

    # Drop indexes
    op.drop_index('ix_scan_opportunities_portfolio_rank', table_name='scan_opportunities')
    op.drop_index('ix_scan_opportunities_execution_session', table_name='scan_opportunities')
    op.drop_index('ix_scan_opportunities_staged_at', table_name='scan_opportunities')

    # Drop execution scheduling columns
    op.drop_column('scan_opportunities', 'execution_priority')
    op.drop_column('scan_opportunities', 'execution_session')

    # Drop market-open validation columns
    op.drop_column('scan_opportunities', 'open_limit_price')
    op.drop_column('scan_opportunities', 'open_ask')
    op.drop_column('scan_opportunities', 'open_bid')
    op.drop_column('scan_opportunities', 'open_checked_at')
    op.drop_column('scan_opportunities', 'open_deviation_pct')
    op.drop_column('scan_opportunities', 'open_stock_price')

    # Drop pre-market validation columns
    op.drop_column('scan_opportunities', 'adjustment_reason')
    op.drop_column('scan_opportunities', 'adjusted_limit_price')
    op.drop_column('scan_opportunities', 'adjusted_strike')
    op.drop_column('scan_opportunities', 'premarket_new_ask')
    op.drop_column('scan_opportunities', 'premarket_new_bid')
    op.drop_column('scan_opportunities', 'premarket_checked_at')
    op.drop_column('scan_opportunities', 'premarket_deviation_pct')
    op.drop_column('scan_opportunities', 'premarket_stock_price')

    # Drop staging columns
    op.drop_column('scan_opportunities', 'portfolio_rank')
    op.drop_column('scan_opportunities', 'staged_margin_source')
    op.drop_column('scan_opportunities', 'staged_margin')
    op.drop_column('scan_opportunities', 'staged_limit_price')
    op.drop_column('scan_opportunities', 'staged_contracts')
    op.drop_column('scan_opportunities', 'staged_at')
