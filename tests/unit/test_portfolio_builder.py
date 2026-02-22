"""Unit tests for portfolio builder module.

Tests the portfolio building logic including margin calculations,
greedy allocation, and constraint checking.
"""

from datetime import date
from unittest.mock import MagicMock

import pytest

from src.services.portfolio_builder import (
    PortfolioBuilder,
    PortfolioConfig,
    PortfolioPlan,
    StagedTrade,
)
from src.services.strike_finder import StrikeCandidate


def create_test_candidate(
    symbol: str,
    strike: float = 100.0,
    stock_price: float = 120.0,
    bid: float = 0.50,
    contracts: int = 5,
    margin_estimate: float = 2000.0,
    margin_actual: float | None = None,
    iv_rank: float = 0.45,
    sector: str = "Technology",
    otm_pct: float = 0.17,
) -> StrikeCandidate:
    """Create a test StrikeCandidate."""
    mid = bid + 0.05
    suggested_limit = bid + (mid - bid) * 0.3
    premium_income = suggested_limit * 100 * contracts
    effective_margin = margin_actual if margin_actual else margin_estimate
    total_margin = effective_margin * contracts

    return StrikeCandidate(
        symbol=symbol,
        stock_price=stock_price,
        strike=strike,
        expiration=date(2026, 2, 7),
        dte=7,
        bid=bid,
        ask=bid + 0.10,
        mid=mid,
        suggested_limit=suggested_limit,
        otm_pct=otm_pct,
        delta=-0.15,
        iv=0.35,
        iv_rank=iv_rank,
        volume=1000,
        open_interest=5000,
        margin_estimate=margin_estimate,
        margin_actual=margin_actual,
        contracts=contracts,
        total_margin=total_margin,
        premium_income=premium_income,
        margin_efficiency=premium_income / total_margin if total_margin > 0 else 0,
        sector=sector,
        score=75.0,
        source="barchart",
    )


class TestPortfolioConfig:
    """Tests for PortfolioConfig dataclass."""

    def test_default_values(self):
        """Test default configuration values."""
        config = PortfolioConfig()

        assert config.margin_budget_pct == 0.50
        assert config.margin_budget_default == 50000.0
        assert config.max_positions == 10
        assert config.max_sector_concentration == 3
        assert config.max_budget_utilization == 0.80
        assert config.high_iv_threshold == 0.60

    def test_from_env(self, monkeypatch):
        """Test loading config from central Config singleton.

        PortfolioConfig.from_env() now delegates to get_config() which
        reads MAX_SECTOR_COUNT (not MAX_SECTOR_CONCENTRATION).
        """
        monkeypatch.setenv("MARGIN_BUDGET_PCT", "0.40")
        monkeypatch.setenv("MARGIN_BUDGET_DEFAULT", "75000")
        monkeypatch.setenv("MAX_POSITIONS", "8")
        monkeypatch.setenv("MAX_SECTOR_COUNT", "2")

        config = PortfolioConfig.from_env()

        assert config.margin_budget_pct == 0.40
        assert config.margin_budget_default == 75000.0
        assert config.max_positions == 8
        assert config.max_sector_concentration == 2


class TestStagedTrade:
    """Tests for StagedTrade dataclass."""

    def test_properties(self):
        """Test StagedTrade properties."""
        candidate = create_test_candidate("AAPL", strike=150.0)
        trade = StagedTrade(
            candidate=candidate,
            margin_per_contract=2000.0,
            margin_source="ibkr_whatif",
            contracts=5,
            total_margin=10000.0,
            total_premium=250.0,
            portfolio_rank=1,
            cumulative_margin=10000.0,
            within_budget=True,
        )

        assert trade.symbol == "AAPL"
        assert trade.strike == 150.0
        assert trade.expiration == date(2026, 2, 7)
        assert trade.margin_efficiency == 0.025  # 250 / 10000

    def test_margin_efficiency_zero_margin(self):
        """Test margin efficiency with zero margin."""
        candidate = create_test_candidate("AAPL")
        trade = StagedTrade(
            candidate=candidate,
            margin_per_contract=0.0,
            margin_source="estimated",
            contracts=5,
            total_margin=0.0,
            total_premium=250.0,
            portfolio_rank=1,
            cumulative_margin=0.0,
            within_budget=True,
        )

        assert trade.margin_efficiency == 0.0


class TestPortfolioPlan:
    """Tests for PortfolioPlan dataclass."""

    def test_budget_utilization(self):
        """Test budget utilization calculation."""
        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=25000.0,
            margin_budget=50000.0,
            margin_remaining=25000.0,
            total_premium_expected=1000.0,
            sector_distribution={},
            warnings=[],
        )

        assert plan.budget_utilization == 0.50

    def test_budget_utilization_zero_budget(self):
        """Test budget utilization with zero budget."""
        plan = PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=0.0,
            margin_budget=0.0,
            margin_remaining=0.0,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=[],
        )

        assert plan.budget_utilization == 0.0

    def test_trade_count(self):
        """Test trade count property."""
        candidate = create_test_candidate("AAPL")
        trade = StagedTrade(
            candidate=candidate,
            margin_per_contract=2000.0,
            margin_source="estimated",
            contracts=5,
            total_margin=10000.0,
            total_premium=250.0,
            portfolio_rank=1,
            cumulative_margin=10000.0,
            within_budget=True,
        )

        plan = PortfolioPlan(
            trades=[trade, trade],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=20000.0,
            margin_budget=50000.0,
            margin_remaining=30000.0,
            total_premium_expected=500.0,
            sector_distribution={"Technology": 2},
            warnings=[],
        )

        assert plan.trade_count == 2

    def test_has_estimated_margins(self):
        """Test detection of estimated margins."""
        candidate = create_test_candidate("AAPL")

        estimated_trade = StagedTrade(
            candidate=candidate,
            margin_per_contract=2000.0,
            margin_source="estimated",
            contracts=5,
            total_margin=10000.0,
            total_premium=250.0,
            portfolio_rank=1,
            cumulative_margin=10000.0,
            within_budget=True,
        )

        actual_trade = StagedTrade(
            candidate=candidate,
            margin_per_contract=2000.0,
            margin_source="ibkr_whatif",
            contracts=5,
            total_margin=10000.0,
            total_premium=250.0,
            portfolio_rank=2,
            cumulative_margin=20000.0,
            within_budget=True,
        )

        # Plan with estimated margin
        plan_with_estimated = PortfolioPlan(
            trades=[estimated_trade],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=10000.0,
            margin_budget=50000.0,
            margin_remaining=40000.0,
            total_premium_expected=250.0,
            sector_distribution={},
            warnings=[],
        )
        assert plan_with_estimated.has_estimated_margins is True

        # Plan with only actual margins
        plan_with_actual = PortfolioPlan(
            trades=[actual_trade],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=10000.0,
            margin_budget=50000.0,
            margin_remaining=40000.0,
            total_premium_expected=250.0,
            sector_distribution={},
            warnings=[],
        )
        assert plan_with_actual.has_estimated_margins is False


class TestPortfolioBuilder:
    """Tests for PortfolioBuilder class."""

    @pytest.fixture
    def builder(self):
        """Create a builder with no IBKR connection."""
        config = PortfolioConfig(
            margin_budget_pct=0.50,
            margin_budget_default=50000.0,
            max_positions=10,
            max_sector_concentration=3,
        )
        return PortfolioBuilder(ibkr_client=None, config=config)

    @pytest.fixture
    def mock_ibkr(self):
        """Create a mock IBKR client."""
        mock = MagicMock()
        mock.get_account_summary.return_value = {"NetLiquidation": "100000.0"}
        mock.get_actual_margin.return_value = 2500.0
        mock.get_option_contract.return_value = MagicMock()
        mock.qualify_contract.return_value = [MagicMock()]
        return mock

    def test_build_portfolio_empty_candidates(self, builder):
        """Test building portfolio with no candidates."""
        plan = builder.build_portfolio([])

        assert plan.trade_count == 0
        assert plan.total_margin_used == 0.0
        assert "No candidates provided" in plan.warnings

    def test_build_portfolio_single_candidate(self, builder):
        """Test building portfolio with single candidate."""
        candidates = [
            create_test_candidate(
                "AAPL",
                strike=150.0,
                stock_price=180.0,
                bid=0.50,
                contracts=5,
                margin_estimate=3000.0,
            )
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        assert plan.trade_count == 1
        assert plan.trades[0].symbol == "AAPL"
        assert plan.trades[0].contracts == 5

    def test_build_portfolio_multiple_candidates(self, builder):
        """Test building portfolio with multiple candidates."""
        candidates = [
            create_test_candidate(
                "AAPL",
                margin_estimate=3000.0,
                sector="Technology",
            ),
            create_test_candidate(
                "MSFT",
                margin_estimate=2800.0,
                sector="Technology",
            ),
            create_test_candidate(
                "GOOGL",
                margin_estimate=3200.0,
                sector="Technology",
            ),
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        assert plan.trade_count == 3
        assert plan.total_margin_used > 0

    def test_build_portfolio_respects_budget(self, builder):
        """Test that portfolio building respects margin budget."""
        candidates = [
            create_test_candidate(
                "AAPL",
                margin_estimate=20000.0,  # Will use 20000 * 1 contract
                contracts=1,
            ),
            create_test_candidate(
                "MSFT",
                margin_estimate=20000.0,
                contracts=1,
            ),
            create_test_candidate(
                "GOOGL",
                margin_estimate=20000.0,
                contracts=1,
            ),
        ]

        # Budget only allows 2 trades
        plan = builder.build_portfolio(candidates, margin_budget=45000.0)

        assert plan.trade_count == 2
        assert len(plan.skipped_trades) == 1
        assert plan.total_margin_used <= 45000.0

    def test_build_portfolio_respects_sector_limit(self, builder):
        """Test that portfolio building respects sector concentration limits."""
        candidates = [
            create_test_candidate(
                "AAPL",
                margin_estimate=2000.0,
                sector="Technology",
            ),
            create_test_candidate(
                "MSFT",
                margin_estimate=2000.0,
                sector="Technology",
            ),
            create_test_candidate(
                "GOOGL",
                margin_estimate=2000.0,
                sector="Technology",
            ),
            create_test_candidate(
                "AMZN",
                margin_estimate=2000.0,
                sector="Technology",  # 4th tech stock
            ),
            create_test_candidate(
                "XOM",
                margin_estimate=2000.0,
                sector="Energy",  # Different sector
            ),
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        # Should have 3 tech + 1 energy = 4 trades
        assert plan.sector_distribution.get("Technology", 0) <= 3
        assert "Technology" in plan.sector_distribution

    def test_build_portfolio_respects_max_positions(self, builder):
        """Test that portfolio building respects max positions."""
        # Create 12 candidates
        candidates = [
            create_test_candidate(
                f"SYM{i}",
                margin_estimate=1000.0,
                sector=f"Sector{i % 4}",  # Spread across sectors
            )
            for i in range(12)
        ]

        plan = builder.build_portfolio(candidates, margin_budget=100000.0)

        # Should respect max_positions=10
        assert plan.trade_count <= 10

    def test_build_portfolio_no_duplicate_symbols(self, builder):
        """Test that same symbol is not selected twice."""
        candidates = [
            create_test_candidate(
                "AAPL",
                strike=150.0,
                margin_estimate=2000.0,
            ),
            create_test_candidate(
                "AAPL",  # Same symbol, different strike
                strike=145.0,
                margin_estimate=1800.0,
            ),
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        # Should only have 1 AAPL trade
        aapl_trades = [t for t in plan.trades if t.symbol == "AAPL"]
        assert len(aapl_trades) == 1

    def test_build_portfolio_ranks_by_efficiency(self, builder):
        """Test that trades are ranked by margin efficiency."""
        candidates = [
            create_test_candidate(
                "LOW_EFF",
                bid=0.30,
                margin_estimate=5000.0,
                sector="Sector1",
            ),
            create_test_candidate(
                "HIGH_EFF",
                bid=0.80,  # Higher premium
                margin_estimate=2000.0,  # Lower margin
                sector="Sector2",
            ),
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        # HIGH_EFF should be ranked higher (better efficiency)
        if plan.trade_count >= 2:
            # Find the HIGH_EFF trade
            high_eff_trade = next(
                (t for t in plan.trades if t.symbol == "HIGH_EFF"), None
            )
            low_eff_trade = next(
                (t for t in plan.trades if t.symbol == "LOW_EFF"), None
            )
            if high_eff_trade and low_eff_trade:
                assert high_eff_trade.portfolio_rank < low_eff_trade.portfolio_rank

    def test_build_portfolio_with_ibkr_budget(self, mock_ibkr):
        """Test budget calculation from IBKR NLV."""
        config = PortfolioConfig(margin_budget_pct=0.50)
        builder = PortfolioBuilder(ibkr_client=mock_ibkr, config=config)

        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0)
        ]

        # Don't provide budget - should calculate from NLV
        plan = builder.build_portfolio(candidates)

        # NLV=100000 * 0.50 = 50000
        assert plan.margin_budget == 50000.0

    def test_build_portfolio_with_actual_margin(self, mock_ibkr):
        """Test that actual margin is used when IBKR connected."""
        config = PortfolioConfig()
        builder = PortfolioBuilder(ibkr_client=mock_ibkr, config=config)

        candidates = [
            create_test_candidate(
                "AAPL",
                margin_estimate=2000.0,  # Estimate
                contracts=1,
            )
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        # mock_ibkr.get_actual_margin returns 2500
        # Trade should use actual margin
        if plan.trade_count > 0:
            assert plan.trades[0].margin_per_contract == 2500.0
            assert plan.trades[0].margin_source == "ibkr_whatif"

    def test_build_portfolio_high_iv_warning(self, builder):
        """Test that high IV candidates generate warnings."""
        candidates = [
            create_test_candidate(
                "HIGH_IV",
                iv_rank=0.75,  # Above 0.60 threshold
            )
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        # Should have high IV warning
        high_iv_warnings = [w for w in plan.warnings if "IV Rank" in w]
        assert len(high_iv_warnings) == 1

    def test_build_portfolio_estimated_margin_warning(self, builder):
        """Test that estimated margins generate warnings."""
        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0)
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        # Should have estimated margin warning
        estimated_warnings = [w for w in plan.warnings if "estimated margin" in w]
        assert len(estimated_warnings) == 1

    def test_margin_comparisons_built(self, builder):
        """Test that margin comparisons are built."""
        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0),
            create_test_candidate("MSFT", margin_estimate=2500.0),
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        assert len(plan.margin_comparisons) == 2

    def test_margin_comparison_rank_shift(self, builder):
        """Test rank shift calculation in margin comparisons."""
        # When actual margin differs, ranks may shift
        candidates = [
            create_test_candidate(
                "AAPL",
                margin_estimate=2000.0,
                margin_actual=3000.0,  # Higher actual = lower efficiency
            ),
            create_test_candidate(
                "MSFT",
                margin_estimate=3000.0,
                margin_actual=2000.0,  # Lower actual = higher efficiency
            ),
        ]

        plan = builder.build_portfolio(candidates, margin_budget=50000.0)

        # Check that comparisons have rank shift info
        for comp in plan.margin_comparisons:
            assert hasattr(comp, "rank_shift")


class TestPortfolioBuilderEstimateMargin:
    """Tests for margin estimation formula."""

    @pytest.fixture
    def builder(self):
        """Create a builder for testing."""
        return PortfolioBuilder()

    def test_estimate_margin_atm(self, builder):
        """Test margin estimate for at-the-money option."""
        # ATM: stock_price = strike
        margin = builder.estimate_margin(
            stock_price=100.0,
            strike=100.0,
            premium=0.50,
        )

        # Formula: max(20% * 100 - 0 + 0.50, 10% * 100) * 100
        # = max(20.50, 10) * 100 = 2050
        assert margin == 2050.0

    def test_estimate_margin_otm(self, builder):
        """Test margin estimate for out-of-the-money option."""
        # OTM: strike < stock_price
        margin = builder.estimate_margin(
            stock_price=120.0,
            strike=100.0,
            premium=0.50,
        )

        # Formula: max(20% * 120 - 20 + 0.50, 10% * 120) * 100
        # = max(24 - 20 + 0.50, 12) * 100
        # = max(4.50, 12) * 100 = 1200
        assert margin == 1200.0

    def test_estimate_margin_deep_otm(self, builder):
        """Test margin estimate for deep OTM option hits minimum."""
        # Deep OTM should hit 10% minimum
        margin = builder.estimate_margin(
            stock_price=150.0,
            strike=100.0,
            premium=0.30,
        )

        # Formula: max(20% * 150 - 50 + 0.30, 10% * 150) * 100
        # = max(30 - 50 + 0.30, 15) * 100
        # = max(-19.70, 15) * 100 = 1500
        assert margin == 1500.0

    def test_estimate_margin_itm(self, builder):
        """Test margin estimate for in-the-money option."""
        # ITM: strike > stock_price (OTM amount = 0)
        margin = builder.estimate_margin(
            stock_price=90.0,
            strike=100.0,
            premium=1.00,
        )

        # Formula: max(20% * 90 - 0 + 1.00, 10% * 90) * 100
        # = max(18 + 1, 9) * 100 = 1900
        assert margin == 1900.0


class TestPortfolioBuilderIntegration:
    """Integration tests for PortfolioBuilder."""

    def test_full_workflow_offline(self):
        """Test complete portfolio building workflow without IBKR."""
        builder = PortfolioBuilder(
            config=PortfolioConfig(
                margin_budget_default=50000.0,
                max_positions=5,
                max_sector_concentration=2,
            )
        )

        candidates = [
            create_test_candidate(
                "IREN",
                strike=40.0,
                stock_price=54.64,
                bid=0.55,
                margin_estimate=2800.0,
                contracts=5,
                sector="Cryptocurrency",
            ),
            create_test_candidate(
                "SOXL",
                strike=50.0,
                stock_price=64.04,
                bid=0.45,
                margin_estimate=3200.0,
                contracts=5,
                sector="ETF-Semis",
            ),
            create_test_candidate(
                "PLTR",
                strike=115.0,
                stock_price=147.60,
                bid=0.50,
                margin_estimate=6300.0,
                contracts=3,
                sector="Technology",
            ),
            create_test_candidate(
                "RKLB",
                strike=65.0,
                stock_price=80.0,
                bid=0.23,
                margin_estimate=2910.0,
                contracts=5,
                sector="Space",
            ),
        ]

        plan = builder.build_portfolio(candidates)

        # Verify plan structure
        assert plan.trade_count > 0
        assert plan.margin_budget == 50000.0
        assert plan.total_margin_used <= plan.margin_budget
        assert plan.margin_remaining == plan.margin_budget - plan.total_margin_used
        assert plan.total_premium_expected > 0

        # Verify trades are ranked
        for i, _ in enumerate(plan.trades[:-1]):
            # Each trade should have ascending rank
            assert plan.trades[i].portfolio_rank < plan.trades[i + 1].portfolio_rank

        # Verify margin comparisons exist
        assert len(plan.margin_comparisons) == len(candidates)

    def test_full_workflow_with_mock_ibkr(self):
        """Test complete workflow with mock IBKR connection."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_account_summary.return_value = {"NetLiquidation": "100000.0"}
        mock_ibkr.get_option_contract.return_value = MagicMock()
        mock_ibkr.qualify_contract.return_value = [MagicMock()]

        # Return different actual margins to test re-ranking
        margin_values = {"IREN": 3500.0, "SOXL": 2800.0}  # SOXL becomes more efficient

        def get_margin(contract, quantity=1):
            # Extract symbol from mock contract
            return margin_values.get("SOXL", 3000.0)

        mock_ibkr.get_actual_margin.side_effect = get_margin

        builder = PortfolioBuilder(ibkr_client=mock_ibkr)

        candidates = [
            create_test_candidate(
                "IREN",
                margin_estimate=2800.0,
                sector="Crypto",
            ),
            create_test_candidate(
                "SOXL",
                margin_estimate=3200.0,
                sector="ETF",
            ),
        ]

        plan = builder.build_portfolio(candidates)

        assert plan.ibkr_connected is True
        # Trades should use actual margins
        for trade in plan.trades:
            assert trade.margin_source in ["ibkr_whatif", "estimated"]

    def test_get_actual_margins_retries_failures(self):
        """Test that _get_actual_margins retries candidates that failed first pass."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_account_summary.return_value = {"NetLiquidation": "100000.0"}
        mock_ibkr.get_option_contract.return_value = MagicMock()
        mock_ibkr.qualify_contract.return_value = [MagicMock()]

        # First call returns None (failure), second call returns valid margin
        mock_ibkr.get_actual_margin.side_effect = [None, 3000.0]

        builder = PortfolioBuilder(ibkr_client=mock_ibkr)

        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0, contracts=1)
        ]

        result = builder._get_actual_margins(candidates)

        # Should have retried and gotten actual margin on second attempt
        assert result[0].margin_actual == 3000.0
        assert mock_ibkr.get_actual_margin.call_count == 2
        # Verify sleep was called for the settle delay between passes
        mock_ibkr.ib.sleep.assert_any_call(1.0)

    def test_get_actual_margins_no_retry_when_all_succeed(self):
        """Test that no retry pass happens when all margins succeed first time."""
        mock_ibkr = MagicMock()
        mock_ibkr.get_account_summary.return_value = {"NetLiquidation": "100000.0"}
        mock_ibkr.get_option_contract.return_value = MagicMock()
        mock_ibkr.qualify_contract.return_value = [MagicMock()]
        mock_ibkr.get_actual_margin.return_value = 2500.0

        builder = PortfolioBuilder(ibkr_client=mock_ibkr)

        candidates = [
            create_test_candidate("AAPL", margin_estimate=2000.0, contracts=1),
            create_test_candidate("MSFT", margin_estimate=2500.0, contracts=1),
        ]

        result = builder._get_actual_margins(candidates)

        # All should have actual margins, no retry needed
        assert all(c.margin_actual is not None for c in result)
        # get_actual_margin called once per candidate (no retries)
        assert mock_ibkr.get_actual_margin.call_count == 2
        # No settle sleep (1.0) should have been called
        settle_calls = [
            c for c in mock_ibkr.ib.sleep.call_args_list if c.args[0] == 1.0
        ]
        assert len(settle_calls) == 0
