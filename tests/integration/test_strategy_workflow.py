"""Integration tests for complete strategy workflow."""

from unittest.mock import MagicMock, patch

import pytest

from src.config.baseline_strategy import BaselineStrategy
from src.strategies.naked_put import NakedPutStrategy
from src.strategies.validator import StrategyValidator


@pytest.fixture
def mock_ibkr_client():
    """Create a mock IBKR client with realistic responses."""
    client = MagicMock()
    client.is_connected.return_value = True
    client.ib = MagicMock()
    return client


@pytest.fixture
def strategy(mock_ibkr_client):
    """Create strategy with mocked dependencies."""
    return NakedPutStrategy(mock_ibkr_client)


@pytest.mark.integration
class TestStrategyWorkflow:
    """Integration tests for full strategy workflow."""

    def test_strategy_initialization_workflow(self, mock_ibkr_client):
        """Test complete strategy initialization."""
        # Create strategy
        config = BaselineStrategy()
        strategy = NakedPutStrategy(mock_ibkr_client, config)

        # Verify all components initialized
        assert strategy.ibkr_client is not None
        assert strategy.config is not None
        assert strategy.screener is not None
        assert strategy.options_finder is not None

        # Verify configuration is valid
        assert strategy.validate_configuration() is True

    def test_entry_and_exit_workflow(self, strategy):
        """Test complete entry and exit decision workflow."""
        from datetime import datetime, timedelta
        from src.strategies.base import TradeOpportunity

        # Create opportunity
        opportunity = TradeOpportunity(
            symbol="AAPL",
            strike=150.0,
            expiration=datetime.now() + timedelta(days=10),
            option_type="PUT",
            premium=0.40,
            contracts=5,
            otm_pct=0.18,
            dte=10,
            stock_price=180.0,
            trend="uptrend",
            confidence=0.8,
        )

        # Test entry decision
        should_enter = strategy.should_enter_trade(opportunity)
        assert should_enter is True

        # Simulate trade progression
        entry_premium = 0.40
        entry_date = datetime.now()

        # Test holding scenario
        signal_holding = strategy.should_exit_trade(
            entry_premium=entry_premium,
            current_premium=0.35,
            current_dte=8,
            entry_date=entry_date,
        )
        assert signal_holding.should_exit is False

        # Test profit target scenario
        signal_profit = strategy.should_exit_trade(
            entry_premium=entry_premium,
            current_premium=0.20,
            current_dte=8,
            entry_date=entry_date,
        )
        assert signal_profit.should_exit is True
        assert signal_profit.reason == "profit_target"

    @patch("src.tools.screener.StockScreener.scan_stocks")
    @patch("src.tools.options_finder.OptionsFinder.find_put_options")
    def test_find_opportunities_workflow(
        self, mock_find_options, mock_scan_stocks, strategy
    ):
        """Test complete opportunity finding workflow."""
        from datetime import datetime

        # Mock screener to return candidate stocks
        mock_scan_stocks.return_value = [
            {
                "symbol": "AAPL",
                "price": 180.0,
                "volume": 2_000_000,
                "trend": "uptrend",
                "trend_score": 0.8,
                "ema_20": 175.0,
                "ema_50": 170.0,
                "sector": "Technology",
            },
            {
                "symbol": "MSFT",
                "price": 380.0,
                "volume": 1_500_000,
                "trend": "uptrend",
                "trend_score": 0.75,
                "ema_20": 370.0,
                "ema_50": 360.0,
                "sector": "Technology",
            },
        ]

        # Mock options finder to return options
        mock_find_options.return_value = [
            {
                "symbol": "AAPL",
                "strike": 150.0,
                "expiration": datetime(2025, 2, 1),
                "option_type": "PUT",
                "premium": 0.40,
                "bid": 0.38,
                "ask": 0.42,
                "dte": 10,
                "otm_pct": 0.1667,
                "margin_required": 1500.0,
            },
        ]

        # Find opportunities
        opportunities = strategy.find_opportunities(max_results=5)

        # Verify workflow
        assert mock_scan_stocks.called
        assert mock_find_options.called
        assert len(opportunities) > 0

        # Verify opportunity structure
        opp = opportunities[0]
        assert opp.symbol == "AAPL"
        assert opp.premium == 0.40
        assert opp.trend == "uptrend"


@pytest.mark.integration
class TestStrategyValidation:
    """Integration tests for strategy validation."""

    def test_complete_validation_workflow(self, mock_ibkr_client):
        """Test complete validation workflow."""
        # Create strategy
        strategy = NakedPutStrategy(mock_ibkr_client)

        # Create validator
        validator = StrategyValidator(strategy, mock_ibkr_client)

        # Run validation (without live IBKR connection)
        # This tests the validation logic, not actual IBKR interaction
        report = validator._validate_entry_criteria()

        # Verify validation results
        assert "total_tests" in report
        assert "passed" in report
        assert "failed" in report
        assert "pass_rate" in report

        # Should have high pass rate for valid strategy
        assert report["pass_rate"] >= 0.8

    def test_exit_criteria_validation_workflow(self, mock_ibkr_client):
        """Test exit criteria validation workflow."""
        strategy = NakedPutStrategy(mock_ibkr_client)
        validator = StrategyValidator(strategy, mock_ibkr_client)

        # Run exit validation
        report = validator._validate_exit_criteria()

        # Verify all exit rules work
        assert report["all_rules_working"] is True
        assert report["passed"] == report["total_tests"]
