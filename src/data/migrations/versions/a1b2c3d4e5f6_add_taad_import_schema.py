"""Add TAAD import schema tables

Revision ID: a1b2c3d4e5f6
Revises: b2f71fc074b9
Create Date: 2026-02-11 14:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, None] = "b2f71fc074b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create schemas (idempotent)
    op.execute('CREATE SCHEMA IF NOT EXISTS "import"')
    op.execute('CREATE SCHEMA IF NOT EXISTS "enrichment"')
    op.execute('CREATE SCHEMA IF NOT EXISTS "analysis"')

    # import_sessions table
    op.create_table(
        "import_sessions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("source_file", sa.String(500), nullable=True),
        sa.Column("account_id", sa.String(20), nullable=True),
        sa.Column("date_range_start", sa.Date(), nullable=True),
        sa.Column("date_range_end", sa.Date(), nullable=True),
        sa.Column("total_records", sa.Integer(), default=0),
        sa.Column("imported_records", sa.Integer(), default=0),
        sa.Column("skipped_duplicates", sa.Integer(), default=0),
        sa.Column("error_count", sa.Integer(), default=0),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("error_details", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="import",
    )
    op.create_index(
        "ix_import_sessions_source_type",
        "import_sessions",
        ["source_type"],
        schema="import",
    )
    op.create_index(
        "ix_import_sessions_account_id",
        "import_sessions",
        ["account_id"],
        schema="import",
    )

    # ibkr_raw_imports table
    op.create_table(
        "ibkr_raw_imports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("import_session_id", sa.Integer(), nullable=False),
        sa.Column("source_type", sa.String(30), nullable=False),
        sa.Column("account_id", sa.String(20), nullable=False),
        sa.Column("account_alias", sa.String(50), nullable=True),
        sa.Column("raw_data", sa.JSON(), nullable=False),
        sa.Column("trade_date", sa.Date(), nullable=False),
        sa.Column("settle_date", sa.Date(), nullable=True),
        sa.Column("symbol", sa.String(50), nullable=True),
        sa.Column("underlying_symbol", sa.String(20), nullable=False),
        sa.Column("strike", sa.Float(), nullable=True),
        sa.Column("expiry", sa.Date(), nullable=True),
        sa.Column("put_call", sa.String(5), nullable=True),
        sa.Column("asset_category", sa.String(10), nullable=False),
        sa.Column("buy_sell", sa.String(10), nullable=False),
        sa.Column("open_close", sa.String(10), nullable=True),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("amount", sa.Float(), nullable=True),
        sa.Column("proceeds", sa.Float(), nullable=True),
        sa.Column("net_cash", sa.Float(), nullable=True),
        sa.Column("commission", sa.Float(), nullable=True),
        sa.Column("multiplier", sa.Integer(), nullable=True),
        sa.Column("ibkr_exec_id", sa.String(100), nullable=True),
        sa.Column("ibkr_trade_id", sa.String(50), nullable=True),
        sa.Column("ibkr_order_id", sa.String(50), nullable=True),
        sa.Column("ibkr_conid", sa.String(20), nullable=True),
        sa.Column("order_type", sa.String(20), nullable=True),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column("order_time", sa.DateTime(), nullable=True),
        sa.Column("execution_time", sa.DateTime(), nullable=True),
        sa.Column("level_of_detail", sa.String(30), nullable=True),
        sa.Column("matched", sa.Boolean(), default=False),
        sa.Column("matched_trade_id", sa.String(50), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("ibkr_exec_id"),
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_import_session_id",
        "ibkr_raw_imports",
        ["import_session_id"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_account_id",
        "ibkr_raw_imports",
        ["account_id"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_trade_date",
        "ibkr_raw_imports",
        ["trade_date"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_underlying_symbol",
        "ibkr_raw_imports",
        ["underlying_symbol"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_expiry",
        "ibkr_raw_imports",
        ["expiry"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_ibkr_exec_id",
        "ibkr_raw_imports",
        ["ibkr_exec_id"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_ibkr_trade_id",
        "ibkr_raw_imports",
        ["ibkr_trade_id"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_ibkr_order_id",
        "ibkr_raw_imports",
        ["ibkr_order_id"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_matched",
        "ibkr_raw_imports",
        ["matched"],
        schema="import",
    )
    op.create_index(
        "ix_ibkr_raw_imports_matched_trade_id",
        "ibkr_raw_imports",
        ["matched_trade_id"],
        schema="import",
    )

    # trade_matching_log table
    op.create_table(
        "trade_matching_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("raw_import_id_open", sa.Integer(), nullable=False),
        sa.Column("raw_import_id_close", sa.Integer(), nullable=True),
        sa.Column("matched_trade_id", sa.String(50), nullable=True),
        sa.Column("match_type", sa.String(30), nullable=False),
        sa.Column("confidence_score", sa.Float(), nullable=True),
        sa.Column("match_notes", sa.Text(), nullable=True),
        sa.Column(
            "matched_at", sa.DateTime(), server_default=sa.text("now()"),
        ),
        sa.PrimaryKeyConstraint("id"),
        schema="import",
    )
    op.create_index(
        "ix_trade_matching_log_raw_import_id_open",
        "trade_matching_log",
        ["raw_import_id_open"],
        schema="import",
    )
    op.create_index(
        "ix_trade_matching_log_raw_import_id_close",
        "trade_matching_log",
        ["raw_import_id_close"],
        schema="import",
    )
    op.create_index(
        "ix_trade_matching_log_matched_trade_id",
        "trade_matching_log",
        ["matched_trade_id"],
        schema="import",
    )


def downgrade() -> None:
    op.drop_table("trade_matching_log", schema="import")
    op.drop_table("ibkr_raw_imports", schema="import")
    op.drop_table("import_sessions", schema="import")

    # Don't drop schemas in downgrade - they may be shared
