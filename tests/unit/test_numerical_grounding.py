"""Tests for Phase 6.4: Numerical Grounding guardrail.

Tests that numbers cited in Claude's reasoning are verified against
the context data.
"""

import pytest
from dataclasses import dataclass, field

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.numerical_grounding import NumericalGroundingChecker


@dataclass
class FakeDecision:
    action: str = "MONITOR_ONLY"
    confidence: float = 0.7
    reasoning: str = ""
    key_factors: list = field(default_factory=list)
    risks_considered: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


@dataclass
class FakeContext:
    market_context: dict = field(default_factory=dict)
    open_positions: list = field(default_factory=list)
    staged_candidates: list = field(default_factory=list)
    recent_trades: list = field(default_factory=list)
    autonomy_level: int = 1


@pytest.fixture
def checker():
    return NumericalGroundingChecker()


@pytest.fixture
def config():
    return GuardrailConfig()


# ---------- VIX Claims ----------


def test_vix_claim_matches_context_passes(checker, config):
    """VIX claim matching context should pass."""
    decision = FakeDecision(
        reasoning="VIX is 22.5 which is within normal range."
    )
    context = FakeContext(market_context={"vix": 22.5})

    results = checker.validate(decision, context, config)
    assert all(r.passed for r in results)


def test_vix_claim_differs_flags(checker, config):
    """VIX claim differing significantly from context should flag."""
    decision = FakeDecision(
        reasoning="VIX is 73.0 indicating extreme fear."
    )
    context = FakeContext(market_context={"vix": 31.0})

    results = checker.validate(decision, context, config)
    failed = [r for r in results if not r.passed]
    assert len(failed) >= 1
    assert any("vix" in r.details.get("field", "") for r in failed)


def test_vix_within_tolerance_passes(checker, config):
    """VIX claim within 10% tolerance should pass."""
    decision = FakeDecision(
        reasoning="VIX is at 23.0 right now."
    )
    context = FakeContext(market_context={"vix": 22.0})

    results = checker.validate(decision, context, config)
    assert all(r.passed for r in results)


# ---------- Premium Claims ----------


def test_premium_within_tolerance_passes(checker, config):
    """Premium claim within tolerance should pass."""
    decision = FakeDecision(
        reasoning="The premium of $0.41 looks attractive."
    )
    context = FakeContext(
        staged_candidates=[{"symbol": "AAPL", "strike": 200, "limit_price": 0.40, "contracts": 1}]
    )

    results = checker.validate(decision, context, config)
    assert all(r.passed for r in results)


def test_fabricated_number_flagged(checker, config):
    """Premium not found in any context data should pass (no ground truth)."""
    decision = FakeDecision(
        reasoning="The premium of $5.50 is very high for this strike."
    )
    context = FakeContext(
        staged_candidates=[{"symbol": "AAPL", "strike": 200, "limit_price": 0.30, "contracts": 1}]
    )

    results = checker.validate(decision, context, config)
    failed = [r for r in results if not r.passed]
    assert len(failed) >= 1
    assert any("premium" in r.details.get("field", "") for r in failed)


# ---------- Strike Claims ----------


def test_strike_claim_matches(checker, config):
    """Strike matching context should pass."""
    decision = FakeDecision(
        reasoning="The 580 strike has good positioning."
    )
    context = FakeContext(
        open_positions=[{"symbol": "XSP", "strike": 580.0, "entry_premium": 0.30, "contracts": 1}]
    )

    results = checker.validate(decision, context, config)
    assert all(r.passed for r in results)


# ---------- No Numbers ----------


def test_no_numbers_in_reasoning_passes(checker, config):
    """Reasoning with no numbers should pass."""
    decision = FakeDecision(
        reasoning="Market conditions look stable. No concerns at this time."
    )
    context = FakeContext(market_context={"vix": 15.0, "spy_price": 500.0})

    results = checker.validate(decision, context, config)
    assert all(r.passed for r in results)


# ---------- Multiple Mismatches ----------


def test_multiple_wrong_numbers_blocks(checker, config):
    """Multiple numerical mismatches should result in block severity."""
    decision = FakeDecision(
        reasoning="VIX is 50.0 and SPY is at $700.00. Premium of $2.50."
    )
    context = FakeContext(
        market_context={"vix": 15.0, "spy_price": 500.0},
        staged_candidates=[{"symbol": "AAPL", "strike": 200, "limit_price": 0.30, "contracts": 1}],
    )

    results = checker.validate(decision, context, config)
    failed = [r for r in results if not r.passed]
    assert len(failed) >= 2
    assert any(r.severity == "block" for r in failed)


# ---------- Disabled Guard ----------


def test_disabled_returns_empty(checker):
    """Disabled numerical grounding should return empty results."""
    config = GuardrailConfig(numerical_grounding_enabled=False)
    decision = FakeDecision(reasoning="VIX is 999.0")
    context = FakeContext(market_context={"vix": 15.0})

    results = checker.validate(decision, context, config)
    assert results == []


# ---------- DTE Claims ----------


def test_dte_integer_tolerance(checker, config):
    """DTE claims within 1 of truth should pass."""
    decision = FakeDecision(
        reasoning="DTE of 5 gives us enough time for decay."
    )
    context = FakeContext(
        open_positions=[{"symbol": "AAPL", "strike": 200, "dte": 5, "contracts": 1}]
    )

    results = checker.validate(decision, context, config)
    assert all(r.passed for r in results)


def test_dte_far_off_flagged(checker, config):
    """DTE claim far from context value should flag."""
    decision = FakeDecision(
        reasoning="DTE is 45 which gives plenty of time."
    )
    context = FakeContext(
        open_positions=[{"symbol": "AAPL", "strike": 200, "dte": 5, "contracts": 1}]
    )

    results = checker.validate(decision, context, config)
    failed = [r for r in results if not r.passed]
    assert len(failed) >= 1


# ---------- Contract Claims ----------


def test_contracts_claim_matches(checker, config):
    """Contracts claim matching context should pass."""
    decision = FakeDecision(
        reasoning="We have 3 contracts in this position."
    )
    context = FakeContext(
        open_positions=[{"symbol": "AAPL", "strike": 200, "contracts": 3}]
    )

    results = checker.validate(decision, context, config)
    assert all(r.passed for r in results)
