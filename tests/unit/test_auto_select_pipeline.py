"""Unit tests for Phase 3 auto-select pipeline: 4-weight scoring + portfolio selection.

Tests cover:
- compute_composite_score_4w: all 4 weights, 3-weight fallback, custom weights
- build_auto_select_portfolio: budget, sector, max positions, duplicates, sorting
- PortfolioCandidate.from_best_strike: construction from BestStrikeResult + AI data
"""

import pytest

from src.agentic.scanner_settings import (
    BudgetSettings,
    FilterSettings,
    RankingWeights,
    ScannerSettings,
)
from src.services.auto_selector import (
    BestStrikeResult,
    PortfolioCandidate,
    build_auto_select_portfolio,
    compute_composite_score_4w,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_best_strike(**kwargs) -> BestStrikeResult:
    """Create a BestStrikeResult with sensible defaults."""
    defaults = dict(
        symbol="AAPL",
        stock_price=150.0,
        strike=135.0,
        expiration="2026-02-28",
        dte=3,
        bid=0.50,
        ask=0.60,
        delta=0.10,
        iv=0.35,
        otm_pct=0.10,
        volume=200,
        open_interest=500,
        margin=2000.0,
        margin_source="ibkr_whatif",
        safety_score=0.80,
        liquidity_score=0.60,
        efficiency_score=0.50,
        composite_score=0.70,
        premium_margin_ratio=0.025,
        annualized_return_pct=304.2,
        contracts=2,
        sector="Technology",
    )
    defaults.update(kwargs)
    return BestStrikeResult(**defaults)


def _make_portfolio_candidate(**kwargs) -> PortfolioCandidate:
    """Create a PortfolioCandidate with sensible defaults."""
    defaults = dict(
        symbol="AAPL",
        stock_price=150.0,
        strike=135.0,
        expiration="2026-02-28",
        dte=3,
        bid=0.50,
        ask=0.60,
        delta=0.10,
        iv=0.35,
        otm_pct=0.10,
        volume=200,
        open_interest=500,
        margin=2000.0,
        margin_source="ibkr_whatif",
        safety_score=0.80,
        liquidity_score=0.60,
        efficiency_score=0.50,
        premium_margin_ratio=0.025,
        annualized_return_pct=304.2,
        contracts=1,
        sector="Technology",
        composite_score=0.70,
        total_margin=2000.0,
    )
    defaults.update(kwargs)
    return PortfolioCandidate(**defaults)


# ---------------------------------------------------------------------------
# compute_composite_score_4w tests
# ---------------------------------------------------------------------------


class TestCompositeScore4W:
    """Tests for compute_composite_score_4w()."""

    def test_default_weights_all_max(self):
        """All scores at 1.0 with AI=10 → composite = 1.0."""
        score = compute_composite_score_4w(
            safety=1.0, liquidity=1.0, efficiency=1.0, ai_score_raw=10.0,
        )
        assert score == pytest.approx(1.0, abs=0.01)

    def test_default_weights_correct_proportions(self):
        """With defaults (40/30/20/10), each component has correct weight."""
        # Only safety=1.0, rest=0 → score = 40/100 = 0.40
        score = compute_composite_score_4w(
            safety=1.0, liquidity=0.0, efficiency=0.0, ai_score_raw=1.0,
        )
        assert score == pytest.approx(0.40, abs=0.01)

        # Only liquidity=1.0 → score = 30/100 = 0.30
        score = compute_composite_score_4w(
            safety=0.0, liquidity=1.0, efficiency=0.0, ai_score_raw=1.0,
        )
        assert score == pytest.approx(0.30, abs=0.01)

        # Only efficiency=1.0 → score = 10/100 = 0.10
        score = compute_composite_score_4w(
            safety=0.0, liquidity=0.0, efficiency=1.0, ai_score_raw=1.0,
        )
        assert score == pytest.approx(0.10, abs=0.01)

        # Only AI=10 → ai_norm=1.0, score = 20/100 = 0.20
        score = compute_composite_score_4w(
            safety=0.0, liquidity=0.0, efficiency=0.0, ai_score_raw=10.0,
        )
        assert score == pytest.approx(0.20, abs=0.01)

    def test_ai_score_none_falls_back_to_3_weight(self):
        """When AI is None, use 3-weight: 40+30+10=80 total."""
        score = compute_composite_score_4w(
            safety=1.0, liquidity=1.0, efficiency=1.0, ai_score_raw=None,
        )
        # (40*1 + 30*1 + 10*1) / 80 = 80/80 = 1.0
        assert score == pytest.approx(1.0, abs=0.01)

    def test_ai_score_none_correct_proportions(self):
        """3-weight: safety gets 40/80=0.50 of total."""
        score = compute_composite_score_4w(
            safety=1.0, liquidity=0.0, efficiency=0.0, ai_score_raw=None,
        )
        # 40 / (40+30+10) = 40/80 = 0.50
        assert score == pytest.approx(0.50, abs=0.01)

    def test_custom_weights_normalize(self):
        """Custom weights (50/20/20/10) should normalize correctly."""
        score = compute_composite_score_4w(
            safety=1.0, liquidity=0.0, efficiency=0.0, ai_score_raw=1.0,
            w_safety=50, w_liquidity=20, w_ai=20, w_efficiency=10,
        )
        # 50/100 = 0.50
        assert score == pytest.approx(0.50, abs=0.01)

    def test_ai_score_normalized_1_to_10(self):
        """AI score of 1 normalizes to 0.0, 10 normalizes to 1.0."""
        score_low = compute_composite_score_4w(
            safety=0.0, liquidity=0.0, efficiency=0.0, ai_score_raw=1.0,
        )
        score_high = compute_composite_score_4w(
            safety=0.0, liquidity=0.0, efficiency=0.0, ai_score_raw=10.0,
        )
        # AI of 1 → norm 0.0 → score = 0
        assert score_low == pytest.approx(0.0, abs=0.01)
        # AI of 10 → norm 1.0 → score = 20/100 = 0.20
        assert score_high == pytest.approx(0.20, abs=0.01)

    def test_ai_score_midrange(self):
        """AI score of 5.5 normalizes to 0.5."""
        score = compute_composite_score_4w(
            safety=0.0, liquidity=0.0, efficiency=0.0, ai_score_raw=5.5,
        )
        # ai_norm = (5.5-1)/9 = 0.5
        # score = 20 * 0.5 / 100 = 0.10
        assert score == pytest.approx(0.10, abs=0.01)

    def test_zero_total_weight_returns_zero(self):
        """If all weights are 0, return 0."""
        score = compute_composite_score_4w(
            safety=1.0, liquidity=1.0, efficiency=1.0, ai_score_raw=10.0,
            w_safety=0, w_liquidity=0, w_ai=0, w_efficiency=0,
        )
        assert score == 0.0


# ---------------------------------------------------------------------------
# build_auto_select_portfolio tests
# ---------------------------------------------------------------------------


class TestBuildAutoSelectPortfolio:
    """Tests for build_auto_select_portfolio()."""

    def test_budget_constraint_stops_selection(self):
        """Candidates exceeding budget are skipped."""
        c1 = _make_portfolio_candidate(
            symbol="AAPL", composite_score=0.90, margin=3000.0, contracts=1,
        )
        c2 = _make_portfolio_candidate(
            symbol="MSFT", composite_score=0.80, margin=3000.0, contracts=1,
        )
        selected, skipped, _ = build_auto_select_portfolio(
            [c1, c2], available_budget=5000.0,
        )
        assert len(selected) == 1
        assert selected[0].symbol == "AAPL"
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "budget_exceeded"

    def test_sector_limit_enforced(self):
        """max_per_sector prevents too many from same sector."""
        c1 = _make_portfolio_candidate(
            symbol="AAPL", composite_score=0.90, margin=1000.0, sector="Technology",
        )
        c2 = _make_portfolio_candidate(
            symbol="MSFT", composite_score=0.80, margin=1000.0, sector="Technology",
        )
        c3 = _make_portfolio_candidate(
            symbol="GOOG", composite_score=0.70, margin=1000.0, sector="Technology",
        )
        selected, skipped, _ = build_auto_select_portfolio(
            [c1, c2, c3], available_budget=50000.0, max_per_sector=2,
        )
        assert len(selected) == 2
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "max_per_sector"

    def test_max_positions_enforced(self):
        """No more than max_positions trades selected."""
        candidates = [
            _make_portfolio_candidate(
                symbol=f"SYM{i}", composite_score=0.90 - i * 0.01,
                margin=500.0, sector=f"Sector{i}",
            )
            for i in range(5)
        ]
        selected, skipped, _ = build_auto_select_portfolio(
            candidates, available_budget=50000.0, max_positions=3,
        )
        assert len(selected) == 3
        assert len(skipped) == 2
        assert all(s.skip_reason == "max_positions" for s in skipped)

    def test_duplicate_symbol_rejected(self):
        """Same symbol cannot appear twice."""
        c1 = _make_portfolio_candidate(
            symbol="AAPL", composite_score=0.90, margin=1000.0,
        )
        c2 = _make_portfolio_candidate(
            symbol="AAPL", composite_score=0.80, margin=1000.0,
        )
        selected, skipped, _ = build_auto_select_portfolio(
            [c1, c2], available_budget=50000.0,
        )
        assert len(selected) == 1
        assert len(skipped) == 1
        assert skipped[0].skip_reason == "duplicate_symbol"

    def test_sorted_by_composite_desc(self):
        """Selected trades are ordered by composite score descending."""
        c1 = _make_portfolio_candidate(
            symbol="LOW", composite_score=0.50, margin=1000.0, sector="S1",
        )
        c2 = _make_portfolio_candidate(
            symbol="HIGH", composite_score=0.95, margin=1000.0, sector="S2",
        )
        c3 = _make_portfolio_candidate(
            symbol="MID", composite_score=0.70, margin=1000.0, sector="S3",
        )
        selected, _, _ = build_auto_select_portfolio(
            [c1, c2, c3], available_budget=50000.0,
        )
        assert [s.symbol for s in selected] == ["HIGH", "MID", "LOW"]

    def test_total_margin_equals_margin_times_contracts(self):
        """total_margin is set to margin × contracts for selected trades."""
        c = _make_portfolio_candidate(
            symbol="AAPL", composite_score=0.90, margin=2000.0, contracts=3,
        )
        selected, _, _ = build_auto_select_portfolio(
            [c], available_budget=50000.0,
        )
        assert selected[0].total_margin == 6000.0

    def test_empty_candidates_returns_empty(self):
        """Empty input returns empty output."""
        selected, skipped, warnings = build_auto_select_portfolio(
            [], available_budget=50000.0,
        )
        assert selected == []
        assert skipped == []
        assert warnings == []

    def test_portfolio_rank_assigned(self):
        """Each selected trade gets a 1-based portfolio rank."""
        candidates = [
            _make_portfolio_candidate(
                symbol=f"SYM{i}", composite_score=0.90 - i * 0.05,
                margin=1000.0, sector=f"S{i}",
            )
            for i in range(3)
        ]
        selected, _, _ = build_auto_select_portfolio(
            candidates, available_budget=50000.0,
        )
        assert [s.portfolio_rank for s in selected] == [1, 2, 3]

    def test_warning_when_nothing_fits_budget(self):
        """Warning emitted when all candidates exceed budget."""
        c = _make_portfolio_candidate(
            symbol="AAPL", composite_score=0.90, margin=10000.0, contracts=1,
        )
        _, _, warnings = build_auto_select_portfolio(
            [c], available_budget=5000.0,
        )
        assert any("budget" in w.lower() for w in warnings)

    def test_selected_flag_set_correctly(self):
        """selected=True for chosen candidates, False for skipped."""
        c1 = _make_portfolio_candidate(
            symbol="AAPL", composite_score=0.90, margin=1000.0, sector="S1",
        )
        c2 = _make_portfolio_candidate(
            symbol="MSFT", composite_score=0.80, margin=1000.0, sector="S2",
        )
        selected, skipped, _ = build_auto_select_portfolio(
            [c1, c2], available_budget=1500.0,
        )
        assert all(s.selected is True for s in selected)
        assert all(s.selected is False for s in skipped)


# ---------------------------------------------------------------------------
# PortfolioCandidate.from_best_strike tests
# ---------------------------------------------------------------------------


class TestPortfolioCandidateFromBestStrike:
    """Tests for PortfolioCandidate.from_best_strike()."""

    def test_basic_construction(self):
        bs = _make_best_strike()
        pc = PortfolioCandidate.from_best_strike(bs)
        assert pc.symbol == "AAPL"
        assert pc.strike == 135.0
        assert pc.ai_score is None
        assert pc.total_margin == 4000.0  # 2000 * 2

    def test_with_ai_data(self):
        bs = _make_best_strike()
        ai_data = {
            "score": 8,
            "recommendation": "strong_buy",
            "reasoning": "High IV, stable trend",
            "risk_flags": ["earnings_soon"],
        }
        pc = PortfolioCandidate.from_best_strike(bs, ai_data=ai_data)
        assert pc.ai_score == 8
        assert pc.ai_recommendation == "strong_buy"
        assert pc.ai_reasoning == "High IV, stable trend"
        assert pc.ai_risk_flags == ["earnings_soon"]

    def test_ai_data_none_gives_defaults(self):
        bs = _make_best_strike()
        pc = PortfolioCandidate.from_best_strike(bs, ai_data=None)
        assert pc.ai_score is None
        assert pc.ai_recommendation is None
        assert pc.ai_risk_flags == []
