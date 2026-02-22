"""Tests for Phase 6.3: Prompt Engineering grounding.

Tests that system prompt contains grounding requirements,
prompt string includes symbol list and data timestamp,
and user message has grounding footer.
"""

import json
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from src.agentic.reasoning_engine import (
    REASONING_SYSTEM_PROMPT,
    ClaudeReasoningEngine,
)
from src.agentic.working_memory import ReasoningContext


# ---------- System Prompt Tests ----------


def test_system_prompt_contains_grounding_requirements():
    """System prompt must contain grounding rules for Claude."""
    assert "Grounding Requirements" in REASONING_SYSTEM_PROMPT
    assert "ONLY reference symbols" in REASONING_SYSTEM_PROMPT
    assert "ONLY cite numbers" in REASONING_SYSTEM_PROMPT
    assert "never fabricate values" in REASONING_SYSTEM_PROMPT


def test_system_prompt_contains_uncertainty_calibration():
    """System prompt must contain uncertainty calibration guidance."""
    assert "Uncertainty Calibration" in REASONING_SYSTEM_PROMPT
    assert "NEVER set confidence > 0.9" in REASONING_SYSTEM_PROMPT
    assert "MONITOR_ONLY when uncertain" in REASONING_SYSTEM_PROMPT


def test_system_prompt_contains_reasoning_structure():
    """System prompt must require OBSERVATION/ASSESSMENT/ACTION structure."""
    assert "Reasoning Structure" in REASONING_SYSTEM_PROMPT
    assert "OBSERVATION" in REASONING_SYSTEM_PROMPT
    assert "ASSESSMENT" in REASONING_SYSTEM_PROMPT
    assert "ACTION" in REASONING_SYSTEM_PROMPT


# ---------- Prompt String Tests ----------


def test_prompt_string_has_symbol_list():
    """to_prompt_string() must include a Symbols in Scope header."""
    ctx = ReasoningContext(
        open_positions=[{"symbol": "AAPL", "strike": 200}],
        staged_candidates=[{"symbol": "MSFT", "strike": 400}],
        recent_trades=[{"symbol": "TSLA", "profit_loss": 50.0}],
    )

    prompt = ctx.to_prompt_string()

    assert "## Symbols in Scope:" in prompt
    assert "AAPL" in prompt
    assert "MSFT" in prompt
    assert "TSLA" in prompt


def test_prompt_string_has_data_timestamp():
    """to_prompt_string() must include a Data as of timestamp."""
    ctx = ReasoningContext()
    prompt = ctx.to_prompt_string()

    assert "## Data as of:" in prompt
    assert "UTC" in prompt


def test_prompt_string_has_data_limitations():
    """to_prompt_string() includes Data Limitations section when present."""
    ctx = ReasoningContext(
        data_limitations=["vix is unavailable", "spy_price is unavailable"],
    )
    prompt = ctx.to_prompt_string()

    assert "## Data Limitations" in prompt
    assert "vix is unavailable" in prompt
    assert "spy_price is unavailable" in prompt


def test_prompt_string_omits_data_limitations_when_empty():
    """to_prompt_string() omits Data Limitations section when no limitations."""
    ctx = ReasoningContext(data_limitations=[])
    prompt = ctx.to_prompt_string()

    assert "## Data Limitations" not in prompt


def test_prompt_string_omits_symbol_list_when_no_symbols():
    """to_prompt_string() omits Symbols in Scope when no symbols in context."""
    ctx = ReasoningContext()
    prompt = ctx.to_prompt_string()

    assert "## Symbols in Scope:" not in prompt


# ---------- User Message Tests ----------


def test_user_message_has_grounding_footer():
    """_build_user_message() appends grounding instruction footer."""
    from src.data.database import close_database, get_session, init_database

    engine_db = init_database(database_url="sqlite:///:memory:")
    session = get_session()

    try:
        with patch("src.agentic.reasoning_engine.BaseAgent") as MockAgent:
            MockAgent.side_effect = [MagicMock(), MagicMock()]
            engine = ClaudeReasoningEngine(db_session=session)

        ctx = ReasoningContext(
            autonomy_level=1,
            market_context={"vix": 18.0},
        )

        message = engine._build_user_message(ctx, "SCHEDULED_CHECK")

        assert "Only reference data provided above" in message
        assert "do not assume or estimate" in message
    finally:
        session.close()
        close_database()
