"""add_decision_context_tracking

Revision ID: a1598d212799
Revises: 29ab28ce5e82
Create Date: 2026-01-29 16:17:44.475936

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1598d212799'
down_revision: Union[str, None] = '29ab28ce5e82'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add decision_contexts table for storing trade decision context."""
    op.create_table(
        "decision_contexts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decision_id", sa.String(), nullable=False),
        sa.Column("opportunity_id", sa.Integer(), nullable=True),
        sa.Column("trade_id", sa.Integer(), nullable=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False),
        # Market context (JSON for flexibility)
        sa.Column("market_context", sa.JSON(), nullable=False),
        # Underlying context (JSON for flexibility)
        sa.Column("underlying_context", sa.JSON(), nullable=False),
        # Strategy parameters at decision time
        sa.Column("strategy_params", sa.JSON(), nullable=False),
        # Ranking info
        sa.Column("source", sa.String(), nullable=True),
        sa.Column("rank_position", sa.Integer(), nullable=True),
        sa.Column("rank_score", sa.Float(), nullable=True),
        sa.Column("rank_factors", sa.JSON(), nullable=True),
        # AI scoring (optional)
        sa.Column("ai_confidence_score", sa.Float(), nullable=True),
        sa.Column("ai_reasoning", sa.Text(), nullable=True),
        # Metadata
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("decision_id"),
        sa.ForeignKeyConstraint(
            ["opportunity_id"], ["scan_opportunities.id"], name="fk_decision_opportunity"
        ),
        sa.ForeignKeyConstraint(
            ["trade_id"], ["trades.id"], name="fk_decision_trade"
        ),
    )

    # Create indexes for common queries
    op.create_index(
        "ix_decision_contexts_opportunity_id",
        "decision_contexts",
        ["opportunity_id"],
    )
    op.create_index(
        "ix_decision_contexts_trade_id", "decision_contexts", ["trade_id"]
    )
    op.create_index(
        "ix_decision_contexts_timestamp", "decision_contexts", ["timestamp"]
    )
    op.create_index(
        "ix_decision_contexts_decision_id", "decision_contexts", ["decision_id"]
    )


def downgrade() -> None:
    """Remove decision_contexts table."""
    op.drop_index("ix_decision_contexts_decision_id", table_name="decision_contexts")
    op.drop_index("ix_decision_contexts_timestamp", table_name="decision_contexts")
    op.drop_index("ix_decision_contexts_trade_id", table_name="decision_contexts")
    op.drop_index(
        "ix_decision_contexts_opportunity_id", table_name="decision_contexts"
    )
    op.drop_table("decision_contexts")
