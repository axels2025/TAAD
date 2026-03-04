"""Add Phase 5 daemon tables

Creates 6 tables for the continuous agentic trading daemon:
- daemon_events: Durable event queue
- decision_audit: Full decision audit trail
- working_memory: Crash-safe context store
- decision_embeddings: pgvector semantic search
- daemon_health: Heartbeat and status tracking
- claude_api_costs: API cost tracking

Also enables pgvector extension on PostgreSQL for semantic search.

Revision ID: f7g8h9i0j1k2
Revises: e6f7g8h9i0j1
Create Date: 2026-02-19 10:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f7g8h9i0j1k2"
down_revision: Union[str, None] = "e6f7g8h9i0j1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    """Check if we're running on PostgreSQL."""
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    # --- daemon_events ---
    op.create_table(
        "daemon_events",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="5"),
        sa.Column("status", sa.String(20), nullable=False, server_default="'pending'", index=True),
        sa.Column("payload", sa.JSON(), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("processed_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )
    op.create_index(
        "ix_daemon_events_status_priority",
        "daemon_events",
        ["status", "priority", "created_at"],
    )

    # --- decision_audit ---
    op.create_table(
        "decision_audit",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("event_id", sa.Integer(), sa.ForeignKey("daemon_events.id"), nullable=True, index=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("autonomy_level", sa.Integer(), nullable=False),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        sa.Column("action", sa.String(50), nullable=False, index=True),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("reasoning", sa.Text(), nullable=True),
        sa.Column("key_factors", sa.JSON(), nullable=True),
        sa.Column("risks_considered", sa.JSON(), nullable=True),
        sa.Column("autonomy_approved", sa.Boolean(), nullable=False),
        sa.Column("escalation_reason", sa.Text(), nullable=True),
        sa.Column("human_override", sa.Boolean(), server_default="false"),
        sa.Column("human_decision", sa.String(50), nullable=True),
        sa.Column("human_decided_at", sa.DateTime(), nullable=True),
        sa.Column("executed", sa.Boolean(), server_default="false"),
        sa.Column("execution_result", sa.JSON(), nullable=True),
        sa.Column("execution_error", sa.Text(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("model_used", sa.String(100), nullable=True),
        sa.Column("cost_usd", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # --- working_memory ---
    op.create_table(
        "working_memory",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("strategy_state", sa.JSON(), nullable=True),
        sa.Column("market_context", sa.JSON(), nullable=True),
        sa.Column("recent_decisions", sa.JSON(), nullable=True),
        sa.Column("anomalies", sa.JSON(), nullable=True),
        sa.Column("autonomy_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("reflection_reports", sa.JSON(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # --- decision_embeddings ---
    op.create_table(
        "decision_embeddings",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("decision_audit_id", sa.Integer(), sa.ForeignKey("decision_audit.id"), nullable=False, index=True),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # pgvector: add VECTOR(1536) column on PostgreSQL only
    if _is_postgresql():
        op.execute("CREATE EXTENSION IF NOT EXISTS vector")
        op.execute(
            "ALTER TABLE decision_embeddings ADD COLUMN embedding vector(1536)"
        )
        op.execute(
            "CREATE INDEX ix_decision_embeddings_embedding "
            "ON decision_embeddings USING ivfflat (embedding vector_cosine_ops) "
            "WITH (lists = 100)"
        )

    # --- daemon_health ---
    op.create_table(
        "daemon_health",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("pid", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(20), nullable=False, server_default="'stopped'"),
        sa.Column("last_heartbeat", sa.DateTime(), nullable=True),
        sa.Column("last_event_processed", sa.String(50), nullable=True),
        sa.Column("events_processed_today", sa.Integer(), server_default="0"),
        sa.Column("decisions_made_today", sa.Integer(), server_default="0"),
        sa.Column("errors_today", sa.Integer(), server_default="0"),
        sa.Column("uptime_seconds", sa.Integer(), server_default="0"),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("autonomy_level", sa.Integer(), server_default="1"),
        sa.Column("message", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now()),
    )

    # --- claude_api_costs ---
    op.create_table(
        "claude_api_costs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("timestamp", sa.DateTime(), nullable=False, server_default=sa.func.now(), index=True),
        sa.Column("model", sa.String(100), nullable=False),
        sa.Column("purpose", sa.String(50), nullable=False, index=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost_usd", sa.Float(), nullable=False),
        sa.Column("daily_total_usd", sa.Float(), nullable=True),
        sa.Column("decision_audit_id", sa.Integer(), sa.ForeignKey("decision_audit.id"), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("claude_api_costs")
    op.drop_table("daemon_health")

    if _is_postgresql():
        op.execute("DROP INDEX IF EXISTS ix_decision_embeddings_embedding")

    op.drop_table("decision_embeddings")
    op.drop_table("working_memory")
    op.drop_table("decision_audit")
    op.drop_table("daemon_events")
