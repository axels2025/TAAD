"""Tests for Phase 6.2: Context Validation guardrails.

Tests data freshness, consistency checks, and null sanitization.
"""

import pytest
from dataclasses import dataclass, field

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.context_validator import ContextValidator


@dataclass
class FakeContext:
    market_context: dict = field(default_factory=dict)
    open_positions: list = field(default_factory=list)
    staged_candidates: list = field(default_factory=list)
    recent_trades: list = field(default_factory=list)
    autonomy_level: int = 1
    data_limitations: list = field(default_factory=list)


@pytest.fixture
def validator():
    return ContextValidator()


@pytest.fixture
def config():
    return GuardrailConfig()


# ---------- Data Freshness ----------


def test_stale_data_blocks(validator, config):
    """Stale market data should block (skip Claude call)."""
    context = FakeContext(market_context={"data_stale": True, "vix": 15.0})

    results = validator.validate(context, config)
    freshness = [r for r in results if r.guard_name == "data_freshness"]

    assert len(freshness) == 1
    assert not freshness[0].passed
    assert freshness[0].severity == "block"
    assert "stale" in freshness[0].reason.lower()


def test_fresh_data_passes(validator, config):
    """Fresh market data should pass."""
    context = FakeContext(market_context={"data_stale": False, "vix": 15.0, "spy_price": 500.0})

    results = validator.validate(context, config)
    freshness = [r for r in results if r.guard_name == "data_freshness"]

    assert len(freshness) == 1
    assert freshness[0].passed


def test_missing_data_stale_flag_passes(validator, config):
    """Missing data_stale flag defaults to fresh (not stale)."""
    context = FakeContext(market_context={"vix": 15.0})

    results = validator.validate(context, config)
    freshness = [r for r in results if r.guard_name == "data_freshness"]

    assert freshness[0].passed


# ---------- Consistency Checks ----------


def test_vix_out_of_range_blocks(validator, config):
    """VIX outside 5-100 range should block."""
    context = FakeContext(market_context={"vix": 150.0, "spy_price": 500.0})

    results = validator.validate(context, config)
    consistency = [r for r in results if r.guard_name == "consistency_check"]

    blocked = [r for r in consistency if not r.passed and r.severity == "block"]
    assert len(blocked) >= 1
    assert any("VIX" in r.reason for r in blocked)


def test_vix_below_range_blocks(validator, config):
    """VIX below 5.0 should block."""
    context = FakeContext(market_context={"vix": 2.0, "spy_price": 500.0})

    results = validator.validate(context, config)
    consistency = [r for r in results if r.guard_name == "consistency_check"]

    blocked = [r for r in consistency if not r.passed and r.severity == "block"]
    assert len(blocked) >= 1


def test_spy_out_of_range_blocks(validator, config):
    """SPY price outside 100-1000 range should block."""
    context = FakeContext(market_context={"vix": 15.0, "spy_price": 50.0})

    results = validator.validate(context, config)
    consistency = [r for r in results if r.guard_name == "consistency_check"]

    blocked = [r for r in consistency if not r.passed and r.severity == "block"]
    assert len(blocked) >= 1
    assert any("SPY" in r.reason for r in blocked)


def test_spy_zero_treated_as_unavailable(validator, config):
    """SPY price 0.0 (IBKR failure) should be treated as unavailable, not block."""
    context = FakeContext(market_context={"vix": 15.0, "spy_price": 0.0})

    results = validator.validate(context, config)
    consistency = [r for r in results if r.guard_name == "consistency_check"]

    # Should NOT produce a block — 0.0 is unavailable, not implausible
    blocked = [r for r in consistency if not r.passed and r.severity == "block"]
    assert len(blocked) == 0
    # Consistency nulls the field; null_sanitization then replaces with "UNKNOWN"
    assert context.market_context["spy_price"] == "UNKNOWN"
    assert any("spy_price" in lim for lim in context.data_limitations)


def test_vix_zero_treated_as_unavailable(validator, config):
    """VIX 0.0 (IBKR failure) should be treated as unavailable, not block."""
    context = FakeContext(market_context={"vix": 0.0, "spy_price": 500.0})

    results = validator.validate(context, config)
    consistency = [r for r in results if r.guard_name == "consistency_check"]

    blocked = [r for r in consistency if not r.passed and r.severity == "block"]
    assert len(blocked) == 0
    assert context.market_context["vix"] == "UNKNOWN"
    assert any("vix" in lim for lim in context.data_limitations)


def test_valid_values_pass(validator, config):
    """Valid VIX and SPY should pass consistency."""
    context = FakeContext(market_context={"vix": 22.0, "spy_price": 580.0})

    results = validator.validate(context, config)
    consistency = [r for r in results if r.guard_name == "consistency_check"]

    assert all(r.passed for r in consistency)


# ---------- Null Sanitization ----------


def test_null_fields_replaced_and_listed_in_limitations(validator, config):
    """None values in critical fields should be replaced and tracked."""
    context = FakeContext(market_context={"vix": None, "spy_price": None})

    results = validator.validate(context, config)
    null_results = [r for r in results if r.guard_name == "null_sanitization"]

    assert len(null_results) >= 1
    # Fields should be replaced with "UNKNOWN"
    assert context.market_context["vix"] == "UNKNOWN"
    assert context.market_context["spy_price"] == "UNKNOWN"
    # Data limitations should be populated
    assert len(context.data_limitations) >= 2
    assert any("vix" in lim for lim in context.data_limitations)
    assert any("spy_price" in lim for lim in context.data_limitations)


def test_no_nulls_no_limitations(validator, config):
    """No null critical fields should produce no limitations."""
    context = FakeContext(
        market_context={"vix": 20.0, "spy_price": 500.0, "conditions_favorable": True}
    )

    results = validator.validate(context, config)
    null_results = [r for r in results if r.guard_name == "null_sanitization"]

    assert all(r.passed for r in null_results)
    assert context.data_limitations == []


def test_partial_nulls_tracked(validator, config):
    """Only null fields should be tracked in limitations."""
    context = FakeContext(
        market_context={"vix": 20.0, "spy_price": None, "conditions_favorable": True}
    )

    results = validator.validate(context, config)

    assert context.market_context["spy_price"] == "UNKNOWN"
    assert context.market_context["vix"] == 20.0  # Unchanged
    assert len(context.data_limitations) == 1
    assert "spy_price" in context.data_limitations[0]


# ---------- Disabled Guards ----------


def test_disabled_freshness_skipped(validator):
    """Disabled freshness check should be skipped."""
    config = GuardrailConfig(data_freshness_enabled=False)
    context = FakeContext(market_context={"data_stale": True})

    results = validator.validate(context, config)
    freshness = [r for r in results if r.guard_name == "data_freshness"]

    assert len(freshness) == 0


def test_disabled_consistency_skipped(validator):
    """Disabled consistency check should be skipped."""
    config = GuardrailConfig(consistency_check_enabled=False)
    context = FakeContext(market_context={"vix": 999.0})

    results = validator.validate(context, config)
    consistency = [r for r in results if r.guard_name == "consistency_check"]

    assert len(consistency) == 0
