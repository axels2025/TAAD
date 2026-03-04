"""Add guardrail_flags to decision_audit and guardrail_metrics table

Phase 6: Hallucination Guardrails schema changes.

- Adds guardrail_flags JSON column to decision_audit for storing per-decision
  guard results (blocks, warnings, findings).
- Creates guardrail_metrics table for daily confidence calibration,
  reasoning entropy tracking, and guardrail activity counters.

Revision ID: h9i0j1k2l3m4
Revises: g8h9i0j1k2l3
Create Date: 2026-02-20 14:00:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "h9i0j1k2l3m4"
down_revision: Union[str, None] = "g8h9i0j1k2l3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Add guardrail_flags column to decision_audit ---
    op.add_column(
        "decision_audit",
        sa.Column("guardrail_flags", sa.JSON(), nullable=True),
    )

    # --- Create guardrail_metrics table ---
    op.create_table(
        "guardrail_metrics",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("metric_date", sa.Date(), nullable=False),
        sa.Column("metric_type", sa.String(50), nullable=False),
        # Confidence calibration fields
        sa.Column("confidence_bucket", sa.String(20), nullable=True),
        sa.Column("predicted_accuracy", sa.Float(), nullable=True),
        sa.Column("actual_accuracy", sa.Float(), nullable=True),
        sa.Column("sample_size", sa.Integer(), nullable=True),
        sa.Column("calibration_error", sa.Float(), nullable=True),
        # Reasoning entropy fields
        sa.Column("avg_reasoning_length", sa.Float(), nullable=True),
        sa.Column("unique_key_factors_ratio", sa.Float(), nullable=True),
        sa.Column("reasoning_similarity_score", sa.Float(), nullable=True),
        # Daily activity counters
        sa.Column("total_decisions", sa.Integer(), nullable=True),
        sa.Column("guardrail_blocks", sa.Integer(), nullable=True),
        sa.Column("guardrail_warnings", sa.Integer(), nullable=True),
        sa.Column("symbols_flagged", sa.Integer(), nullable=True),
        sa.Column("numbers_flagged", sa.Integer(), nullable=True),
        # Metadata
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now()),
    )

    op.create_index(
        "ix_guardrail_metrics_date_type",
        "guardrail_metrics",
        ["metric_date", "metric_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_guardrail_metrics_date_type", table_name="guardrail_metrics")
    op.drop_table("guardrail_metrics")
    op.drop_column("decision_audit", "guardrail_flags")
