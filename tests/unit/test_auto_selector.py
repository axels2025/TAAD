"""Unit tests for the best-strike auto-selector.

Tests cover:
- Filtering logic (delta range, min premium, OTM %, delta=None, bid=0)
- Safety scoring (delta proximity, OTM distance, high-delta penalty)
- Liquidity scoring (OI tiers, spread %, volume tiers)
- Efficiency scoring (annualized return tiers, margin=None)
- Composite score with normalized weights
- Selection: shortest DTE preference, symbol skipping
"""

import pytest

from src.agentic.scanner_settings import (
    BudgetSettings,
    FilterSettings,
    RankingWeights,
    ScannerSettings,
)
from src.services.auto_selector import (
    AutoSelector,
    BestStrikeResult,
    ScannerStrikeCandidate,
    compute_efficiency_score,
    compute_liquidity_score,
    compute_safety_score,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _default_settings(**overrides) -> ScannerSettings:
    """Create ScannerSettings with optional overrides."""
    filters = overrides.pop("filters", {})
    ranking = overrides.pop("ranking", {})
    budget = overrides.pop("budget", {})
    return ScannerSettings(
        filters=FilterSettings(**filters) if filters else FilterSettings(),
        ranking=RankingWeights(**ranking) if ranking else RankingWeights(),
        budget=BudgetSettings(**budget) if budget else BudgetSettings(),
    )


def _make_candidate(**kwargs) -> ScannerStrikeCandidate:
    """Create a ScannerStrikeCandidate with sensible defaults."""
    defaults = dict(
        symbol="AAPL",
        stock_price=150.0,
        strike=135.0,
        expiration="2026-02-28",
        dte=3,
        bid=0.50,
        ask=0.60,
        mid=0.55,
        delta=0.10,
        iv=0.35,
        theta=-0.05,
        volume=200,
        open_interest=500,
        otm_pct=0.10,
    )
    defaults.update(kwargs)
    return ScannerStrikeCandidate(**defaults)


def _make_chain(symbol="AAPL", stock_price=150.0, puts=None) -> dict:
    """Create chain data dict as returned by IBKRScannerService."""
    if puts is None:
        puts = [
            {
                "strike": 135.0, "bid": 0.50, "ask": 0.60, "mid": 0.55,
                "delta": 0.10, "iv": 0.35, "theta": -0.05,
                "volume": 200, "open_interest": 500, "otm_pct": 0.10,
            },
        ]
    return {
        "symbol": symbol,
        "stock_price": stock_price,
        "expirations": [
            {"date": "2026-02-28", "dte": 3, "puts": puts},
        ],
    }


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------


class TestFilterCandidates:
    """Tests for AutoSelector.filter_candidates()."""

    def test_passes_valid_candidate(self):
        selector = AutoSelector(_default_settings())
        chain = _make_chain(puts=[
            {"strike": 135.0, "bid": 0.50, "ask": 0.60, "mid": 0.55,
             "delta": 0.10, "otm_pct": 0.10, "iv": 0.35, "theta": -0.05,
             "volume": 200, "open_interest": 500},
        ])
        result = selector.filter_candidates(chain)
        assert len(result) == 1
        assert result[0].strike == 135.0

    def test_excludes_delta_below_min(self):
        selector = AutoSelector(_default_settings())
        chain = _make_chain(puts=[
            {"strike": 135.0, "bid": 0.50, "ask": 0.60, "mid": 0.55,
             "delta": 0.03, "otm_pct": 0.10, "iv": 0.35, "theta": -0.05,
             "volume": 200, "open_interest": 500},
        ])
        result = selector.filter_candidates(chain)
        assert len(result) == 0

    def test_excludes_delta_above_max(self):
        selector = AutoSelector(_default_settings())
        chain = _make_chain(puts=[
            {"strike": 135.0, "bid": 0.50, "ask": 0.60, "mid": 0.55,
             "delta": 0.35, "otm_pct": 0.10, "iv": 0.35, "theta": -0.05,
             "volume": 200, "open_interest": 500},
        ])
        result = selector.filter_candidates(chain)
        assert len(result) == 0

    def test_excludes_bid_below_min_premium(self):
        selector = AutoSelector(_default_settings())
        chain = _make_chain(puts=[
            {"strike": 135.0, "bid": 0.10, "ask": 0.20, "mid": 0.15,
             "delta": 0.10, "otm_pct": 0.10, "iv": 0.35, "theta": -0.05,
             "volume": 200, "open_interest": 500},
        ])
        result = selector.filter_candidates(chain)
        assert len(result) == 0

    def test_excludes_otm_below_min(self):
        selector = AutoSelector(_default_settings(
            filters={"delta_min": 0.05, "delta_max": 0.30,
                     "delta_target": 0.065, "min_premium": 0.30,
                     "min_otm_pct": 0.10}
        ))
        chain = _make_chain(puts=[
            {"strike": 145.0, "bid": 0.50, "ask": 0.60, "mid": 0.55,
             "delta": 0.20, "otm_pct": 0.03, "iv": 0.35, "theta": -0.05,
             "volume": 200, "open_interest": 500},
        ])
        result = selector.filter_candidates(chain)
        assert len(result) == 0

    def test_excludes_delta_none(self):
        selector = AutoSelector(_default_settings())
        chain = _make_chain(puts=[
            {"strike": 135.0, "bid": 0.50, "ask": 0.60, "mid": 0.55,
             "delta": None, "otm_pct": 0.10, "iv": 0.35, "theta": -0.05,
             "volume": 200, "open_interest": 500},
        ])
        result = selector.filter_candidates(chain)
        assert len(result) == 0

    def test_excludes_bid_zero(self):
        selector = AutoSelector(_default_settings())
        chain = _make_chain(puts=[
            {"strike": 135.0, "bid": 0.0, "ask": 0.60, "mid": 0.30,
             "delta": 0.10, "otm_pct": 0.10, "iv": 0.35, "theta": -0.05,
             "volume": 200, "open_interest": 500},
        ])
        result = selector.filter_candidates(chain)
        assert len(result) == 0

    def test_no_stock_price_returns_empty(self):
        selector = AutoSelector(_default_settings())
        chain = {"symbol": "AAPL", "stock_price": None, "expirations": []}
        result = selector.filter_candidates(chain)
        assert len(result) == 0


# ---------------------------------------------------------------------------
# Safety score tests
# ---------------------------------------------------------------------------


class TestSafetyScore:
    """Tests for compute_safety_score()."""

    def test_delta_at_target_gets_highest(self):
        score = compute_safety_score(delta=0.065, otm_pct=0.20, delta_target=0.065)
        # delta component = 1.0, otm component = 1.0
        assert score == pytest.approx(1.0, abs=0.01)

    def test_delta_none_returns_zero(self):
        assert compute_safety_score(delta=None, otm_pct=0.20, delta_target=0.065) == 0.0

    def test_high_delta_gets_penalty(self):
        """Delta > 0.20 should be penalized (capped at 0.3 for delta component)."""
        score = compute_safety_score(delta=0.25, otm_pct=0.20, delta_target=0.065)
        # delta component capped at 0.3, otm = 1.0
        # 0.6 * 0.3 + 0.4 * 1.0 = 0.58
        assert score <= 0.60

    def test_far_otm_scores_higher_than_near(self):
        far = compute_safety_score(delta=0.065, otm_pct=0.25, delta_target=0.065)
        near = compute_safety_score(delta=0.065, otm_pct=0.08, delta_target=0.065)
        assert far > near

    def test_low_otm_gets_low_score(self):
        score = compute_safety_score(delta=0.065, otm_pct=0.03, delta_target=0.065)
        # OTM < 5% -> 0.1 sub-score
        assert score < 0.7


# ---------------------------------------------------------------------------
# Liquidity score tests
# ---------------------------------------------------------------------------


class TestLiquidityScore:
    """Tests for compute_liquidity_score()."""

    def test_high_oi_full_score(self):
        score = compute_liquidity_score(
            open_interest=1500, volume=500, bid=1.00, ask=1.05
        )
        # OI=1.0, spread~5%=1.0, vol=1.0 -> avg 1.0
        assert score == pytest.approx(1.0, abs=0.05)

    def test_wide_spread_low_score(self):
        score = compute_liquidity_score(
            open_interest=1000, volume=500, bid=0.30, ask=0.80
        )
        # spread = 0.50/0.55 ~ 91% -> 0.1
        # OI=1.0, vol=1.0 -> avg = (1.0 + 0.1 + 1.0)/3 ~ 0.7
        assert score < 0.8

    def test_zero_volume_low_subscore(self):
        score = compute_liquidity_score(
            open_interest=1000, volume=0, bid=1.00, ask=1.05
        )
        # vol=0 -> 0.1 subscore
        assert score < 0.8

    def test_all_low_gets_minimum(self):
        score = compute_liquidity_score(
            open_interest=10, volume=0, bid=0.10, ask=0.50
        )
        # OI=0.1, spread=0.1, vol=0.1 -> 0.1
        assert score == pytest.approx(0.1, abs=0.01)

    def test_none_values_treated_as_zero(self):
        score = compute_liquidity_score(
            open_interest=None, volume=None, bid=1.00, ask=1.05
        )
        # OI=0.1, vol=0.1, spread~5%=1.0 -> 0.4
        assert score == pytest.approx(0.4, abs=0.05)


# ---------------------------------------------------------------------------
# Efficiency score tests
# ---------------------------------------------------------------------------


class TestEfficiencyScore:
    """Tests for compute_efficiency_score()."""

    def test_high_annualized_gets_full(self):
        # bid=1.00, margin=1000, dte=3 -> (100/1000)*(365/3) = 12.17 = 1217%
        score = compute_efficiency_score(premium_bid=1.00, margin=1000.0, dte=3)
        assert score == 1.0

    def test_moderate_annualized(self):
        # bid=0.30, margin=3000, dte=7 -> (30/3000)*(365/7) = 0.521 -> 1.0
        score = compute_efficiency_score(premium_bid=0.30, margin=3000.0, dte=7)
        assert score == 1.0

    def test_low_annualized(self):
        # bid=0.05, margin=5000, dte=30 -> (5/5000)*(365/30) = 0.0122 -> 0.2
        score = compute_efficiency_score(premium_bid=0.05, margin=5000.0, dte=30)
        assert score == 0.2

    def test_margin_none_returns_zero(self):
        score = compute_efficiency_score(premium_bid=0.50, margin=None, dte=3)
        assert score == 0.0

    def test_margin_zero_returns_zero(self):
        score = compute_efficiency_score(premium_bid=0.50, margin=0.0, dte=3)
        assert score == 0.0

    def test_dte_zero_returns_zero(self):
        score = compute_efficiency_score(premium_bid=0.50, margin=1000.0, dte=0)
        assert score == 0.0


# ---------------------------------------------------------------------------
# Composite score tests
# ---------------------------------------------------------------------------


class TestCompositeScore:
    """Tests for AutoSelector.score_candidate() and weight normalization."""

    def test_default_weights_normalized(self):
        """Default weights: safety=40, liquidity=30, ai=20, efficiency=10.
        Normalized (exclude AI): safety=50%, liquidity=37.5%, efficiency=12.5%.
        """
        selector = AutoSelector(_default_settings())
        assert selector.w_safety == pytest.approx(0.50, abs=0.01)
        assert selector.w_liquidity == pytest.approx(0.375, abs=0.01)
        assert selector.w_efficiency == pytest.approx(0.125, abs=0.01)

    def test_custom_weights_normalized(self):
        selector = AutoSelector(_default_settings(
            ranking={"safety": 50, "liquidity": 20, "ai_score": 20, "efficiency": 10}
        ))
        # available = 50 + 20 + 10 = 80
        assert selector.w_safety == pytest.approx(50 / 80, abs=0.01)
        assert selector.w_liquidity == pytest.approx(20 / 80, abs=0.01)
        assert selector.w_efficiency == pytest.approx(10 / 80, abs=0.01)

    def test_score_candidate_returns_float(self):
        selector = AutoSelector(_default_settings())
        c = _make_candidate(margin=2000.0)
        score = selector.score_candidate(c)
        assert 0.0 <= score <= 1.0


# ---------------------------------------------------------------------------
# Selection tests
# ---------------------------------------------------------------------------


class TestSelectBestPerSymbol:
    """Tests for AutoSelector.select_best_per_symbol()."""

    def test_selects_best_from_candidates(self):
        selector = AutoSelector(_default_settings())
        c1 = _make_candidate(strike=135.0, delta=0.10, otm_pct=0.10, bid=0.50)
        c2 = _make_candidate(strike=130.0, delta=0.065, otm_pct=0.13, bid=0.40)
        margins = {
            "AAPL|135.0|2026-02-28": 2000.0,
            "AAPL|130.0|2026-02-28": 1800.0,
        }
        results = selector.select_best_per_symbol({"AAPL": [c1, c2]}, margins)
        assert len(results) == 1
        assert results[0].status == "selected"
        assert results[0].composite_score > 0

    def test_shorter_dte_preferred(self):
        selector = AutoSelector(_default_settings())
        c_long = _make_candidate(
            strike=135.0, expiration="2026-03-05", dte=8,
            delta=0.065, otm_pct=0.15, bid=0.80,
            open_interest=1500, volume=500,
        )
        c_short = _make_candidate(
            strike=135.0, expiration="2026-02-28", dte=3,
            delta=0.065, otm_pct=0.15, bid=0.50,
            open_interest=1500, volume=500,
        )
        margins = {
            "AAPL|135.0|2026-03-05": 2000.0,
            "AAPL|135.0|2026-02-28": 2000.0,
        }
        results = selector.select_best_per_symbol(
            {"AAPL": [c_long, c_short]}, margins
        )
        assert results[0].expiration == "2026-02-28"

    def test_symbol_skipped_when_no_candidates(self):
        selector = AutoSelector(_default_settings())
        results = selector.select_best_per_symbol({"AAPL": []}, {})
        assert len(results) == 1
        assert results[0].status == "skipped"
        assert results[0].skip_reason == "no_candidates"

    def test_margin_fallback_to_regt_estimate(self):
        """When margin is not in the dict, Reg-T estimate is used."""
        selector = AutoSelector(_default_settings())
        c = _make_candidate(strike=135.0, stock_price=150.0, bid=0.50)
        results = selector.select_best_per_symbol({"AAPL": [c]}, {})
        assert results[0].margin > 0
        assert results[0].margin_source == "estimated"

    def test_ibkr_margin_preferred_over_regt(self):
        selector = AutoSelector(_default_settings())
        c = _make_candidate(strike=135.0, bid=0.50)
        ibkr_margin = 2500.0
        margins = {"AAPL|135.0|2026-02-28": ibkr_margin}
        results = selector.select_best_per_symbol({"AAPL": [c]}, margins)
        assert results[0].margin == ibkr_margin
        assert results[0].margin_source == "ibkr_whatif"

    def test_multiple_symbols(self):
        selector = AutoSelector(_default_settings())
        c_aapl = _make_candidate(symbol="AAPL", strike=135.0, bid=0.50)
        c_msft = _make_candidate(symbol="MSFT", strike=280.0,
                                 stock_price=310.0, bid=0.60)
        results = selector.select_best_per_symbol(
            {"AAPL": [c_aapl], "MSFT": [c_msft]}, {}
        )
        assert len(results) == 2
        symbols = {r.symbol for r in results}
        assert symbols == {"AAPL", "MSFT"}

    def test_annualized_return_computed(self):
        selector = AutoSelector(_default_settings())
        c = _make_candidate(strike=135.0, bid=0.50, dte=3)
        margins = {"AAPL|135.0|2026-02-28": 2000.0}
        results = selector.select_best_per_symbol({"AAPL": [c]}, margins)
        # premium_margin_ratio = 0.50 * 100 / 2000 = 0.025
        # annualized = 0.025 * 365/3 = 3.042 = 304.2%
        assert results[0].annualized_return_pct > 0
        assert results[0].premium_margin_ratio > 0


# ---------------------------------------------------------------------------
# Reg-T margin estimate tests
# ---------------------------------------------------------------------------


class TestRegTEstimate:
    """Tests for AutoSelector._estimate_margin_regt()."""

    def test_basic_estimate(self):
        c = _make_candidate(stock_price=150.0, strike=135.0, bid=0.50)
        margin = AutoSelector._estimate_margin_regt(c)
        # 0.20 * 150 - (150-135) + 0.50 = 30 - 15 + 0.50 = 15.50 * 100 = 1550
        # min = 0.10 * 150 * 100 = 1500
        # max(1550, 1500) = 1550
        assert margin == pytest.approx(1550.0, abs=1.0)

    def test_deep_otm_uses_minimum(self):
        c = _make_candidate(stock_price=150.0, strike=100.0, bid=0.10)
        margin = AutoSelector._estimate_margin_regt(c)
        # 0.20 * 150 - 50 + 0.10 = -19.9 * 100 = negative
        # min = 0.10 * 150 * 100 = 1500
        assert margin == 1500.0
