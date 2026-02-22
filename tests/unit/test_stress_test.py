"""Tests for PortfolioStressTest — stress testing scenarios."""

from datetime import datetime, timedelta

import pytest

from src.analysis.stress_test import PortfolioStressTest, StressTestResult
from src.execution.position_monitor import PositionStatus


def make_position(
    symbol="AAPL",
    strike=170.0,
    contracts=5,
    current_pnl=250.0,
    delta=-0.20,
    underlying_price=195.0,
) -> PositionStatus:
    """Helper to create a test PositionStatus."""
    exp_date = (datetime.now() + timedelta(days=15)).strftime("%Y%m%d")
    return PositionStatus(
        position_id=f"{symbol}_{strike}_{exp_date}_P",
        symbol=symbol,
        strike=strike,
        option_type="P",
        expiration_date=exp_date,
        contracts=contracts,
        entry_premium=0.50,
        current_premium=0.25,
        current_pnl=current_pnl,
        current_pnl_pct=0.50,
        days_held=10,
        dte=15,
        delta=delta,
        underlying_price=underlying_price,
    )


@pytest.fixture
def positions():
    """Create a portfolio of test positions."""
    return [
        make_position("AAPL", 170.0, 5, 250.0, -0.20, 195.0),
        make_position("MSFT", 350.0, 3, 150.0, -0.15, 410.0),
        make_position("AMZN", 180.0, 4, 200.0, -0.25, 210.0),
    ]


@pytest.fixture
def tester():
    """Create stress tester with $100K equity."""
    return PortfolioStressTest(account_equity=100_000)


class TestStressScenarios:
    """Test stress scenario calculations."""

    def test_market_drop_5pct(self, tester, positions):
        """5% market drop produces negative P&L change."""
        result = tester.run_scenario("market_drop_5pct", positions)
        assert result.total_pnl_change < 0
        assert result.scenario_name == "market_drop_5pct"
        assert len(result.position_impacts) == 3

    def test_market_drop_10pct(self, tester, positions):
        """10% drop is worse than 5% drop."""
        result_5 = tester.run_scenario("market_drop_5pct", positions)
        result_10 = tester.run_scenario("market_drop_10pct", positions)
        assert result_10.total_pnl_change < result_5.total_pnl_change

    def test_market_drop_20pct_severe(self, tester, positions):
        """20% crash has worst P&L impact."""
        result = tester.run_scenario("market_drop_20pct", positions)
        assert result.total_pnl_change < -1000  # Significant loss

    def test_vix_spike_margin_expansion(self, tester, positions):
        """VIX spike increases margin requirements."""
        result_normal = tester.run_scenario("market_drop_5pct", positions)
        result_vix = tester.run_scenario("vix_spike_35", positions)
        # VIX scenario has higher margin multiplier (1.5 vs 1.1)
        assert result_vix.total_margin_estimate > result_normal.total_margin_estimate

    def test_empty_portfolio(self, tester):
        """Empty portfolio returns empty result."""
        results = tester.run_all_scenarios([])
        assert results == {}

    def test_run_all_scenarios(self, tester, positions):
        """run_all_scenarios returns results for all 5 scenarios."""
        results = tester.run_all_scenarios(positions)
        assert len(results) == 5
        assert "market_drop_5pct" in results
        assert "market_drop_10pct" in results
        assert "market_drop_20pct" in results
        assert "vix_spike_35" in results
        assert "correlation_crisis" in results

    def test_unknown_scenario_raises(self, tester, positions):
        """Unknown scenario name raises ValueError."""
        with pytest.raises(ValueError, match="Unknown scenario"):
            tester.run_scenario("fake_scenario", positions)

    def test_margin_call_risk_detected(self):
        """Large portfolio with crash triggers margin call risk."""
        # Create a position that will blow up margin
        big_positions = [
            make_position("AAPL", 170.0, 20, 500.0, -0.30, 195.0),
            make_position("MSFT", 350.0, 15, 400.0, -0.25, 410.0),
        ]
        # Small account
        tester = PortfolioStressTest(account_equity=50_000)
        result = tester.run_scenario("market_drop_20pct", big_positions)
        assert result.margin_call_risk is True

    def test_worst_position_identified(self, tester, positions):
        """Worst position is correctly identified."""
        result = tester.run_scenario("market_drop_10pct", positions)
        assert result.worst_position in ("AAPL", "MSFT", "AMZN")
        assert result.worst_pnl_change < 0

    def test_position_impact_fields(self, tester, positions):
        """Position impacts have all required fields."""
        result = tester.run_scenario("market_drop_5pct", positions)
        for impact in result.position_impacts:
            assert impact.symbol in ("AAPL", "MSFT", "AMZN")
            assert impact.contracts > 0
            assert impact.margin_estimate > 0
            assert impact.new_underlying > 0
            assert impact.pnl_change < 0  # All positions lose in a drop


class TestSingleStockCrash:
    """Test single-stock crash scenarios."""

    def test_single_stock_crash(self, tester, positions):
        """Single stock crash affects only the target symbol."""
        results = tester.run_single_stock_crash(positions)
        assert len(results) == 3  # One per symbol

        # AAPL crash should mainly affect AAPL position
        aapl_result = results["AAPL"]
        assert aapl_result.worst_position == "AAPL"

        # Other positions should have 0 P&L change in AAPL crash
        for impact in aapl_result.position_impacts:
            if impact.symbol != "AAPL":
                assert impact.pnl_change == 0.0

    def test_no_positions_returns_empty(self, tester):
        """No positions → empty results."""
        results = tester.run_single_stock_crash([])
        assert results == {}
