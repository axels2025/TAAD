"""Add strike selection fields to trade_entry_snapshots

Revision ID: d5e6f7g8h9i0
Revises: c4d5e6f7g8h9
Create Date: 2026-02-16 12:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d5e6f7g8h9i0"
down_revision: Union[str, None] = "c4d5e6f7g8h9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "trade_entry_snapshots",
        sa.Column("strike_selection_method", sa.String(20), nullable=True),
    )
    op.add_column(
        "trade_entry_snapshots",
        sa.Column("original_strike", sa.Float(), nullable=True),
    )
    op.add_column(
        "trade_entry_snapshots",
        sa.Column("live_delta_at_selection", sa.Float(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("trade_entry_snapshots", "live_delta_at_selection")
    op.drop_column("trade_entry_snapshots", "original_strike")
    op.drop_column("trade_entry_snapshots", "strike_selection_method")
