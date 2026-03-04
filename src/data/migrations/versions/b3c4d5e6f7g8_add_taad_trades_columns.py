"""Add TAAD extension columns to trades table

Revision ID: b3c4d5e6f7g8
Revises: a1b2c3d4e5f6
Create Date: 2026-02-11 16:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b3c4d5e6f7g8"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add TAAD columns to trades table
    op.add_column("trades", sa.Column("trade_source", sa.String(20), nullable=True))
    op.add_column("trades", sa.Column("account_id", sa.String(20), nullable=True))
    op.add_column("trades", sa.Column("assignment_status", sa.String(20), nullable=True))
    op.add_column("trades", sa.Column("ibkr_execution_id", sa.String(50), nullable=True))
    op.add_column("trades", sa.Column("enrichment_status", sa.String(20), nullable=True))
    op.add_column("trades", sa.Column("enrichment_quality", sa.Float(), nullable=True))

    op.create_index("ix_trades_trade_source", "trades", ["trade_source"])
    op.create_index("ix_trades_account_id", "trades", ["account_id"])


def downgrade() -> None:
    op.drop_index("ix_trades_account_id", "trades")
    op.drop_index("ix_trades_trade_source", "trades")
    op.drop_column("trades", "enrichment_quality")
    op.drop_column("trades", "enrichment_status")
    op.drop_column("trades", "ibkr_execution_id")
    op.drop_column("trades", "assignment_status")
    op.drop_column("trades", "account_id")
    op.drop_column("trades", "trade_source")
