"""Unit tests for LiveStrikeSelector.

Tests delta-based strike selection logic with mocked IBKR client.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from src.services.live_strike_selector import (
    LiveStrikeSelector,
    StrikeSelectionConfig,
    StrikeSelectionResult,
)
from src.services.premarket_validator import StagedOpportunity
from src.services.limit_price_calculator import LimitPriceCalculator


def make_opp(
    symbol: str = "AAPL",
    strike: float = 200.0,
    staged_stock_price: float = 230.0,
    staged_limit_price: float = 0.50,
    otm_pct: float = 0.13,
) -> StagedOpportunity:
    """Create a test StagedOpportunity."""
    return StagedOpportunity(
        id=1,
        symbol=symbol,
        strike=strike,
        expiration="2026-02-20",
        staged_stock_price=staged_stock_price,
        staged_limit_price=staged_limit_price,
        staged_contracts=5,
        staged_margin=4000.0,
        otm_pct=otm_pct,
    )


def make_config(**overrides) -> StrikeSelectionConfig:
    """Create a test config with optional overrides."""
    defaults = dict(
        target_delta=0.20,
        delta_tolerance=0.05,
        min_otm_pct=0.10,
        min_premium=0.20,
        max_spread_pct=0.30,
        min_volume=10,
        min_open_interest=50,
        max_candidates=5,
        fallback_to_otm=True,
        enabled=True,
    )
    defaults.update(overrides)
    return StrikeSelectionConfig(**defaults)


class TestStrikeSelectionConfig:
    """Tests for StrikeSelectionConfig."""

    def test_default_values(self):
        config = StrikeSelectionConfig()
        assert config.target_delta == 0.20
        assert config.delta_tolerance == 0.05
        assert config.min_otm_pct == 0.10
        assert config.enabled is True

    def test_from_env(self):
        with patch.dict("os.environ", {"STRIKE_TARGET_DELTA": "0.15", "ADAPTIVE_STRIKE_ENABLED": "false"}):
            config = StrikeSelectionConfig.from_env()
            assert config.target_delta == 0.15
            assert config.enabled is False


class TestGetCandidateStrikes:
    """Tests for _get_candidate_strikes filtering logic."""

    def setup_method(self):
        self.client = MagicMock()
        self.selector = LiveStrikeSelector(
            ibkr_client=self.client,
            config=make_config(),
        )

    def test_filters_to_otm_puts(self):
        """Strikes above max_strike (stock * (1 - min_otm)) are excluded."""
        chain = [190.0, 195.0, 200.0, 205.0, 210.0, 220.0, 225.0, 230.0]
        stock_price = 230.0
        # max_strike = 230 * (1 - 0.10) = 207
        candidates = self.selector._get_candidate_strikes(chain, stock_price, 200.0)
        assert all(c <= 207.0 for c in candidates)
        assert 220.0 not in candidates
        assert 230.0 not in candidates

    def test_limited_to_max_candidates(self):
        """No more than max_candidates returned."""
        chain = list(range(100, 210, 5))  # Many strikes
        candidates = self.selector._get_candidate_strikes(chain, 230.0, 200.0)
        assert len(candidates) <= self.selector.config.max_candidates

    def test_centered_around_current_strike(self):
        """Candidates are sorted by distance from current strike."""
        chain = [190.0, 195.0, 200.0, 205.0, 180.0, 185.0, 170.0]
        candidates = self.selector._get_candidate_strikes(chain, 230.0, 200.0)
        # 200.0 and nearby should be included (closest to current_strike=200)
        assert 200.0 in candidates

    def test_empty_chain_returns_empty(self):
        candidates = self.selector._get_candidate_strikes([], 230.0, 200.0)
        assert candidates == []

    def test_no_otm_strikes_returns_empty(self):
        """All strikes at or above stock price should be filtered out."""
        chain = [230.0, 235.0, 240.0]
        candidates = self.selector._get_candidate_strikes(chain, 230.0, 200.0)
        assert candidates == []


class TestSelectBestStrike:
    """Tests for _select_best_strike delta matching logic."""

    def setup_method(self):
        self.client = MagicMock()
        self.selector = LiveStrikeSelector(
            ibkr_client=self.client,
            config=make_config(),
        )

    def test_selects_closest_to_target_delta(self):
        """Strike with delta closest to 0.20 wins."""
        candidates = {
            195.0: {"delta": 0.25, "bid": 0.50, "ask": 0.60, "volume": 100, "oi": 500},
            200.0: {"delta": 0.19, "bid": 0.45, "ask": 0.55, "volume": 80, "oi": 400},
            205.0: {"delta": 0.15, "bid": 0.30, "ask": 0.40, "volume": 50, "oi": 200},
        }
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is not None
        strike, data = result
        assert strike == 200.0  # delta=0.19, distance=0.01 from target

    def test_rejects_delta_outside_tolerance(self):
        """Strikes with delta > target ± tolerance are excluded."""
        candidates = {
            180.0: {"delta": 0.10, "bid": 0.30, "ask": 0.40, "volume": 100, "oi": 500},
            195.0: {"delta": 0.30, "bid": 0.80, "ask": 0.90, "volume": 100, "oi": 500},
        }
        # target=0.20, tolerance=0.05 → range [0.15, 0.25]
        # delta=0.10 and delta=0.30 both outside
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is None

    def test_rejects_low_premium(self):
        """Strikes with bid below min_premium are excluded."""
        candidates = {
            200.0: {"delta": 0.20, "bid": 0.15, "ask": 0.25, "volume": 100, "oi": 500},
        }
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is None  # bid=0.15 < min_premium=0.20

    def test_rejects_low_otm(self):
        """Strikes too close to money are excluded."""
        candidates = {
            225.0: {"delta": 0.20, "bid": 0.50, "ask": 0.60, "volume": 100, "oi": 500},
        }
        # OTM% = (230-225)/230 = 2.2% < 10% minimum
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is None

    def test_rejects_wide_spread(self):
        """Strikes with spread > max_spread_pct are excluded."""
        candidates = {
            200.0: {"delta": 0.20, "bid": 0.30, "ask": 0.60, "volume": 100, "oi": 500},
        }
        # spread% = (0.60-0.30)/0.45 = 66.7% > 30%
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is None

    def test_rejects_low_volume(self):
        """Strikes below min_volume are excluded."""
        candidates = {
            200.0: {"delta": 0.20, "bid": 0.50, "ask": 0.60, "volume": 5, "oi": 500},
        }
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is None

    def test_rejects_low_oi(self):
        """Strikes below min_open_interest are excluded."""
        candidates = {
            200.0: {"delta": 0.20, "bid": 0.50, "ask": 0.60, "volume": 100, "oi": 20},
        }
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is None

    def test_none_volume_passes_soft_check(self):
        """If volume data is None, the liquidity check is skipped (soft)."""
        candidates = {
            200.0: {"delta": 0.20, "bid": 0.50, "ask": 0.60, "volume": None, "oi": None},
        }
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is not None
        assert result[0] == 200.0

    def test_all_passing_sorted_by_delta_distance(self):
        """Multiple passing candidates → closest to target wins."""
        candidates = {
            195.0: {"delta": 0.22, "bid": 0.50, "ask": 0.60, "volume": 100, "oi": 500},
            200.0: {"delta": 0.18, "bid": 0.45, "ask": 0.55, "volume": 100, "oi": 500},
            205.0: {"delta": 0.21, "bid": 0.40, "ask": 0.50, "volume": 100, "oi": 500},
        }
        result = self.selector._select_best_strike(candidates, 230.0)
        assert result is not None
        # distance from 0.20: 195→0.02, 200→0.02, 205→0.01
        assert result[0] == 205.0


class TestSelectAll:
    """Tests for select_all orchestration."""

    def setup_method(self):
        self.client = MagicMock()
        self.selector = LiveStrikeSelector(
            ibkr_client=self.client,
            config=make_config(),
        )

    @pytest.mark.asyncio
    async def test_disabled_returns_unchanged(self):
        """When disabled, all opportunities get UNCHANGED status."""
        self.selector.config.enabled = False
        opps = [make_opp(), make_opp(symbol="MSFT")]

        results = await self.selector.select_all(opps)

        assert len(results) == 2
        assert all(r.status == "UNCHANGED" for r in results)

    @pytest.mark.asyncio
    async def test_no_stock_price_returns_unchanged(self):
        """If all stock price sources are unavailable, keep original strike."""
        self.client.get_stock_price.return_value = None
        opp = make_opp(staged_stock_price=0.0)
        opp.current_stock_price = None

        results = await self.selector.select_all([opp])

        assert results[0].status == "UNCHANGED"
        assert "No stock price" in results[0].reason


class TestSelectForSymbol:
    """Integration-style tests for _select_for_symbol end-to-end."""

    def setup_method(self):
        self.client = MagicMock()
        self.client.get_stock_price.return_value = 230.0
        self.selector = LiveStrikeSelector(
            ibkr_client=self.client,
            config=make_config(),
        )

    @pytest.mark.asyncio
    async def test_no_chain_falls_back(self):
        """If option chain is empty, return UNCHANGED."""
        # Make _get_chain_strikes return empty
        self.selector._get_chain_strikes = MagicMock(return_value=[])

        result = await self.selector._select_for_symbol(make_opp())

        assert result.status == "UNCHANGED"
        assert "No option chain" in result.reason

    @pytest.mark.asyncio
    async def test_no_greeks_with_fallback(self):
        """If Greeks unavailable but fallback enabled → UNCHANGED."""
        self.selector._get_chain_strikes = MagicMock(
            return_value=[195.0, 200.0, 205.0]
        )
        self.selector._get_greeks_for_strikes = AsyncMock(return_value={})

        result = await self.selector._select_for_symbol(make_opp())

        assert result.status == "UNCHANGED"
        assert "falling back" in result.reason

    @pytest.mark.asyncio
    async def test_no_greeks_without_fallback(self):
        """If Greeks unavailable and fallback disabled → ABANDONED."""
        self.selector.config.fallback_to_otm = False
        self.selector._get_chain_strikes = MagicMock(
            return_value=[195.0, 200.0, 205.0]
        )
        self.selector._get_greeks_for_strikes = AsyncMock(return_value={})

        result = await self.selector._select_for_symbol(make_opp())

        assert result.status == "ABANDONED"

    @pytest.mark.asyncio
    async def test_successful_selection(self):
        """Full successful path: chain → Greeks → select → update opportunity."""
        self.selector._get_chain_strikes = MagicMock(
            return_value=[195.0, 200.0, 205.0]
        )
        self.selector._get_greeks_for_strikes = AsyncMock(return_value={
            195.0: {"delta": 0.25, "iv": 0.35, "gamma": 0.01, "theta": -0.02,
                     "bid": 0.60, "ask": 0.70, "volume": 100, "oi": 500},
            200.0: {"delta": 0.19, "iv": 0.30, "gamma": 0.008, "theta": -0.015,
                     "bid": 0.45, "ask": 0.55, "volume": 80, "oi": 400},
            205.0: {"delta": 0.15, "iv": 0.28, "gamma": 0.006, "theta": -0.01,
                     "bid": 0.30, "ask": 0.38, "volume": 50, "oi": 200},
        })

        opp = make_opp(strike=200.0)
        result = await self.selector._select_for_symbol(opp)

        assert result.status == "SELECTED" or result.status == "UNCHANGED"
        # delta=0.19 is closest to 0.20 → strike 200 or 195 (delta=0.25, dist=0.05)
        assert result.selected_delta is not None
        assert abs(result.selected_delta - self.selector.config.target_delta) <= self.selector.config.delta_tolerance

        # Check opportunity was updated
        assert opp.strike_selection_method == "delta"
        assert opp.live_delta is not None

    @pytest.mark.asyncio
    async def test_exception_returns_unchanged(self):
        """Exceptions during selection are caught → UNCHANGED."""
        self.client.get_stock_price.side_effect = Exception("Connection lost")

        result = await self.selector._select_for_symbol(make_opp())

        assert result.status == "UNCHANGED"
        assert "Error" in result.reason
