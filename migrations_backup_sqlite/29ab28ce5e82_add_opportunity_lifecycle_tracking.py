"""add_opportunity_lifecycle_tracking

Revision ID: 29ab28ce5e82
Revises: 02b0dcf1301b
Create Date: 2026-01-28 07:44:44.806929

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '29ab28ce5e82'
down_revision: Union[str, None] = '02b0dcf1301b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add lifecycle tracking columns to scan_opportunities table.

    Note: SQLite doesn't support non-constant defaults in ALTER TABLE,
    so we add columns as nullable and update existing rows separately.
    """

    # Add lifecycle state tracking
    op.add_column('scan_opportunities', sa.Column('state', sa.String(length=20), nullable=True))
    op.add_column('scan_opportunities', sa.Column('state_history', sa.JSON(), nullable=True))

    # Add timestamps
    op.add_column('scan_opportunities', sa.Column('updated_at', sa.DateTime(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('expires_at', sa.DateTime(), nullable=True))

    # Add snapshot data at different stages
    op.add_column('scan_opportunities', sa.Column('enrichment_snapshot', sa.JSON(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('validation_snapshot', sa.JSON(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('execution_snapshot', sa.JSON(), nullable=True))

    # Add rejection tracking (critical for learning)
    op.add_column('scan_opportunities', sa.Column('rejection_reasons', sa.JSON(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('risk_check_results', sa.JSON(), nullable=True))

    # Add user decision tracking
    op.add_column('scan_opportunities', sa.Column('user_decision', sa.String(length=20), nullable=True))
    op.add_column('scan_opportunities', sa.Column('user_decision_at', sa.DateTime(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('user_notes', sa.Text(), nullable=True))

    # Add execution tracking
    op.add_column('scan_opportunities', sa.Column('execution_attempts', sa.Integer(), nullable=True))
    op.add_column('scan_opportunities', sa.Column('last_error', sa.Text(), nullable=True))

    # Add idempotency key (prevent duplicates)
    op.add_column('scan_opportunities', sa.Column('opportunity_hash', sa.String(length=64), nullable=True))

    # Create indexes for common queries
    op.create_index(op.f('ix_scan_opportunities_state'), 'scan_opportunities', ['state'], unique=False)
    op.create_index(op.f('ix_scan_opportunities_opportunity_hash'), 'scan_opportunities', ['opportunity_hash'], unique=False)

    # Update existing rows with default values
    # SQLite requires explicit updates after adding nullable columns
    op.execute("UPDATE scan_opportunities SET state = 'PENDING' WHERE state IS NULL")
    op.execute("UPDATE scan_opportunities SET state_history = '[]' WHERE state_history IS NULL")
    op.execute("UPDATE scan_opportunities SET rejection_reasons = '[]' WHERE rejection_reasons IS NULL")
    op.execute("UPDATE scan_opportunities SET execution_attempts = 0 WHERE execution_attempts IS NULL")
    op.execute("UPDATE scan_opportunities SET updated_at = created_at WHERE updated_at IS NULL")


def downgrade() -> None:
    """Remove lifecycle tracking columns from scan_opportunities table."""

    # Drop indexes
    op.drop_index(op.f('ix_scan_opportunities_opportunity_hash'), table_name='scan_opportunities')
    op.drop_index(op.f('ix_scan_opportunities_state'), table_name='scan_opportunities')

    # Drop columns in reverse order
    op.drop_column('scan_opportunities', 'opportunity_hash')
    op.drop_column('scan_opportunities', 'last_error')
    op.drop_column('scan_opportunities', 'execution_attempts')
    op.drop_column('scan_opportunities', 'user_notes')
    op.drop_column('scan_opportunities', 'user_decision_at')
    op.drop_column('scan_opportunities', 'user_decision')
    op.drop_column('scan_opportunities', 'risk_check_results')
    op.drop_column('scan_opportunities', 'rejection_reasons')
    op.drop_column('scan_opportunities', 'execution_snapshot')
    op.drop_column('scan_opportunities', 'validation_snapshot')
    op.drop_column('scan_opportunities', 'enrichment_snapshot')
    op.drop_column('scan_opportunities', 'expires_at')
    op.drop_column('scan_opportunities', 'updated_at')
    op.drop_column('scan_opportunities', 'state_history')
    op.drop_column('scan_opportunities', 'state')
