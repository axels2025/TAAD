"""Unit tests for the Claude-powered reasoning engine.

Tests CostTracker daily totals, cap enforcement, and record persistence.
Tests ClaudeReasoningEngine.reason() with mocked BaseAgent, including
cost-cap fallback, Claude failure fallback, and response parsing.
Tests _parse_response() for valid JSON, markdown code blocks, invalid JSON,
and invalid actions. Tests reflect() calls to the Sonnet model.
"""

import json
from datetime import datetime, date, timedelta
from unittest.mock import MagicMock, patch

import pytest

from src.agentic.reasoning_engine import (
    VALID_ACTIONS,
    ClaudeReasoningEngine,
    CostTracker,
    DecisionOutput,
)
from src.agentic.working_memory import ReasoningContext
from src.data.database import close_database, get_session, init_database
from src.data.models import Base, ClaudeApiCost, DecisionAudit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_database():
    """Provide an in-memory SQLite database with all tables created."""
    engine = init_database(database_url="sqlite:///:memory:")
    yield engine
    close_database()


@pytest.fixture
def db_session(temp_database):
    """Provide a fresh SQLAlchemy session."""
    session = get_session()
    yield session
    session.close()


@pytest.fixture
def cost_tracker(db_session):
    """CostTracker with $10 daily cap."""
    return CostTracker(db_session, daily_cap_usd=10.0)


@pytest.fixture
def sample_context():
    """Minimal ReasoningContext for engine tests."""
    return ReasoningContext(
        autonomy_level=1,
        strategy_state={"mode": "paper"},
        market_context={"vix": 18.5},
    )


@pytest.fixture
def valid_claude_response():
    """A well-formed Claude JSON response dict."""
    return {
        "content": json.dumps(
            {
                "action": "MONITOR_ONLY",
                "confidence": 0.85,
                "reasoning": "Market is stable, no action needed",
                "key_factors": ["low_vix", "no_positions"],
                "risks_considered": ["overnight_gap"],
                "metadata": {},
            }
        ),
        "input_tokens": 1000,
        "output_tokens": 200,
        "model": "claude-opus-4-6",
    }


def _make_engine(db_session, reasoning_agent=None, reflection_agent=None):
    """Build a ClaudeReasoningEngine with mocked BaseAgent instances.

    This patches the BaseAgent constructor so that no real Anthropic
    client is created and we can inject predetermined responses.
    """
    with patch("src.agentic.reasoning_engine.BaseAgent") as MockBaseAgent:
        # Each call to BaseAgent(...) returns our mock in order
        instances = []
        if reasoning_agent is None:
            reasoning_agent = MagicMock()
        if reflection_agent is None:
            reflection_agent = MagicMock()
        instances = [reasoning_agent, reflection_agent]
        MockBaseAgent.side_effect = instances

        engine = ClaudeReasoningEngine(db_session=db_session)

    return engine


# ===========================================================================
# CostTracker Tests
# ===========================================================================


class TestCostTrackerGetDailyTotal:
    """CostTracker.get_daily_total() returns correct sum for today."""

    def test_returns_zero_when_no_records(self, cost_tracker):
        """Daily total is 0.0 when there are no cost records."""
        assert cost_tracker.get_daily_total() == 0.0

    def test_sums_todays_records(self, cost_tracker, db_session):
        """Sums only records from today."""
        # Insert two records for today
        now = datetime.utcnow()
        for cost in (1.50, 2.25):
            record = ClaudeApiCost(
                timestamp=now,
                model="claude-opus-4-6",
                purpose="reasoning",
                input_tokens=500,
                output_tokens=100,
                cost_usd=cost,
                daily_total_usd=0.0,
            )
            db_session.add(record)
        db_session.commit()

        assert cost_tracker.get_daily_total() == pytest.approx(3.75)

    def test_excludes_yesterdays_records(self, cost_tracker, db_session):
        """Records from yesterday are not included in today's total."""
        yesterday = datetime.utcnow() - timedelta(days=1)
        record = ClaudeApiCost(
            timestamp=yesterday,
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=500,
            output_tokens=100,
            cost_usd=5.00,
            daily_total_usd=5.00,
        )
        db_session.add(record)
        db_session.commit()

        assert cost_tracker.get_daily_total() == 0.0


class TestCostTrackerCanCall:
    """CostTracker.can_call() returns True under cap, False over."""

    def test_returns_true_when_under_cap(self, cost_tracker):
        """Can call when no costs recorded yet."""
        assert cost_tracker.can_call() is True

    def test_returns_true_when_below_cap(self, cost_tracker, db_session):
        """Can call when today's total is below the daily cap."""
        record = ClaudeApiCost(
            timestamp=datetime.utcnow(),
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=500,
            output_tokens=100,
            cost_usd=9.99,
            daily_total_usd=9.99,
        )
        db_session.add(record)
        db_session.commit()

        assert cost_tracker.can_call() is True

    def test_returns_false_when_at_cap(self, cost_tracker, db_session):
        """Cannot call when today's total equals the cap."""
        record = ClaudeApiCost(
            timestamp=datetime.utcnow(),
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=500,
            output_tokens=100,
            cost_usd=10.0,
            daily_total_usd=10.0,
        )
        db_session.add(record)
        db_session.commit()

        assert cost_tracker.can_call() is False

    def test_returns_false_when_over_cap(self, cost_tracker, db_session):
        """Cannot call when today's total exceeds the cap."""
        record = ClaudeApiCost(
            timestamp=datetime.utcnow(),
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=500,
            output_tokens=100,
            cost_usd=15.0,
            daily_total_usd=15.0,
        )
        db_session.add(record)
        db_session.commit()

        assert cost_tracker.can_call() is False


class TestCostTrackerRecord:
    """CostTracker.record() persists cost record to the database."""

    def test_record_persists(self, cost_tracker, db_session):
        """A recorded cost appears in the database."""
        cost_tracker.record(
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=1000,
            output_tokens=200,
            cost_usd=0.018,
        )

        rows = db_session.query(ClaudeApiCost).all()
        assert len(rows) == 1
        assert rows[0].model == "claude-opus-4-6"
        assert rows[0].purpose == "reasoning"
        assert rows[0].input_tokens == 1000
        assert rows[0].output_tokens == 200
        assert rows[0].cost_usd == pytest.approx(0.018)

    def test_record_updates_daily_total(self, cost_tracker, db_session):
        """daily_total_usd in the record reflects running total."""
        cost_tracker.record(
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=500,
            output_tokens=100,
            cost_usd=1.00,
        )
        cost_tracker.record(
            model="claude-opus-4-6",
            purpose="reflection",
            input_tokens=500,
            output_tokens=100,
            cost_usd=2.00,
        )

        rows = db_session.query(ClaudeApiCost).order_by(ClaudeApiCost.id).all()
        assert rows[0].daily_total_usd == pytest.approx(1.00)
        assert rows[1].daily_total_usd == pytest.approx(3.00)

    def test_record_with_decision_audit_id(self, cost_tracker, db_session):
        """decision_audit_id foreign key is stored when provided."""
        # Create a valid DecisionAudit row so the FK constraint is satisfied
        audit = DecisionAudit(
            timestamp=datetime.utcnow(),
            autonomy_level=1,
            event_type="SCHEDULED_CHECK",
            action="MONITOR_ONLY",
            autonomy_approved=True,
        )
        db_session.add(audit)
        db_session.flush()  # populate audit.id

        cost_tracker.record(
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=500,
            output_tokens=100,
            cost_usd=0.50,
            decision_audit_id=audit.id,
        )

        row = db_session.query(ClaudeApiCost).one()
        assert row.decision_audit_id == audit.id

    def test_record_without_decision_audit_id(self, cost_tracker, db_session):
        """decision_audit_id defaults to None when not provided."""
        cost_tracker.record(
            model="claude-sonnet-4-5-20250929",
            purpose="reflection",
            input_tokens=500,
            output_tokens=100,
            cost_usd=0.50,
        )

        row = db_session.query(ClaudeApiCost).one()
        assert row.decision_audit_id is None


# ===========================================================================
# ClaudeReasoningEngine.reason() Tests
# ===========================================================================


class TestReasonClaude:
    """ClaudeReasoningEngine.reason() calls Claude and returns DecisionOutput."""

    def test_reason_returns_decision_output(
        self, db_session, sample_context, valid_claude_response
    ):
        """reason() calls the reasoning agent and returns a DecisionOutput."""
        mock_reasoning = MagicMock()
        mock_reasoning.send_message.return_value = valid_claude_response
        mock_reasoning.estimate_cost.return_value = 0.018

        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)

        result = engine.reason(sample_context, event_type="SCHEDULED_CHECK")

        assert isinstance(result, DecisionOutput)
        assert result.action == "MONITOR_ONLY"
        assert result.confidence == pytest.approx(0.85)
        assert result.reasoning == "Market is stable, no action needed"
        assert "low_vix" in result.key_factors
        assert "overnight_gap" in result.risks_considered

    def test_reason_calls_send_message_with_correct_params(
        self, db_session, sample_context, valid_claude_response
    ):
        """reason() passes system prompt and assembled user message to send_message."""
        mock_reasoning = MagicMock()
        mock_reasoning.send_message.return_value = valid_claude_response
        mock_reasoning.estimate_cost.return_value = 0.018

        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)
        engine.reason(sample_context, event_type="MARKET_OPEN")

        call_kwargs = mock_reasoning.send_message.call_args
        assert "system_prompt" in call_kwargs.kwargs or len(call_kwargs.args) >= 1
        # Verify the system prompt contains key instruction text
        system_prompt = call_kwargs.kwargs.get("system_prompt", call_kwargs.args[0] if call_kwargs.args else "")
        assert "reasoning engine" in system_prompt.lower()

    def test_reason_records_cost(
        self, db_session, sample_context, valid_claude_response
    ):
        """reason() records the API cost via CostTracker."""
        mock_reasoning = MagicMock()
        mock_reasoning.send_message.return_value = valid_claude_response
        mock_reasoning.estimate_cost.return_value = 0.018

        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)
        engine.reason(sample_context, event_type="SCHEDULED_CHECK")

        rows = db_session.query(ClaudeApiCost).all()
        assert len(rows) == 1
        assert rows[0].purpose == "reasoning"
        assert rows[0].cost_usd == pytest.approx(0.018)


class TestReasonCostCapFallback:
    """ClaudeReasoningEngine.reason() falls back to MONITOR_ONLY when cost cap exceeded."""

    def test_falls_back_when_cost_cap_exceeded(self, db_session, sample_context):
        """When daily cost cap is exceeded, returns MONITOR_ONLY without calling Claude."""
        # Pre-fill cost to exceed cap
        record = ClaudeApiCost(
            timestamp=datetime.utcnow(),
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=50000,
            output_tokens=10000,
            cost_usd=15.0,
            daily_total_usd=15.0,
        )
        db_session.add(record)
        db_session.commit()

        mock_reasoning = MagicMock()
        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)

        result = engine.reason(sample_context, event_type="SCHEDULED_CHECK")

        assert result.action == "MONITOR_ONLY"
        assert result.confidence == 1.0
        assert "cost cap" in result.reasoning.lower()
        assert "cost_cap_exceeded" in result.key_factors
        # Claude should NOT have been called
        mock_reasoning.send_message.assert_not_called()


class TestReasonClaudeFailureFallback:
    """ClaudeReasoningEngine.reason() falls back to MONITOR_ONLY on Claude failure."""

    def test_falls_back_on_exception(self, db_session, sample_context):
        """When Claude raises an exception on all attempts, returns MONITOR_ONLY."""
        mock_reasoning = MagicMock()
        mock_reasoning.send_message.side_effect = RuntimeError("API error")

        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)
        result = engine.reason(sample_context, event_type="SCHEDULED_CHECK")

        assert result.action == "MONITOR_ONLY"
        assert result.confidence == 1.0
        assert "failed" in result.reasoning.lower()
        assert "reasoning_failure" in result.key_factors

    def test_retries_once_then_falls_back(self, db_session, sample_context):
        """Claude is called twice (initial + 1 retry) before falling back."""
        mock_reasoning = MagicMock()
        mock_reasoning.send_message.side_effect = RuntimeError("API error")

        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)
        engine.reason(sample_context, event_type="SCHEDULED_CHECK")

        assert mock_reasoning.send_message.call_count == 2

    def test_falls_back_on_unparseable_response(self, db_session, sample_context):
        """When Claude returns unparseable content on both attempts, falls back."""
        mock_reasoning = MagicMock()
        mock_reasoning.send_message.return_value = {
            "content": "This is not valid JSON at all!!!",
            "input_tokens": 500,
            "output_tokens": 50,
            "model": "claude-opus-4-6",
        }
        mock_reasoning.estimate_cost.return_value = 0.01

        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)
        result = engine.reason(sample_context, event_type="SCHEDULED_CHECK")

        assert result.action == "MONITOR_ONLY"
        assert "reasoning_failure" in result.key_factors

    def test_recovers_on_second_attempt(self, db_session, sample_context):
        """If first attempt fails but second succeeds, returns the second result."""
        good_response = {
            "content": json.dumps(
                {
                    "action": "EXECUTE_TRADES",
                    "confidence": 0.90,
                    "reasoning": "Ready to trade",
                    "key_factors": ["good_setup"],
                    "risks_considered": [],
                    "metadata": {},
                }
            ),
            "input_tokens": 1000,
            "output_tokens": 200,
            "model": "claude-opus-4-6",
        }

        mock_reasoning = MagicMock()
        mock_reasoning.send_message.side_effect = [
            RuntimeError("Transient error"),
            good_response,
        ]
        mock_reasoning.estimate_cost.return_value = 0.018

        engine = _make_engine(db_session, reasoning_agent=mock_reasoning)
        result = engine.reason(sample_context, event_type="SCHEDULED_CHECK")

        assert result.action == "EXECUTE_TRADES"
        assert result.confidence == pytest.approx(0.90)


# ===========================================================================
# _parse_response() Tests
# ===========================================================================


class TestParseResponseValidJSON:
    """_parse_response() handles valid JSON."""

    def test_parses_valid_json(self, db_session):
        """Parses a well-formed JSON string into DecisionOutput."""
        engine = _make_engine(db_session)

        content = json.dumps(
            {
                "action": "STAGE_CANDIDATES",
                "confidence": 0.75,
                "reasoning": "Sunday screening time",
                "key_factors": ["sunday", "market_closed"],
                "risks_considered": ["stale_data"],
                "metadata": {"session": "sunday_scan"},
            }
        )

        result = engine._parse_response(content)

        assert result is not None
        assert result.action == "STAGE_CANDIDATES"
        assert result.confidence == pytest.approx(0.75)
        assert result.reasoning == "Sunday screening time"
        assert result.key_factors == ["sunday", "market_closed"]
        assert result.risks_considered == ["stale_data"]
        assert result.metadata == {"session": "sunday_scan"}

    def test_parses_minimal_json(self, db_session):
        """Parses JSON with only action field; defaults fill in."""
        engine = _make_engine(db_session)
        content = json.dumps({"action": "MONITOR_ONLY"})

        result = engine._parse_response(content)

        assert result is not None
        assert result.action == "MONITOR_ONLY"
        assert result.confidence == pytest.approx(0.5)  # default
        assert result.reasoning == ""
        assert result.key_factors == []
        assert result.risks_considered == []
        assert result.metadata == {}

    def test_clamps_confidence_above_one(self, db_session):
        """Confidence above 1.0 is clamped to 1.0."""
        engine = _make_engine(db_session)
        content = json.dumps({"action": "MONITOR_ONLY", "confidence": 1.5})

        result = engine._parse_response(content)
        assert result.confidence == pytest.approx(1.0)

    def test_clamps_confidence_below_zero(self, db_session):
        """Confidence below 0.0 is clamped to 0.0."""
        engine = _make_engine(db_session)
        content = json.dumps({"action": "MONITOR_ONLY", "confidence": -0.3})

        result = engine._parse_response(content)
        assert result.confidence == pytest.approx(0.0)

    def test_parses_all_valid_actions(self, db_session):
        """Every action in VALID_ACTIONS is accepted."""
        engine = _make_engine(db_session)

        for action in VALID_ACTIONS:
            content = json.dumps({"action": action, "confidence": 0.7, "reasoning": f"Testing {action}"})
            result = engine._parse_response(content)
            assert result is not None
            assert result.action == action


class TestParseResponseMarkdownCodeBlocks:
    """_parse_response() handles markdown code blocks."""

    def test_parses_json_code_block(self, db_session):
        """Extracts JSON from ```json ... ``` code block."""
        engine = _make_engine(db_session)

        inner_json = json.dumps(
            {
                "action": "EXECUTE_TRADES",
                "confidence": 0.92,
                "reasoning": "All checks passed",
            }
        )
        content = f"Here is my analysis:\n```json\n{inner_json}\n```\nEnd of response."

        result = engine._parse_response(content)

        assert result is not None
        assert result.action == "EXECUTE_TRADES"
        assert result.confidence == pytest.approx(0.92)

    def test_parses_generic_code_block(self, db_session):
        """Extracts JSON from plain ``` ... ``` code block."""
        engine = _make_engine(db_session)

        inner_json = json.dumps(
            {
                "action": "CLOSE_POSITION",
                "confidence": 0.80,
                "reasoning": "Stop loss triggered",
                "metadata": {"position_id": "POS-123"},
            }
        )
        content = f"```\n{inner_json}\n```"

        result = engine._parse_response(content)

        assert result is not None
        assert result.action == "CLOSE_POSITION"
        assert result.metadata == {"position_id": "POS-123"}


class TestParseResponseInvalidJSON:
    """_parse_response() handles invalid JSON (returns None)."""

    def test_returns_none_for_plain_text(self, db_session):
        """Returns None when content is plain text."""
        engine = _make_engine(db_session)
        result = engine._parse_response("I think we should monitor the market.")
        assert result is None

    def test_returns_none_for_truncated_json(self, db_session):
        """Returns None when JSON is truncated."""
        engine = _make_engine(db_session)
        result = engine._parse_response('{"action": "MONITOR_ONLY", "confidence":')
        assert result is None

    def test_returns_none_for_empty_string(self, db_session):
        """Returns None for empty string."""
        engine = _make_engine(db_session)
        result = engine._parse_response("")
        assert result is None

    def test_returns_none_for_empty_code_block(self, db_session):
        """Returns None when code block contains non-JSON text."""
        engine = _make_engine(db_session)
        result = engine._parse_response("```\nNot JSON at all\n```")
        assert result is None


class TestParseResponseInvalidAction:
    """_parse_response() handles invalid action (defaults to MONITOR_ONLY)."""

    def test_defaults_to_monitor_only_for_unknown_action(self, db_session):
        """Invalid action string is replaced with MONITOR_ONLY."""
        engine = _make_engine(db_session)

        content = json.dumps(
            {
                "action": "SELL_EVERYTHING",
                "confidence": 0.95,
                "reasoning": "Panic mode",
            }
        )

        result = engine._parse_response(content)

        assert result is not None
        assert result.action == "MONITOR_ONLY"
        assert result.confidence == pytest.approx(0.95)

    def test_defaults_to_monitor_only_for_missing_action(self, db_session):
        """Missing action key defaults to MONITOR_ONLY."""
        engine = _make_engine(db_session)

        content = json.dumps(
            {
                "confidence": 0.70,
                "reasoning": "No action specified",
            }
        )

        result = engine._parse_response(content)

        assert result is not None
        assert result.action == "MONITOR_ONLY"

    def test_defaults_to_monitor_only_for_empty_action(self, db_session):
        """Empty-string action defaults to MONITOR_ONLY."""
        engine = _make_engine(db_session)

        content = json.dumps(
            {
                "action": "",
                "confidence": 0.50,
                "reasoning": "Empty action",
            }
        )

        result = engine._parse_response(content)

        assert result is not None
        assert result.action == "MONITOR_ONLY"


# ===========================================================================
# reflect() Tests
# ===========================================================================


class TestReflect:
    """reflect() calls Sonnet model for EOD reflection."""

    def test_reflect_calls_reflection_agent(self, db_session):
        """reflect() calls the reflection agent (Sonnet) with decisions and trades."""
        reflection_response = {
            "content": json.dumps(
                {
                    "correct_decisions": ["Held position through dip"],
                    "lucky_decisions": [],
                    "wrong_decisions": [],
                    "patterns_to_investigate": ["VIX spike timing"],
                    "prior_updates": [],
                    "summary": "Good day overall",
                }
            ),
            "input_tokens": 800,
            "output_tokens": 300,
            "model": "claude-sonnet-4-5-20250929",
        }

        mock_reflection = MagicMock()
        mock_reflection.send_message.return_value = reflection_response
        mock_reflection.estimate_cost.return_value = 0.007

        engine = _make_engine(db_session, reflection_agent=mock_reflection)

        decisions_today = [{"action": "MONITOR_ONLY", "timestamp": "2026-02-19T10:00:00"}]
        trades_today = [{"symbol": "AAPL", "profit_loss": 50.0}]

        result = engine.reflect(decisions_today, trades_today)

        mock_reflection.send_message.assert_called_once()
        assert result["summary"] == "Good day overall"
        assert "correct_decisions" in result
        assert result["patterns_to_investigate"] == ["VIX spike timing"]

    def test_reflect_records_cost(self, db_session):
        """reflect() records the API cost for the reflection call."""
        reflection_response = {
            "content": json.dumps({"summary": "Quiet day"}),
            "input_tokens": 500,
            "output_tokens": 150,
            "model": "claude-sonnet-4-5-20250929",
        }

        mock_reflection = MagicMock()
        mock_reflection.send_message.return_value = reflection_response
        mock_reflection.estimate_cost.return_value = 0.004

        engine = _make_engine(db_session, reflection_agent=mock_reflection)
        engine.reflect([], [])

        rows = db_session.query(ClaudeApiCost).all()
        assert len(rows) == 1
        assert rows[0].purpose == "reflection"
        assert rows[0].cost_usd == pytest.approx(0.004)

    def test_reflect_skips_when_cost_cap_exceeded(self, db_session):
        """reflect() returns a skip message when cost cap is exceeded."""
        record = ClaudeApiCost(
            timestamp=datetime.utcnow(),
            model="claude-opus-4-6",
            purpose="reasoning",
            input_tokens=50000,
            output_tokens=10000,
            cost_usd=15.0,
            daily_total_usd=15.0,
        )
        db_session.add(record)
        db_session.commit()

        mock_reflection = MagicMock()
        engine = _make_engine(db_session, reflection_agent=mock_reflection)

        result = engine.reflect([], [])

        assert "cost cap" in result["summary"].lower()
        mock_reflection.send_message.assert_not_called()

    def test_reflect_handles_exception(self, db_session):
        """reflect() returns error summary when Claude raises an exception."""
        mock_reflection = MagicMock()
        mock_reflection.send_message.side_effect = RuntimeError("Sonnet API down")

        engine = _make_engine(db_session, reflection_agent=mock_reflection)
        result = engine.reflect([], [])

        assert "error" in result["summary"].lower()

    def test_reflect_handles_non_json_response(self, db_session):
        """reflect() gracefully handles non-JSON response from Claude."""
        reflection_response = {
            "content": "Today was uneventful. No major decisions were made.",
            "input_tokens": 400,
            "output_tokens": 50,
            "model": "claude-sonnet-4-5-20250929",
        }

        mock_reflection = MagicMock()
        mock_reflection.send_message.return_value = reflection_response
        mock_reflection.estimate_cost.return_value = 0.002

        engine = _make_engine(db_session, reflection_agent=mock_reflection)
        result = engine.reflect([], [])

        # _parse_reflection falls back to returning {"summary": content[:500]}
        assert "summary" in result
        assert "uneventful" in result["summary"]

    def test_reflect_uses_sonnet_temperature(self, db_session):
        """reflect() calls send_message with temperature=0.3 and max_tokens=2048."""
        reflection_response = {
            "content": json.dumps({"summary": "OK"}),
            "input_tokens": 300,
            "output_tokens": 50,
            "model": "claude-sonnet-4-5-20250929",
        }

        mock_reflection = MagicMock()
        mock_reflection.send_message.return_value = reflection_response
        mock_reflection.estimate_cost.return_value = 0.001

        engine = _make_engine(db_session, reflection_agent=mock_reflection)
        engine.reflect([{"action": "MONITOR_ONLY"}], [])

        call_kwargs = mock_reflection.send_message.call_args.kwargs
        assert call_kwargs["max_tokens"] == 2048
        assert call_kwargs["temperature"] == pytest.approx(0.3)


# ===========================================================================
# DecisionOutput dataclass tests
# ===========================================================================


class TestDecisionOutput:
    """DecisionOutput dataclass tests."""

    def test_default_fields(self):
        """Default field values are correct."""
        d = DecisionOutput(
            action="MONITOR_ONLY",
            confidence=0.5,
            reasoning="test",
        )
        assert d.key_factors == []
        assert d.risks_considered == []
        assert d.metadata == {}

    def test_all_fields(self):
        """All fields are stored correctly."""
        d = DecisionOutput(
            action="EXECUTE_TRADES",
            confidence=0.99,
            reasoning="All green",
            key_factors=["a", "b"],
            risks_considered=["c"],
            metadata={"foo": "bar"},
        )
        assert d.action == "EXECUTE_TRADES"
        assert d.confidence == 0.99
        assert d.key_factors == ["a", "b"]
        assert d.metadata == {"foo": "bar"}


# ===========================================================================
# VALID_ACTIONS constant tests
# ===========================================================================


class TestValidActions:
    """Ensure VALID_ACTIONS contains the expected actions."""

    def test_all_expected_actions_present(self):
        expected = {
            "MONITOR_ONLY",
            "STAGE_CANDIDATES",
            "EXECUTE_TRADES",
            "CLOSE_POSITION",
            "ADJUST_PARAMETERS",
            "RUN_EXPERIMENT",
            "REQUEST_HUMAN_REVIEW",
            "EMERGENCY_STOP",
        }
        assert VALID_ACTIONS == expected

    def test_valid_actions_is_a_set(self):
        assert isinstance(VALID_ACTIONS, set)
