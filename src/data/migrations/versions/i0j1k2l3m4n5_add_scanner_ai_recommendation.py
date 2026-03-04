"""Add ai_recommendation column to scan_opportunities

Phase 7: IBKR Scanner Dashboard - adds JSON column for storing
AI-generated recommendations (score, recommendation, reasoning,
risk_flags) per scan opportunity.

Revision ID: i0j1k2l3m4n5
Revises: h9i0j1k2l3m4
Create Date: 2026-02-21 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "i0j1k2l3m4n5"
down_revision: Union[str, None] = "h9i0j1k2l3m4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "scan_opportunities",
        sa.Column("ai_recommendation", sa.JSON(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("scan_opportunities", "ai_recommendation")
