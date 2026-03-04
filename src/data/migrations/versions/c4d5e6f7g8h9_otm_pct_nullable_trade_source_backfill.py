"""Make otm_pct nullable and backfill trade_source on existing trades

Revision ID: c4d5e6f7g8h9
Revises: b3c4d5e6f7g8
Create Date: 2026-02-12 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7g8h9"
down_revision: Union[str, None] = "b3c4d5e6f7g8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Make otm_pct nullable — historical imports don't have stock price at entry,
    # so OTM% can't be calculated at promotion time. NULL = "not yet computed".
    op.alter_column("trades", "otm_pct", existing_type=sa.Float(), nullable=True)

    # Backfill trade_source on existing trades (real executions from IBKR).
    # Distinguishes them from ibkr_import records that will be promoted later.
    op.execute("UPDATE trades SET trade_source = 'live' WHERE trade_source IS NULL")


def downgrade() -> None:
    # Revert trade_source backfill
    op.execute("UPDATE trades SET trade_source = NULL WHERE trade_source = 'live'")

    # Restore otm_pct NOT NULL — fill NULLs with 0.0 first
    op.execute("UPDATE trades SET otm_pct = 0.0 WHERE otm_pct IS NULL")
    op.alter_column("trades", "otm_pct", existing_type=sa.Float(), nullable=False)
