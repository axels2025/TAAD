"""Add multiplier column to trades

Track per-trade option contract multiplier. US equity options use 100
(the default). ASX XJO index options use 10. Backfills existing trades
to 100 (all prior trades are US-based).

Revision ID: k2l3m4n5o6p7
Revises: j1k2l3m4n5o6
Create Date: 2026-02-25 15:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "k2l3m4n5o6p7"
down_revision: Union[str, None] = "j1k2l3m4n5o6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    if dialect == "postgresql":
        op.add_column(
            "trades",
            sa.Column(
                "multiplier", sa.Integer(), nullable=True, server_default="100"
            ),
        )
    else:
        op.add_column(
            "trades",
            sa.Column(
                "multiplier", sa.Integer(), nullable=True, server_default="100"
            ),
        )

    # Backfill existing trades (all are US-based with multiplier=100)
    op.execute("UPDATE trades SET multiplier = 100 WHERE multiplier IS NULL")


def downgrade() -> None:
    op.drop_column("trades", "multiplier")
