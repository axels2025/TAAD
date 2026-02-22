"""Add scan_results and scan_opportunities tables

Revision ID: 02b0dcf1301b
Revises: 97913c3b0e4d
Create Date: 2026-01-26 20:33:17.353309

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '02b0dcf1301b'
down_revision: Union[str, None] = '97913c3b0e4d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create scan_results table
    op.create_table(
        'scan_results',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('scan_timestamp', sa.DateTime(), nullable=False),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('config_used', sa.JSON(), nullable=True),
        sa.Column('total_candidates', sa.Integer(), nullable=True),
        sa.Column('validated_count', sa.Integer(), nullable=True),
        sa.Column('execution_time_seconds', sa.Float(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scan_results_scan_timestamp'), 'scan_results', ['scan_timestamp'], unique=False)
    op.create_index(op.f('ix_scan_results_source'), 'scan_results', ['source'], unique=False)

    # Create scan_opportunities table
    op.create_table(
        'scan_opportunities',
        sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
        sa.Column('scan_id', sa.Integer(), nullable=False),
        sa.Column('symbol', sa.String(length=10), nullable=False),
        sa.Column('strike', sa.Float(), nullable=False),
        sa.Column('expiration', sa.Date(), nullable=False),
        sa.Column('option_type', sa.String(length=10), nullable=True),
        sa.Column('premium', sa.Float(), nullable=True),
        sa.Column('bid', sa.Float(), nullable=True),
        sa.Column('ask', sa.Float(), nullable=True),
        sa.Column('spread_pct', sa.Float(), nullable=True),
        sa.Column('delta', sa.Float(), nullable=True),
        sa.Column('gamma', sa.Float(), nullable=True),
        sa.Column('theta', sa.Float(), nullable=True),
        sa.Column('vega', sa.Float(), nullable=True),
        sa.Column('iv', sa.Float(), nullable=True),
        sa.Column('otm_pct', sa.Float(), nullable=True),
        sa.Column('dte', sa.Integer(), nullable=True),
        sa.Column('stock_price', sa.Float(), nullable=True),
        sa.Column('margin_required', sa.Float(), nullable=True),
        sa.Column('margin_efficiency', sa.Float(), nullable=True),
        sa.Column('volume', sa.Integer(), nullable=True),
        sa.Column('open_interest', sa.Integer(), nullable=True),
        sa.Column('trend', sa.String(length=20), nullable=True),
        sa.Column('validation_status', sa.String(length=20), nullable=True),
        sa.Column('rejection_reason', sa.Text(), nullable=True),
        sa.Column('source', sa.String(length=20), nullable=False),
        sa.Column('entry_notes', sa.Text(), nullable=True),
        sa.Column('executed', sa.Boolean(), nullable=True),
        sa.Column('trade_id', sa.String(length=50), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.text('CURRENT_TIMESTAMP'), nullable=True),
        sa.ForeignKeyConstraint(['scan_id'], ['scan_results.id'], ),
        sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_scan_opportunities_executed'), 'scan_opportunities', ['executed'], unique=False)
    op.create_index(op.f('ix_scan_opportunities_expiration'), 'scan_opportunities', ['expiration'], unique=False)
    op.create_index(op.f('ix_scan_opportunities_scan_id'), 'scan_opportunities', ['scan_id'], unique=False)
    op.create_index(op.f('ix_scan_opportunities_source'), 'scan_opportunities', ['source'], unique=False)
    op.create_index(op.f('ix_scan_opportunities_symbol'), 'scan_opportunities', ['symbol'], unique=False)
    op.create_index(op.f('ix_scan_opportunities_trade_id'), 'scan_opportunities', ['trade_id'], unique=False)
    op.create_index(op.f('ix_scan_opportunities_validation_status'), 'scan_opportunities', ['validation_status'], unique=False)


def downgrade() -> None:
    # Drop scan_opportunities table and its indexes
    op.drop_index(op.f('ix_scan_opportunities_validation_status'), table_name='scan_opportunities')
    op.drop_index(op.f('ix_scan_opportunities_trade_id'), table_name='scan_opportunities')
    op.drop_index(op.f('ix_scan_opportunities_symbol'), table_name='scan_opportunities')
    op.drop_index(op.f('ix_scan_opportunities_source'), table_name='scan_opportunities')
    op.drop_index(op.f('ix_scan_opportunities_scan_id'), table_name='scan_opportunities')
    op.drop_index(op.f('ix_scan_opportunities_expiration'), table_name='scan_opportunities')
    op.drop_index(op.f('ix_scan_opportunities_executed'), table_name='scan_opportunities')
    op.drop_table('scan_opportunities')

    # Drop scan_results table and its indexes
    op.drop_index(op.f('ix_scan_results_source'), table_name='scan_results')
    op.drop_index(op.f('ix_scan_results_scan_timestamp'), table_name='scan_results')
    op.drop_table('scan_results')
