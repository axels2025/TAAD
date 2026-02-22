"""Unit tests for StrikeFinder.

Tests the strike finding and scoring algorithm for optimal
naked put strike selection.
"""

import pytest
from datetime import date
from unittest.mock import patch, MagicMock

from src.data.candidates import BarchartCandidate
from src.scoring.scorer import ScoredCandidate
from src.services.strike_finder import (
    StrikeFinder,
    StrikePreferences,
    StrikeCandidate,
)
from src.services.limit_price_calculator import LimitPriceCalculator


def create_barchart_candidate(
    symbol: str = "TEST",
    strike: float = 100.0,
    underlying_price: float = 120.0,
    bid: float = 0.45,
    dte: int = 7,
    moneyness_pct: float = -0.17,
    iv_rank: float = 0.45,
    volume: int = 500,
    open_interest: int = 1000,
    delta: float = -0.15,
) -> BarchartCandidate:
    """Create a test BarchartCandidate."""
    return BarchartCandidate(
        symbol=symbol,
        expiration=date(2026, 2, 7),
        strike=strike,
        option_type="PUT",
        underlying_price=underlying_price,
        bid=bid,
        dte=dte,
        moneyness_pct=moneyness_pct,
        breakeven=strike - bid,
        breakeven_pct=moneyness_pct - (bid / underlying_price),
        volume=volume,
        open_interest=open_interest,
        iv_rank=iv_rank,
        delta=delta,
        premium_return_pct=0.01,
        annualized_return_pct=0.50,
        profit_probability=0.85,
    )


def create_scored_candidate(
    symbol: str = "TEST",
    composite_score: float = 75.0,
    **kwargs,
) -> ScoredCandidate:
    """Create a test ScoredCandidate."""
    candidate = create_barchart_candidate(symbol, **kwargs)
    scored = ScoredCandidate(candidate=candidate)
    scored.composite_score = composite_score
    scored.grade = "A" if composite_score >= 75 else "B"
    return scored


class TestStrikePreferences:
    """Tests for StrikePreferences dataclass."""

    def test_default_values(self):
        """Test default preference values."""
        prefs = StrikePreferences()
        assert prefs.min_premium == 0.30
        assert prefs.max_premium == 0.60
        assert prefs.target_premium == 0.40
        assert prefs.min_otm_pct == 0.15
        assert prefs.target_otm_pct == 0.20
        assert prefs.max_dte == 14
        assert prefs.target_dte == 7
        assert prefs.contract_price_threshold == 90.0
        assert prefs.contract_max_expensive == 3
        assert prefs.contract_max_cheap == 5

    def test_from_env_defaults(self):
        """Test loading from env with defaults.

        StrikePreferences.from_env() now delegates premium values to
        get_config() which reads the same env vars. The conftest
        reset_config fixture ensures a fresh singleton each test.
        """
        prefs = StrikePreferences.from_env()
        assert prefs.min_premium == 0.30
        assert prefs.target_otm_pct == 0.20

    def test_from_env_custom_values(self, monkeypatch):
        """Test loading custom values from env.

        Premium values now come from get_config(); OTM/contract values
        are still read directly from os.getenv in StrikePreferences.
        """
        monkeypatch.setenv("PREMIUM_MIN", "0.25")
        monkeypatch.setenv("PREMIUM_MAX", "0.75")
        monkeypatch.setenv("PREMIUM_TARGET", "0.45")
        monkeypatch.setenv("OTM_MIN_PCT", "0.12")
        monkeypatch.setenv("OTM_TARGET_PCT", "0.18")
        monkeypatch.setenv("CONTRACT_PRICE_THRESHOLD", "100.0")
        monkeypatch.setenv("CONTRACT_MAX_EXPENSIVE", "2")
        monkeypatch.setenv("CONTRACT_MAX_CHEAP", "4")

        prefs = StrikePreferences.from_env()
        assert prefs.min_premium == 0.25
        assert prefs.max_premium == 0.75
        assert prefs.target_premium == 0.45
        assert prefs.min_otm_pct == 0.12
        assert prefs.target_otm_pct == 0.18
        assert prefs.contract_price_threshold == 100.0
        assert prefs.contract_max_expensive == 2
        assert prefs.contract_max_cheap == 4


class TestStrikeCandidate:
    """Tests for StrikeCandidate dataclass."""

    def test_effective_margin_with_actual(self):
        """Test effective margin when actual margin is available."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.17,
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=2800.0,
            contracts=5,
            total_margin=14000.0,
            premium_income=235.0,
            margin_efficiency=0.0168,
        )
        assert candidate.effective_margin == 2800.0
        assert candidate.margin_source == "ibkr_whatif"

    def test_effective_margin_without_actual(self):
        """Test effective margin when only estimate is available."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.17,
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=None,
            contracts=5,
            total_margin=12500.0,
            premium_income=235.0,
            margin_efficiency=0.0188,
        )
        assert candidate.effective_margin == 2500.0
        assert candidate.margin_source == "estimated"

    def test_effective_margin_zero_actual_uses_estimate(self):
        """Test effective margin falls back to estimate when actual is 0."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.17,
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=0.0,
            contracts=5,
            total_margin=0.0,
            premium_income=235.0,
            margin_efficiency=0.0,
        )
        # Should fall back to estimate, not return 0
        assert candidate.effective_margin == 2500.0

    def test_effective_margin_zero_both_uses_regt_fallback(self):
        """Test effective margin uses Reg-T minimum when both are 0."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.17,
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=0.0,
            margin_actual=0.0,
            contracts=5,
            total_margin=0.0,
            premium_income=235.0,
            margin_efficiency=0.0,
        )
        # Should return Reg-T minimum: 10% * 120 * 100 = 1200
        assert candidate.effective_margin == 1200.0

    def test_effective_margin_never_zero(self):
        """Test effective margin never returns 0 regardless of inputs."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=50.0,
            strike=45.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.30,
            ask=0.35,
            mid=0.325,
            suggested_limit=0.32,
            otm_pct=0.10,
            delta=-0.10,
            iv=0.30,
            iv_rank=0.40,
            volume=200,
            open_interest=500,
            margin_estimate=0.0,
            margin_actual=None,
            contracts=3,
            total_margin=0.0,
            premium_income=96.0,
            margin_efficiency=0.0,
        )
        # Should return Reg-T minimum: 10% * 50 * 100 = 500
        assert candidate.effective_margin == 500.0
        assert candidate.effective_margin > 0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.17,
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=None,
            contracts=5,
            total_margin=12500.0,
            premium_income=235.0,
            margin_efficiency=0.0188,
            sector="Technology",
            score=75.5,
        )

        result = candidate.to_dict()

        assert result["symbol"] == "TEST"
        assert result["strike"] == 100.0
        assert result["expiration"] == "2026-02-07"
        assert result["sector"] == "Technology"
        assert result["score"] == 75.5


class TestStrikeFinder:
    """Tests for StrikeFinder class."""

    @pytest.fixture
    def finder(self):
        """Create a finder with default preferences."""
        return StrikeFinder(
            preferences=StrikePreferences(),
            limit_calculator=LimitPriceCalculator(),
        )

    @pytest.fixture
    def custom_finder(self):
        """Create a finder with custom preferences."""
        prefs = StrikePreferences(
            min_premium=0.20,
            max_premium=0.80,
            target_premium=0.50,
            min_otm_pct=0.10,
            target_otm_pct=0.25,
        )
        return StrikeFinder(preferences=prefs)

    # --- _determine_contracts tests ---

    def test_determine_contracts_expensive(self, finder):
        """Test contract count for expensive stocks (>$90)."""
        assert finder._determine_contracts(100.0) == 3
        assert finder._determine_contracts(150.0) == 3
        assert finder._determine_contracts(91.0) == 3

    def test_determine_contracts_cheap(self, finder):
        """Test contract count for cheaper stocks (<=$90)."""
        assert finder._determine_contracts(90.0) == 5
        assert finder._determine_contracts(50.0) == 5
        assert finder._determine_contracts(89.99) == 5

    def test_determine_contracts_boundary(self, finder):
        """Test contract count at boundary."""
        # At exactly $90, should get 5 contracts
        assert finder._determine_contracts(90.0) == 5
        # Just above $90, should get 3 contracts
        assert finder._determine_contracts(90.01) == 3

    # --- _estimate_margin tests ---

    def test_estimate_margin_basic(self, finder):
        """Test basic margin estimation."""
        # stock=120, strike=100, premium=0.45
        # OTM amount = 120 - 100 = 20
        # Method 1: (0.20 * 120 - 20 + 0.45) * 100 = (24 - 20 + 0.45) * 100 = 445
        # Method 2: 0.10 * 120 * 100 = 1200
        # Result: max(445, 1200) = 1200
        margin = finder._estimate_margin(120.0, 100.0, 0.45)
        assert margin == 1200.0

    def test_estimate_margin_closer_to_money(self, finder):
        """Test margin for strike closer to money."""
        # stock=120, strike=115, premium=0.80
        # OTM amount = 120 - 115 = 5
        # Method 1: (0.20 * 120 - 5 + 0.80) * 100 = (24 - 5 + 0.80) * 100 = 1980
        # Method 2: 0.10 * 120 * 100 = 1200
        # Result: max(1980, 1200) = 1980
        margin = finder._estimate_margin(120.0, 115.0, 0.80)
        assert margin == 1980.0

    # --- _passes_filters tests ---

    def test_passes_filters_valid(self, finder):
        """Test that valid candidate passes filters."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.17,  # 17% OTM > 15% min
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=None,
            contracts=5,
            total_margin=12500.0,
            premium_income=235.0,
            margin_efficiency=0.0188,
        )
        assert finder._passes_filters(candidate) is True

    def test_passes_filters_otm_too_low(self, finder):
        """Test that candidate with OTM below minimum fails."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=110.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.08,  # 8% OTM < 15% min
            delta=-0.25,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=None,
            contracts=5,
            total_margin=12500.0,
            premium_income=235.0,
            margin_efficiency=0.0188,
        )
        assert finder._passes_filters(candidate) is False

    def test_passes_filters_premium_too_low(self, finder):
        """Test that candidate with premium below minimum fails."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.20,  # Below $0.30 min
            ask=0.25,
            mid=0.225,
            suggested_limit=0.22,
            otm_pct=0.17,
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=None,
            contracts=5,
            total_margin=12500.0,
            premium_income=110.0,
            margin_efficiency=0.0088,
        )
        assert finder._passes_filters(candidate) is False

    def test_passes_filters_dte_too_high(self, finder):
        """Test that candidate with DTE above maximum fails."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=100.0,
            expiration=date(2026, 2, 20),
            dte=18,  # > 14 max
            bid=0.45,
            ask=0.50,
            mid=0.475,
            suggested_limit=0.47,
            otm_pct=0.17,
            delta=-0.15,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=None,
            contracts=5,
            total_margin=12500.0,
            premium_income=235.0,
            margin_efficiency=0.0188,
        )
        assert finder._passes_filters(candidate) is False

    def test_passes_filters_high_premium_allowed_if_far_otm(self, finder):
        """Test high premium allowed if far OTM."""
        candidate = StrikeCandidate(
            symbol="TEST",
            stock_price=120.0,
            strike=90.0,
            expiration=date(2026, 2, 7),
            dte=7,
            bid=0.80,  # > $0.60 max
            ask=0.90,
            mid=0.85,
            suggested_limit=0.82,
            otm_pct=0.25,  # 25% OTM - allows higher premium
            delta=-0.10,
            iv=0.35,
            iv_rank=0.45,
            volume=500,
            open_interest=1000,
            margin_estimate=2500.0,
            margin_actual=None,
            contracts=5,
            total_margin=12500.0,
            premium_income=410.0,
            margin_efficiency=0.0328,
        )
        assert finder._passes_filters(candidate) is True

    # --- Scoring tests ---

    def test_score_otm_at_target(self, finder):
        """Test OTM score at target (20%)."""
        score = finder._score_otm(0.20)
        assert score == 100

    def test_score_otm_below_target(self, finder):
        """Test OTM score below target."""
        # At 15% min, should be 80 (bottom of 80-100 range)
        score_at_min = finder._score_otm(0.15)
        assert score_at_min == 80

        # At 17.5% (halfway between min and target), should be ~90
        score_halfway = finder._score_otm(0.175)
        assert 88 <= score_halfway <= 92

    def test_score_otm_above_target(self, finder):
        """Test OTM score above target (no penalty - safety first)."""
        score = finder._score_otm(0.30)
        # Above target gets maximum score (safety-first: far OTM is ideal)
        assert score == 100

    def test_score_premium_at_target(self, finder):
        """Test premium score at target ($0.40)."""
        score = finder._score_premium(0.40)
        assert score == 100

    def test_score_premium_at_min(self, finder):
        """Test premium score at minimum."""
        score = finder._score_premium(0.30)
        # At min, should be 0
        assert score == 0

    def test_score_premium_below_min(self, finder):
        """Test premium score below minimum."""
        score = finder._score_premium(0.25)
        assert score == 0

    def test_score_iv_rank_low(self, finder):
        """Test IV rank score for low IV (good)."""
        score = finder._score_iv_rank(0.25)
        assert score == 100

    def test_score_iv_rank_high(self, finder):
        """Test IV rank score for high IV (risky)."""
        score = finder._score_iv_rank(0.70)
        assert score < 70

    def test_score_liquidity_good(self, finder):
        """Test liquidity score for good liquidity."""
        score = finder._score_liquidity(1000, 2000)
        assert score == 100

    def test_score_liquidity_low(self, finder):
        """Test liquidity score for low liquidity."""
        score = finder._score_liquidity(100, 200)
        assert score < 50

    # --- find_best_strikes tests ---

    def test_find_best_strikes_basic(self, finder):
        """Test finding best strikes for symbols."""
        barchart_data = {
            "AAPL": [
                create_scored_candidate(
                    "AAPL",
                    strike=140.0,
                    underlying_price=165.0,
                    bid=0.45,
                    moneyness_pct=-0.15,
                ),
                create_scored_candidate(
                    "AAPL",
                    strike=135.0,
                    underlying_price=165.0,
                    bid=0.35,
                    moneyness_pct=-0.18,
                ),
            ],
            "MSFT": [
                create_scored_candidate(
                    "MSFT",
                    strike=380.0,
                    underlying_price=450.0,
                    bid=0.50,
                    moneyness_pct=-0.16,
                ),
            ],
        }

        results = finder.find_best_strikes(["AAPL", "MSFT"], barchart_data)

        assert len(results) == 2
        assert all(isinstance(r, StrikeCandidate) for r in results)
        # Results should be sorted by score
        assert results[0].score >= results[1].score

    def test_find_best_strikes_missing_symbol(self, finder):
        """Test handling of missing symbol in data."""
        barchart_data = {
            "AAPL": [
                create_scored_candidate("AAPL", strike=140.0, underlying_price=165.0),
            ],
        }

        results = finder.find_best_strikes(["AAPL", "UNKNOWN"], barchart_data)

        # Should only return AAPL
        assert len(results) == 1
        assert results[0].symbol == "AAPL"

    def test_find_best_strikes_no_valid_strikes(self, finder):
        """Test when no strikes pass filters."""
        barchart_data = {
            "TEST": [
                # All candidates have OTM below minimum
                create_scored_candidate(
                    "TEST",
                    strike=115.0,
                    underlying_price=120.0,
                    bid=0.45,
                    moneyness_pct=-0.04,  # Only 4% OTM
                ),
            ],
        }

        results = finder.find_best_strikes(["TEST"], barchart_data)

        # Should return empty list
        assert len(results) == 0

    def test_find_best_strikes_selects_best_per_symbol(self, finder):
        """Test that best strike is selected per symbol."""
        barchart_data = {
            "TEST": [
                create_scored_candidate(
                    "TEST",
                    strike=100.0,
                    underlying_price=125.0,
                    bid=0.40,  # Target premium
                    moneyness_pct=-0.20,  # Target OTM
                    iv_rank=0.30,  # Low IV (good)
                    volume=1000,
                    open_interest=2000,
                ),
                create_scored_candidate(
                    "TEST",
                    strike=95.0,
                    underlying_price=125.0,
                    bid=0.30,  # Min premium
                    moneyness_pct=-0.24,  # Further OTM
                    iv_rank=0.50,
                    volume=500,
                    open_interest=1000,
                ),
            ],
        }

        results = finder.find_best_strikes(["TEST"], barchart_data)

        assert len(results) == 1
        # First candidate should win (better premium, lower IV)
        assert results[0].strike == 100.0


class TestStrikeFinderIntegration:
    """Integration tests for StrikeFinder."""

    def test_full_workflow(self):
        """Test complete strike finding workflow."""
        finder = StrikeFinder()

        # Create realistic test data
        # Note: PLTR has premium within range since high premium (>$0.60) only
        # allowed if OTM >= 25%
        barchart_data = {
            "IREN": [
                create_scored_candidate(
                    "IREN",
                    strike=40.0,
                    underlying_price=54.64,
                    bid=0.55,  # Within $0.30-$0.60 range
                    moneyness_pct=-0.27,
                    iv_rank=0.45,
                    volume=1500,
                    open_interest=3000,
                ),
            ],
            "SOXL": [
                create_scored_candidate(
                    "SOXL",
                    strike=50.0,
                    underlying_price=64.04,
                    bid=0.45,  # Within range
                    moneyness_pct=-0.22,
                    iv_rank=0.45,
                    volume=2000,
                    open_interest=5000,
                ),
            ],
            "PLTR": [
                create_scored_candidate(
                    "PLTR",
                    strike=115.0,
                    underlying_price=147.60,
                    bid=0.50,  # Within range (changed from $1.21)
                    moneyness_pct=-0.22,
                    iv_rank=0.55,
                    volume=800,
                    open_interest=1500,
                ),
            ],
        }

        results = finder.find_best_strikes(
            ["IREN", "SOXL", "PLTR"],
            barchart_data,
        )

        assert len(results) == 3

        # Verify each result has required fields
        for r in results:
            assert r.symbol in ["IREN", "SOXL", "PLTR"]
            assert r.suggested_limit > 0
            assert r.contracts in [3, 5]
            assert r.margin_estimate > 0
            assert r.score > 0
