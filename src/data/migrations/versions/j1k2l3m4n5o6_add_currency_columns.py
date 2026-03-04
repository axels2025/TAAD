"""Add currency columns to ibkr_raw_imports and trades

Track trade denomination currency (e.g., USD, AUD) to prevent
mixed-currency P&L corruption. Backfills existing records from
the raw_data JSON blob where available, defaults to USD.

Revision ID: j1k2l3m4n5o6
Revises: i0j1k2l3m4n5
Create Date: 2026-02-25 10:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "j1k2l3m4n5o6"
down_revision: Union[str, None] = "i0j1k2l3m4n5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    dialect = op.get_bind().dialect.name

    # --- ibkr_raw_imports (import schema) ---
    if dialect == "postgresql":
        op.add_column(
            "ibkr_raw_imports",
            sa.Column("currency", sa.String(10), nullable=True, server_default="USD"),
            schema="import",
        )
        # Backfill from raw_data JSONB
        op.execute(
            "UPDATE import.ibkr_raw_imports "
            "SET currency = raw_data->>'currency' "
            "WHERE raw_data->>'currency' IS NOT NULL AND currency IS NULL"
        )
    else:
        # SQLite — no schema prefix
        op.add_column(
            "ibkr_raw_imports",
            sa.Column("currency", sa.String(10), nullable=True, server_default="USD"),
        )
        # Backfill from raw_data JSON
        op.execute(
            "UPDATE ibkr_raw_imports "
            "SET currency = json_extract(raw_data, '$.currency') "
            "WHERE json_extract(raw_data, '$.currency') IS NOT NULL AND currency IS NULL"
        )

    # Backfill remaining imports (no JSON data) to USD
    if dialect == "postgresql":
        op.execute(
            "UPDATE import.ibkr_raw_imports SET currency = 'USD' WHERE currency IS NULL"
        )
    else:
        op.execute(
            "UPDATE ibkr_raw_imports SET currency = 'USD' WHERE currency IS NULL"
        )

    # --- trades (public schema) ---
    if dialect == "postgresql":
        op.add_column(
            "trades",
            sa.Column("currency", sa.String(10), nullable=True, server_default="USD"),
        )
        # Backfill trades from their source imports via ibkr_execution_id
        op.execute(
            "UPDATE trades "
            "SET currency = imp.currency "
            "FROM import.ibkr_raw_imports imp "
            "WHERE trades.ibkr_execution_id = imp.ibkr_exec_id "
            "AND trades.ibkr_execution_id IS NOT NULL "
            "AND trades.currency IS NULL"
        )
    else:
        op.add_column(
            "trades",
            sa.Column("currency", sa.String(10), nullable=True, server_default="USD"),
        )
        # SQLite backfill from imports
        op.execute(
            "UPDATE trades "
            "SET currency = ("
            "  SELECT imp.currency FROM ibkr_raw_imports imp "
            "  WHERE trades.ibkr_execution_id = imp.ibkr_exec_id "
            "  LIMIT 1"
            ") "
            "WHERE trades.ibkr_execution_id IS NOT NULL "
            "AND trades.currency IS NULL"
        )

    # Backfill remaining trades (non-import, e.g. nakedtrader) to USD
    if dialect == "postgresql":
        op.execute("UPDATE trades SET currency = 'USD' WHERE currency IS NULL")
    else:
        op.execute("UPDATE trades SET currency = 'USD' WHERE currency IS NULL")


def downgrade() -> None:
    dialect = op.get_bind().dialect.name

    if dialect == "postgresql":
        op.drop_column("trades", "currency")
        op.drop_column("ibkr_raw_imports", "currency", schema="import")
    else:
        op.drop_column("trades", "currency")
        op.drop_column("ibkr_raw_imports", "currency")
