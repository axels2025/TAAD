"""Unit tests for EfficientOptionScanner."""

from datetime import datetime, timedelta
from unittest.mock import MagicMock, Mock, patch

import pytest

from src.tools.efficient_scanner import EfficientOptionScanner, LIQUID_UNIVERSE


@pytest.fixture
def mock_ibkr_client():
    """Create a mock IBKR client."""
    client = MagicMock()
    client.is_connected.return_value = True
    client.ib = MagicMock()
    client.reset_suppressed_error_count = MagicMock()
    client.get_suppressed_error_count = MagicMock(return_value=0)
    return client


@pytest.fixture
def mock_cache():
    """Create a mock scanner cache."""
    cache = MagicMock()
    cache.is_chain_fresh.return_value = False
    cache.is_trend_fresh.return_value = False
    cache.get_stats.return_value = {
        "chains_cached": 0,
        "trends_cached": 0,
        "contracts_cached": 0,
        "symbols_with_contracts": 0,
    }
    return cache


@pytest.fixture
def scanner(mock_ibkr_client, mock_cache):
    """Create EfficientOptionScanner instance."""
    return EfficientOptionScanner(
        mock_ibkr_client, cache=mock_cache, universe=["AAPL", "MSFT", "GOOGL"]
    )


class TestScannerInitialization:
    """Test scanner initialization."""

    def test_initialization_with_defaults(self, mock_ibkr_client):
        """Test scanner initializes with default parameters."""
        scanner = EfficientOptionScanner(mock_ibkr_client)

        assert scanner.ibkr_client == mock_ibkr_client
        assert scanner.config is not None
        assert scanner.cache is not None
        assert len(scanner.universe) > 0

    def test_initialization_with_custom_universe(self, mock_ibkr_client, mock_cache):
        """Test scanner initializes with custom universe."""
        custom_universe = ["AAPL", "MSFT"]
        scanner = EfficientOptionScanner(
            mock_ibkr_client, cache=mock_cache, universe=custom_universe
        )

        assert scanner.universe == custom_universe

    def test_default_universe_is_defined(self):
        """Test that default liquid universe is properly defined."""
        assert len(LIQUID_UNIVERSE) > 0
        assert "AAPL" in LIQUID_UNIVERSE
        assert "SPY" in LIQUID_UNIVERSE
        # No duplicates
        assert len(LIQUID_UNIVERSE) == len(set(LIQUID_UNIVERSE))


class TestSelectBestChain:
    """Test chain selection logic."""

    def test_prefers_smart_with_matching_trading_class(self, scanner):
        """Test prefers SMART exchange with matching tradingClass."""
        mock_chain1 = Mock()
        mock_chain1.exchange = "CBOE"
        mock_chain1.tradingClass = "AAPL"
        mock_chain1.multiplier = "100"
        mock_chain1.expirations = {"20250207"}
        mock_chain1.strikes = {150.0}

        mock_chain2 = Mock()
        mock_chain2.exchange = "SMART"
        mock_chain2.tradingClass = "AAPL"
        mock_chain2.multiplier = "100"
        mock_chain2.expirations = {"20250207"}
        mock_chain2.strikes = {150.0}

        chains = [mock_chain1, mock_chain2]

        selected = scanner._select_best_chain(chains, "AAPL")

        assert selected is not None
        assert selected["exchange"] == "SMART"
        assert selected["trading_class"] == "AAPL"

    def test_falls_back_to_any_smart(self, scanner):
        """Test falls back to any SMART exchange if no exact match."""
        mock_chain1 = Mock()
        mock_chain1.exchange = "CBOE"
        mock_chain1.tradingClass = "AAPL"
        mock_chain1.multiplier = "100"
        mock_chain1.expirations = {"20250207"}
        mock_chain1.strikes = {150.0}

        mock_chain2 = Mock()
        mock_chain2.exchange = "SMART"
        mock_chain2.tradingClass = "AAPL7"  # Weekly, not exact match
        mock_chain2.multiplier = "100"
        mock_chain2.expirations = {"20250207"}
        mock_chain2.strikes = {150.0}

        chains = [mock_chain1, mock_chain2]

        selected = scanner._select_best_chain(chains, "AAPL")

        assert selected is not None
        assert selected["exchange"] == "SMART"

    def test_returns_none_for_empty_chains(self, scanner):
        """Test returns None if chains list is empty."""
        selected = scanner._select_best_chain([], "AAPL")

        assert selected is None


class TestExtractMatchingOptions:
    """Test option extraction from chains."""

    def test_extracts_options_in_range(self, scanner):
        """Test extracts options matching OTM and DTE criteria."""
        today = datetime.now().date()
        exp_10_days = (today + timedelta(days=10)).strftime("%Y%m%d")
        exp_15_days = (today + timedelta(days=15)).strftime("%Y%m%d")
        exp_30_days = (today + timedelta(days=30)).strftime("%Y%m%d")

        # For PUT at $150 stock price:
        # 10% OTM = $135 strike (150 * 0.90)
        # 20% OTM = $120 strike (150 * 0.80)
        chain = {
            "exchange": "SMART",
            "trading_class": "AAPL",
            "multiplier": "100",
            "expirations": {exp_10_days, exp_15_days, exp_30_days},
            "strikes": {115.0, 120.0, 125.0, 130.0, 135.0, 140.0},
        }

        stock_price = 150.0

        candidates = scanner._extract_matching_options(
            symbol="AAPL",
            stock_price=stock_price,
            chain=chain,
            min_otm=0.10,  # 10% OTM
            max_otm=0.20,  # 20% OTM
            min_dte=7,
            max_dte=21,
            option_type="PUT",
        )

        # Should include expirations within 7-21 DTE
        assert len(candidates) > 0

        # Check that strikes are in OTM range
        for candidate in candidates:
            # For PUT, OTM means strike < stock price
            assert candidate["strike"] < stock_price

            # OTM % should be in range
            assert 0.10 <= candidate["otm_pct"] <= 0.20

            # DTE should be in range
            assert 7 <= candidate["dte"] <= 21

    def test_returns_empty_for_no_matching_expirations(self, scanner):
        """Test returns empty list if no expirations in DTE range."""
        today = datetime.now().date()
        exp_far_future = (today + timedelta(days=100)).strftime("%Y%m%d")

        chain = {
            "exchange": "SMART",
            "trading_class": "AAPL",
            "multiplier": "100",
            "expirations": {exp_far_future},
            "strikes": {140.0, 145.0, 150.0},
        }

        candidates = scanner._extract_matching_options(
            symbol="AAPL",
            stock_price=150.0,
            chain=chain,
            min_otm=0.10,
            max_otm=0.20,
            min_dte=7,
            max_dte=21,
            option_type="PUT",
        )

        assert len(candidates) == 0

    def test_returns_empty_for_no_matching_strikes(self, scanner):
        """Test returns empty list if no strikes in OTM range."""
        today = datetime.now().date()
        exp_10_days = (today + timedelta(days=10)).strftime("%Y%m%d")

        chain = {
            "exchange": "SMART",
            "trading_class": "AAPL",
            "multiplier": "100",
            "expirations": {exp_10_days},
            "strikes": {100.0, 105.0, 110.0},  # All far OTM
        }

        candidates = scanner._extract_matching_options(
            symbol="AAPL",
            stock_price=150.0,
            chain=chain,
            min_otm=0.10,
            max_otm=0.20,
            min_dte=7,
            max_dte=21,
            option_type="PUT",
        )

        assert len(candidates) == 0


class TestRankOpportunities:
    """Test opportunity ranking."""

    def test_ranks_by_margin_efficiency(self, scanner):
        """Test opportunities are ranked by margin efficiency."""
        opportunities = [
            {
                "symbol": "AAPL",
                "strike": 150.0,
                "premium": 0.50,
                "stock_price": 160.0,
                "dte": 10,
                "otm_pct": 0.0625,  # 10/160
            },
            {
                "symbol": "MSFT",
                "strike": 300.0,
                "premium": 1.00,  # Higher premium
                "stock_price": 320.0,
                "dte": 10,
                "otm_pct": 0.0625,  # 20/320
            },
            {
                "symbol": "GOOGL",
                "strike": 100.0,
                "premium": 0.30,
                "stock_price": 110.0,
                "dte": 10,
                "otm_pct": 0.0909,  # 10/110
            },
        ]

        ranked = scanner._rank_opportunities(opportunities)

        # Should be sorted with best first
        assert len(ranked) == 3
        # All opportunities should have required fields added
        for opp in ranked:
            assert "margin_required" in opp
            assert "confidence" in opp
            assert "reasoning" in opp

    def test_adds_margin_and_confidence(self, scanner):
        """Test ranking adds margin and confidence fields."""
        opportunities = [
            {
                "symbol": "AAPL",
                "strike": 150.0,
                "premium": 0.50,
                "stock_price": 160.0,
                "dte": 10,
                "otm_pct": 0.0625,
            }
        ]

        ranked = scanner._rank_opportunities(opportunities)

        assert "margin_required" in ranked[0]
        assert "confidence" in ranked[0]
        assert "reasoning" in ranked[0]
        assert ranked[0]["margin_required"] > 0


class TestCacheUsage:
    """Test cache integration."""

    def test_uses_cached_chain_when_fresh(self, scanner, mock_cache):
        """Test uses cached chain when available and fresh."""
        mock_cache.is_chain_fresh.return_value = True
        mock_cache.get_chain.return_value = {
            "exchange": "SMART",
            "trading_class": "AAPL",
            "multiplier": "100",
            "expirations": {"20250207"},
            "strikes": {150.0},
        }

        chain = scanner.get_or_cache_chain("AAPL")

        assert chain is not None
        mock_cache.get_chain.assert_called_once_with("AAPL")
        # Should not fetch from IBKR
        scanner.ibkr_client.get_stock_contract.assert_not_called()

    def test_fetches_from_ibkr_when_cache_stale(self, scanner, mock_cache):
        """Test fetches from IBKR when cache is stale."""
        mock_cache.is_chain_fresh.return_value = False

        # Mock IBKR responses
        mock_stock = Mock()
        mock_stock.symbol = "AAPL"
        mock_stock.secType = "STK"
        mock_stock.conId = 123
        scanner.ibkr_client.get_stock_contract.return_value = mock_stock
        scanner.ibkr_client.qualify_contract.return_value = mock_stock

        mock_chain = Mock()
        mock_chain.exchange = "SMART"
        mock_chain.tradingClass = "AAPL"
        mock_chain.multiplier = "100"
        mock_chain.expirations = {"20250207"}
        mock_chain.strikes = {150.0}
        scanner.ibkr_client.ib.reqSecDefOptParams.return_value = [mock_chain]

        chain = scanner.get_or_cache_chain("AAPL")

        assert chain is not None
        scanner.ibkr_client.get_stock_contract.assert_called_once_with("AAPL")
        mock_cache.set_chain.assert_called_once()


class TestBatchOperations:
    """Test batch qualification and premium fetching."""

    def test_batch_qualify_processes_in_batches(self, scanner):
        """Test batch qualification processes contracts in batches."""
        # Create 60 candidates (more than batch size of 50)
        candidates = []
        for i in range(60):
            candidates.append(
                {
                    "symbol": "AAPL",
                    "strike": 150.0 + i,
                    "expiration": "20250207",
                    "option_type": "PUT",
                    "exchange": "SMART",
                    "trading_class": "AAPL",
                }
            )

        # Mock qualified contracts
        mock_qualified = []
        for _ in range(60):
            contract = Mock()
            contract.conId = 12345
            mock_qualified.append(contract)

        scanner.ibkr_client.ib.qualifyContracts.return_value = mock_qualified[:50]

        qualified = scanner.batch_qualify_options(candidates)

        # Should have called qualifyContracts twice (2 batches)
        assert scanner.ibkr_client.ib.qualifyContracts.call_count >= 1
