"""Tests for Phase 6.1: Output Validation guardrails.

Tests action plausibility, symbol cross-reference, and reasoning-action
coherence checks.
"""

import pytest
from dataclasses import dataclass, field

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.output_validator import OutputValidator
from src.agentic.guardrails.registry import GuardrailRegistry, GuardrailResult


# Lightweight stand-ins for DecisionOutput and ReasoningContext
@dataclass
class FakeDecision:
    action: str = "MONITOR_ONLY"
    confidence: float = 0.7
    reasoning: str = "Market conditions look stable, continuing to monitor."
    key_factors: list = field(default_factory=lambda: ["VIX low", "market stable", "no candidates"])
    risks_considered: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class FakeContext:
    staged_candidates: list = field(default_factory=list)
    open_positions: list = field(default_factory=list)
    recent_trades: list = field(default_factory=list)
    market_context: dict = field(default_factory=dict)
    autonomy_level: int = 1


@pytest.fixture
def validator():
    return OutputValidator()


@pytest.fixture
def config():
    return GuardrailConfig()


# ---------- Action Plausibility ----------


def test_execute_blocked_when_no_candidates_staged(validator, config):
    """EXECUTE_TRADES should be blocked when no staged candidates exist."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(staged_candidates=[])

    results = validator.validate(decision, context, config)
    plausibility = [r for r in results if r.guard_name == "action_plausibility"]

    assert len(plausibility) == 1
    assert not plausibility[0].passed
    assert plausibility[0].severity == "block"
    assert "no staged candidates" in plausibility[0].reason


def test_execute_passes_when_candidates_staged(validator, config):
    """EXECUTE_TRADES should pass when staged candidates exist."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        staged_candidates=[
            {"symbol": "AAPL", "strike": 200, "expiration": "2026-03-20", "limit_price": 0.30, "contracts": 1, "state": "STAGED"},
        ]
    )

    results = validator.validate(decision, context, config)
    plausibility = [r for r in results if r.guard_name == "action_plausibility"]

    assert len(plausibility) == 1
    assert plausibility[0].passed


def test_close_position_blocked_when_position_not_in_context(validator, config):
    """CLOSE_POSITION should be blocked when position_id not in open positions."""
    decision = FakeDecision(
        action="CLOSE_POSITION",
        metadata={"position_id": "FAKE-123"},
    )
    context = FakeContext(open_positions=[])

    results = validator.validate(decision, context, config)
    plausibility = [r for r in results if r.guard_name == "action_plausibility"]

    assert len(plausibility) == 1
    assert not plausibility[0].passed
    assert plausibility[0].severity == "block"


def test_close_position_blocked_when_no_position_id(validator, config):
    """CLOSE_POSITION should be blocked when no position_id in metadata."""
    decision = FakeDecision(action="CLOSE_POSITION", metadata={})
    context = FakeContext()

    results = validator.validate(decision, context, config)
    plausibility = [r for r in results if r.guard_name == "action_plausibility"]

    assert any(not r.passed and r.severity == "block" for r in plausibility)


def test_run_experiment_blocked_when_no_params(validator, config):
    """RUN_EXPERIMENT should be blocked when no experiment parameters."""
    decision = FakeDecision(action="RUN_EXPERIMENT", metadata={})
    context = FakeContext()

    results = validator.validate(decision, context, config)
    plausibility = [r for r in results if r.guard_name == "action_plausibility"]

    assert any(not r.passed and r.severity == "block" for r in plausibility)


def test_monitor_only_always_passes(validator, config):
    """MONITOR_ONLY should always pass plausibility."""
    decision = FakeDecision(action="MONITOR_ONLY")
    context = FakeContext()

    results = validator.validate(decision, context, config)
    plausibility = [r for r in results if r.guard_name == "action_plausibility"]

    assert all(r.passed for r in plausibility)


# ---------- Symbol Cross-Reference ----------


def test_unknown_symbol_in_reasoning_flagged(validator, config):
    """Symbols in reasoning not found in context should be flagged."""
    decision = FakeDecision(
        action="EXECUTE_TRADES",
        reasoning="TSLA looks like a great trade based on strong momentum.",
    )
    context = FakeContext(
        staged_candidates=[{"symbol": "AAPL", "strike": 200}],
    )

    results = validator.validate(decision, context, config)
    symbol_results = [r for r in results if r.guard_name == "symbol_crossref"]

    flagged = [r for r in symbol_results if not r.passed]
    assert len(flagged) >= 1
    assert any("TSLA" in r.reason for r in flagged)


def test_known_symbols_pass(validator, config):
    """Symbols that exist in context should pass cross-reference."""
    decision = FakeDecision(
        action="MONITOR_ONLY",
        reasoning="AAPL position is performing well. VIX is low.",
    )
    context = FakeContext(
        open_positions=[{"symbol": "AAPL", "strike": 200}],
    )

    results = validator.validate(decision, context, config)
    symbol_results = [r for r in results if r.guard_name == "symbol_crossref"]

    assert all(r.passed for r in symbol_results)


def test_common_abbreviations_not_flagged(validator, config):
    """Common abbreviations like VIX, SPY, DTE, OTM should not be flagged."""
    decision = FakeDecision(
        action="MONITOR_ONLY",
        reasoning="VIX is at 15. SPY trending up. OTM positions look safe. DTE is 5 days. IV is elevated.",
    )
    context = FakeContext()

    results = validator.validate(decision, context, config)
    symbol_results = [r for r in results if r.guard_name == "symbol_crossref"]

    assert all(r.passed for r in symbol_results)


def test_symbols_from_recent_trades_known(validator, config):
    """Symbols from recent_trades should be recognized."""
    decision = FakeDecision(
        action="MONITOR_ONLY",
        reasoning="MSFT had a good outcome in the recent trade.",
    )
    context = FakeContext(
        recent_trades=[{"symbol": "MSFT", "profit_loss": 50.0}],
    )

    results = validator.validate(decision, context, config)
    symbol_results = [r for r in results if r.guard_name == "symbol_crossref"]

    assert all(r.passed for r in symbol_results)


# ---------- Reasoning Coherence ----------


def test_contradictory_reasoning_action_flagged(validator, config):
    """Reasoning suggesting 'no action' with EXECUTE_TRADES action should flag."""
    decision = FakeDecision(
        action="EXECUTE_TRADES",
        reasoning="Market is uncertain, we should wait and take no action for now.",
        key_factors=["uncertainty", "wait for clarity", "monitoring recommended"],
    )
    context = FakeContext(
        staged_candidates=[{"symbol": "AAPL", "strike": 200}],
    )

    results = validator.validate(decision, context, config)
    coherence = [r for r in results if r.guard_name == "reasoning_coherence"]

    assert any(not r.passed for r in coherence)
    assert any("monitoring/waiting" in r.reason for r in coherence)


def test_execute_language_with_monitor_action_warned(validator, config):
    """Reasoning saying 'execute' but action MONITOR_ONLY should warn."""
    decision = FakeDecision(
        action="MONITOR_ONLY",
        reasoning="We should execute the staged trades now before the premium decays.",
        key_factors=["premium decay", "good entry", "time sensitive"],
    )
    context = FakeContext()

    results = validator.validate(decision, context, config)
    coherence = [r for r in results if r.guard_name == "reasoning_coherence"]

    assert any(not r.passed and "execution" in r.reason for r in coherence)


def test_high_confidence_low_factors_warned(validator, config):
    """High confidence (>0.85) with <3 key_factors should warn."""
    decision = FakeDecision(
        action="MONITOR_ONLY",
        confidence=0.90,
        reasoning="Everything looks good and stable. VIX is low. Market is calm. No concerns at all.",
        key_factors=["VIX low"],
    )
    context = FakeContext()

    results = validator.validate(decision, context, config)
    coherence = [r for r in results if r.guard_name == "reasoning_coherence"]

    warned = [r for r in coherence if not r.passed]
    assert any("key factors" in r.reason for r in warned)


def test_high_confidence_short_reasoning_warned(validator, config):
    """High confidence with very short reasoning should warn."""
    decision = FakeDecision(
        action="MONITOR_ONLY",
        confidence=0.90,
        reasoning="Looks good.",
        key_factors=["good"],
    )
    context = FakeContext()

    results = validator.validate(decision, context, config)
    coherence = [r for r in results if r.guard_name == "reasoning_coherence"]

    warned = [r for r in coherence if not r.passed]
    assert any("short reasoning" in r.reason for r in warned)


def test_coherent_reasoning_passes(validator, config):
    """Coherent reasoning+action should pass all checks."""
    decision = FakeDecision(
        action="MONITOR_ONLY",
        confidence=0.70,
        reasoning="Market conditions are stable. VIX at 15 is within normal range. No staged candidates available.",
        key_factors=["VIX normal", "no candidates", "stable market"],
    )
    context = FakeContext()

    results = validator.validate(decision, context, config)
    coherence = [r for r in results if r.guard_name == "reasoning_coherence"]

    assert all(r.passed for r in coherence)


# ---------- Registry Integration ----------


def test_registry_overrides_to_monitor_only_on_block():
    """Registry.has_block() should detect blocking results."""
    registry = GuardrailRegistry()
    registry.register_output_validator(OutputValidator())

    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(staged_candidates=[])

    results = registry.validate_output(decision, context)

    assert registry.has_block(results)
    reasons = registry.get_block_reasons(results)
    assert len(reasons) >= 1
    assert "action_plausibility" in reasons[0]


def test_registry_disabled_returns_empty():
    """Disabled guardrails should return no results."""
    config = GuardrailConfig(enabled=False)
    registry = GuardrailRegistry(config)
    registry.register_output_validator(OutputValidator())

    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(staged_candidates=[])

    results = registry.validate_output(decision, context)
    assert results == []


def test_results_to_dict_serialization():
    """Results should be serializable to dict for JSON storage."""
    registry = GuardrailRegistry()
    results = [
        GuardrailResult(
            passed=False,
            guard_name="test_guard",
            severity="block",
            reason="Test reason",
            details={"key": "value"},
        )
    ]

    serialized = registry.results_to_dict(results)
    assert len(serialized) == 1
    assert serialized[0]["passed"] is False
    assert serialized[0]["guard_name"] == "test_guard"
    assert serialized[0]["severity"] == "block"
    assert serialized[0]["reason"] == "Test reason"
    assert serialized[0]["details"]["key"] == "value"
