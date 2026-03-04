"""Tests for Phase 6.5: Execution Gate guardrails.

Tests live state diff, order parameter bounds, and rate limiting.
"""

import time

import pytest
from dataclasses import dataclass, field
from unittest.mock import MagicMock, AsyncMock

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.execution_gate import ExecutionGate


@dataclass
class FakeDecision:
    action: str = "EXECUTE_TRADES"
    confidence: float = 0.8
    reasoning: str = "Ready to execute trades."
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
def gate():
    return ExecutionGate()


@pytest.fixture
def config():
    return GuardrailConfig()


# ---------- Monitor Only Skips Gate ----------


def test_monitor_only_skips_gate(gate, config):
    """MONITOR_ONLY should skip execution gate entirely."""
    decision = FakeDecision(action="MONITOR_ONLY")
    context = FakeContext()

    results = gate.validate(decision, context, config)
    assert all(r.passed for r in results)
    assert any("does not require" in r.reason for r in results)


def test_stage_candidates_skips_gate(gate, config):
    """STAGE_CANDIDATES should skip execution gate."""
    decision = FakeDecision(action="STAGE_CANDIDATES")
    context = FakeContext()

    results = gate.validate(decision, context, config)
    assert all(r.passed for r in results)


# ---------- Live State Diff ----------


def test_vix_moved_15pct_blocks(gate, config):
    """VIX moving >15% since context was built should block."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"vix": 20.0, "spy_price": 500.0},
        staged_candidates=[{"symbol": "AAPL", "strike": 480, "contracts": 1}],
    )

    # Mock IBKR client that returns VIX moved from 20 -> 25 (25% move)
    mock_client = MagicMock()
    gate._fetch_live_data = MagicMock(return_value=(25.0, 500.0))

    results = gate.check_live_state(decision, context, config, mock_client)
    blocked = [r for r in results if not r.passed and r.severity == "block"]
    assert len(blocked) >= 1
    assert any("VIX" in r.reason for r in blocked)


def test_small_movement_passes(gate, config):
    """Small VIX/SPY movements should pass."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"vix": 20.0, "spy_price": 500.0},
    )

    # VIX moved 5% (under 15% threshold), SPY moved 0.5% (under 2%)
    mock_client = MagicMock()
    gate._fetch_live_data = MagicMock(return_value=(21.0, 502.5))

    results = gate.check_live_state(decision, context, config, mock_client)
    assert all(r.passed for r in results)


def test_spy_moved_blocks(gate, config):
    """SPY moving >2% should block."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"vix": 20.0, "spy_price": 500.0},
    )

    # SPY moved from 500 -> 515 (3% move)
    mock_client = MagicMock()
    gate._fetch_live_data = MagicMock(return_value=(20.0, 515.0))

    results = gate.check_live_state(decision, context, config, mock_client)
    blocked = [r for r in results if not r.passed and r.severity == "block"]
    assert len(blocked) >= 1
    assert any("SPY" in r.reason for r in blocked)


def test_no_ibkr_client_warns(gate, config):
    """No IBKR client should produce a warning, not a block."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(market_context={"vix": 20.0, "spy_price": 500.0})

    results = gate.check_live_state(decision, context, config, ibkr_client=None)
    assert all(r.passed for r in results)
    assert any("No IBKR" in r.reason for r in results)


# ---------- Order Bounds ----------


def test_zero_contracts_blocks(gate, config):
    """Zero contracts should be blocked."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"spy_price": 500.0},
        staged_candidates=[{"symbol": "AAPL", "strike": 480, "contracts": 0}],
    )

    results = gate.check_order_bounds(decision, context, config)
    blocked = [r for r in results if not r.passed]
    assert len(blocked) >= 1


def test_strike_too_far_from_underlying_blocks(gate, config):
    """Strike >30% from underlying stock price should be blocked."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"spy_price": 500.0},
        staged_candidates=[{"symbol": "AAPL", "strike": 200, "contracts": 1, "stock_price": 500.0}],
    )

    results = gate.check_order_bounds(decision, context, config)
    blocked = [r for r in results if not r.passed]
    assert len(blocked) >= 1
    assert any("30%" in r.reason for r in blocked)


def test_valid_order_params_pass(gate, config):
    """Valid order parameters should pass."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"spy_price": 500.0},
        staged_candidates=[{"symbol": "XSP", "strike": 480, "contracts": 2}],
    )

    results = gate.check_order_bounds(decision, context, config)
    assert all(r.passed for r in results)


# ---------- Rate Limiting ----------


def test_rate_limit_blocks_rapid_execution(gate, config):
    """Exceeding max orders per minute should block."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"spy_price": 500.0},
        staged_candidates=[{"symbol": "XSP", "strike": 480, "contracts": 1}],
    )

    # Fill up the rate limiter
    for _ in range(config.max_orders_per_minute):
        gate._order_timestamps.append(time.time())

    result = gate.check_rate_limit(config)
    assert not result.passed
    assert result.severity == "block"
    assert "Rate limit" in result.reason


def test_rate_limit_passes_normal_usage(gate, config):
    """Normal usage should pass rate limit."""
    result = gate.check_rate_limit(config)
    assert result.passed


def test_rate_limit_old_timestamps_expire(gate, config):
    """Old timestamps outside the window should not count."""
    # Add timestamps from 2 minutes ago
    old_time = time.time() - 120
    for _ in range(10):
        gate._order_timestamps.append(old_time)

    result = gate.check_rate_limit(config)
    assert result.passed


# ---------- Disabled Gate ----------


def test_disabled_gate_returns_empty(gate):
    """Disabled execution gate should return empty results."""
    config = GuardrailConfig(execution_gate_enabled=False)
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext()

    results = gate.validate(decision, context, config)
    assert results == []


# ---------- Absolute VIX Circuit Breaker ----------


def test_absolute_vix_above_35_blocks(gate, config):
    """VIX above absolute threshold should block."""
    results = gate.check_absolute_vix(config, live_data=(36.0, 500.0))
    blocked = [r for r in results if not r.passed and r.severity == "block"]
    assert len(blocked) == 1
    assert "36.0" in blocked[0].reason
    assert "35.0" in blocked[0].reason


def test_absolute_vix_below_35_passes(gate, config):
    """VIX below absolute threshold should pass."""
    results = gate.check_absolute_vix(config, live_data=(28.0, 500.0))
    assert all(r.passed for r in results)
    assert any("28.0" in r.reason for r in results)


def test_absolute_vix_at_exactly_35_passes(gate, config):
    """VIX at exactly the threshold should pass (only > blocks)."""
    results = gate.check_absolute_vix(config, live_data=(35.0, 500.0))
    assert all(r.passed for r in results)


def test_absolute_vix_no_live_data(gate, config):
    """No live data should return empty results (can't check)."""
    results = gate.check_absolute_vix(config, live_data=None)
    assert results == []


# ---------- Earnings Proximity ----------


def test_earnings_same_day_blocks(gate, config):
    """Candidate with same-day earnings should be blocked."""
    from unittest.mock import patch
    from src.services.earnings_service import EarningsInfo
    from datetime import date

    mock_info = EarningsInfo(
        earnings_date=date.today(),
        days_to_earnings=0,
        earnings_in_dte=True,
    )

    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        staged_candidates=[{"symbol": "AAPL", "expiration": "2026-03-20"}],
    )

    with patch("src.services.earnings_service.get_cached_earnings", return_value=mock_info):
        results = gate.check_earnings_proximity(decision, context, config)

    blocked = [r for r in results if not r.passed and r.severity == "block"]
    assert len(blocked) == 1
    assert "AAPL" in blocked[0].reason
    assert "0 day" in blocked[0].reason


def test_earnings_1_day_out_passes(gate, config):
    """Candidate with earnings 1 day out passes when block_days=0."""
    from unittest.mock import patch
    from src.services.earnings_service import EarningsInfo
    from datetime import date, timedelta

    mock_info = EarningsInfo(
        earnings_date=date.today() + timedelta(days=1),
        days_to_earnings=1,
        earnings_in_dte=True,
    )

    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        staged_candidates=[{"symbol": "AAPL", "expiration": "2026-03-20"}],
    )

    with patch("src.services.earnings_service.get_cached_earnings", return_value=mock_info):
        results = gate.check_earnings_proximity(decision, context, config)

    # Should pass since days_to_earnings=1 > block_days=0
    assert all(r.passed for r in results)


def test_earnings_check_disabled(gate):
    """Disabled earnings check should return empty results."""
    config = GuardrailConfig(earnings_block_enabled=False)
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        staged_candidates=[{"symbol": "AAPL", "expiration": "2026-03-20"}],
    )

    results = gate.check_earnings_proximity(decision, context, config)
    assert results == []


def test_earnings_no_candidates_passes(gate, config):
    """No staged candidates should produce info-level pass."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(staged_candidates=[])

    results = gate.check_earnings_proximity(decision, context, config)
    assert all(r.passed for r in results)
    assert any("No staged" in r.reason for r in results)


# ---------- Validate fetches live data once ----------


def test_validate_fetches_live_data_once(gate, config):
    """validate() should call _fetch_live_data at most once."""
    decision = FakeDecision(action="EXECUTE_TRADES")
    context = FakeContext(
        market_context={"vix": 20.0, "spy_price": 500.0},
        staged_candidates=[{"symbol": "XSP", "strike": 480, "contracts": 1}],
    )

    mock_client = MagicMock()
    gate._fetch_live_data = MagicMock(return_value=(21.0, 502.0))

    gate.validate(decision, context, config, ibkr_client=mock_client)

    # _fetch_live_data should be called exactly once
    gate._fetch_live_data.assert_called_once_with(mock_client)
