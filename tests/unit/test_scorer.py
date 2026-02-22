"""Unit tests for NakedPutScorer."""

from datetime import date

import pytest

from src.data.candidates import BarchartCandidate
from src.scoring.score_config import ScoreWeights
from src.scoring.scorer import NakedPutScorer, ScoredCandidate


@pytest.fixture
def sample_candidate():
    """Create a sample BarchartCandidate for testing."""
    return BarchartCandidate(
        symbol="AAPL",
        expiration=date(2026, 2, 27),
        strike=200.0,
        option_type="PUT",
        underlying_price=220.0,
        bid=2.50,
        dte=30,
        moneyness_pct=-0.10,  # 10% OTM
        breakeven=197.50,
        breakeven_pct=-0.102,
        volume=500,
        open_interest=2000,
        iv_rank=0.65,  # 65%
        delta=-0.15,
        premium_return_pct=0.0125,  # 1.25%
        annualized_return_pct=0.35,  # 35%
        profit_probability=0.85,  # 85%
    )


@pytest.fixture
def scorer():
    """Create a NakedPutScorer instance."""
    return NakedPutScorer()


class TestNakedPutScorerInitialization:
    """Test scorer initialization."""

    def test_initialization_with_defaults(self):
        """Test scorer initializes with default configuration."""
        scorer = NakedPutScorer()

        assert scorer.weights.risk_adjusted_return == 0.15
        assert scorer.weights.probability == 0.20
        assert scorer.weights.iv_rank == 0.20
        assert scorer.weights.liquidity == 0.05
        assert scorer.weights.capital_efficiency == 0.25
        assert scorer.weights.safety_buffer == 0.15

    def test_initialization_with_custom_weights(self):
        """Test scorer initializes with custom weights."""
        custom_weights = ScoreWeights(
            risk_adjusted_return=0.30,
            probability=0.25,
            iv_rank=0.15,
            liquidity=0.10,
            capital_efficiency=0.10,
            safety_buffer=0.10,
        )
        scorer = NakedPutScorer(weights=custom_weights)

        assert scorer.weights.risk_adjusted_return == 0.30
        assert scorer.weights.probability == 0.25

    def test_initialization_validates_weights(self):
        """Test scorer validates that weights sum to 1.0."""
        invalid_weights = ScoreWeights(
            risk_adjusted_return=0.50,  # Total will be > 1.0
            probability=0.50,
            iv_rank=0.15,
            liquidity=0.15,
            capital_efficiency=0.15,
            safety_buffer=0.10,
        )

        with pytest.raises(ValueError, match="weights must sum to 1.0"):
            NakedPutScorer(weights=invalid_weights)


class TestScoreCandidate:
    """Test scoring individual candidates."""

    def test_score_candidate_returns_scored_object(self, scorer, sample_candidate):
        """Test score_candidate returns ScoredCandidate object."""
        result = scorer.score_candidate(sample_candidate)

        assert isinstance(result, ScoredCandidate)
        assert result.candidate == sample_candidate

    def test_score_candidate_calculates_all_dimensions(self, scorer, sample_candidate):
        """Test all dimension scores are calculated."""
        result = scorer.score_candidate(sample_candidate)

        # All dimension scores should be > 0
        assert result.return_score > 0
        assert result.probability_score > 0
        assert result.iv_rank_score > 0
        assert result.liquidity_score > 0
        assert result.efficiency_score > 0
        assert result.safety_score > 0

    def test_score_candidate_calculates_composite(self, scorer, sample_candidate):
        """Test composite score is calculated correctly."""
        result = scorer.score_candidate(sample_candidate)

        # Composite should be weighted average
        expected = (
            result.return_score * 0.15
            + result.probability_score * 0.20
            + result.iv_rank_score * 0.20
            + result.liquidity_score * 0.05
            + result.efficiency_score * 0.25
            + result.safety_score * 0.15
        )

        assert result.composite_score == pytest.approx(expected)

    def test_score_candidate_assigns_grade(self, scorer, sample_candidate):
        """Test grade is assigned based on composite score."""
        result = scorer.score_candidate(sample_candidate)

        assert result.grade in ["A+", "A", "B", "C", "D", "F"]

    def test_high_quality_candidate_scores_well(self, scorer):
        """Test high-quality candidate receives high score."""
        excellent_candidate = BarchartCandidate(
            symbol="NVDA",
            expiration=date(2026, 2, 20),
            strike=850.0,
            option_type="PUT",
            underlying_price=1000.0,
            bid=22.0,  # bid/strike = 2.59% -> efficiency_score=90
            dte=16,
            moneyness_pct=-0.15,  # 15% OTM - optimal
            breakeven=828.0,
            breakeven_pct=-0.172,
            volume=800,  # Excellent
            open_interest=5000,  # Excellent
            iv_rank=0.70,  # Optimal range
            delta=-0.16,
            premium_return_pct=0.0259,
            annualized_return_pct=0.40,  # Optimal range
            profit_probability=0.88,  # Very good
        )

        result = scorer.score_candidate(excellent_candidate)

        assert result.composite_score >= 80.0  # Should score A or A+
        assert result.grade in ["A+", "A"]

    def test_poor_quality_candidate_scores_low(self, scorer):
        """Test poor-quality candidate receives low score."""
        poor_candidate = BarchartCandidate(
            symbol="POOR",
            expiration=date(2026, 2, 20),
            strike=50.0,
            option_type="PUT",
            underlying_price=52.0,
            bid=0.15,
            dte=16,
            moneyness_pct=-0.038,  # 3.8% OTM - too close
            breakeven=49.85,
            breakeven_pct=-0.041,
            volume=20,  # Poor
            open_interest=150,  # Poor
            iv_rank=0.18,  # Low IV
            delta=-0.35,
            premium_return_pct=0.003,
            annualized_return_pct=0.08,  # Low return
            profit_probability=0.65,  # Low probability
        )

        result = scorer.score_candidate(poor_candidate)

        assert result.composite_score < 50.0  # Should score D or F
        assert result.grade in ["D", "F"]


class TestScoreAll:
    """Test scoring multiple candidates."""

    def test_score_all_scores_all_candidates(self, scorer, sample_candidate):
        """Test score_all scores all provided candidates."""
        candidates = [sample_candidate] * 3
        results = scorer.score_all(candidates)

        assert len(results) == 3
        assert all(isinstance(r, ScoredCandidate) for r in results)

    def test_score_all_sorts_by_composite_score(self, scorer):
        """Test score_all sorts by composite score descending."""
        # Create candidates with different quality
        good = BarchartCandidate(
            symbol="GOOD",
            expiration=date(2026, 2, 27),
            strike=200.0,
            option_type="PUT",
            underlying_price=220.0,
            bid=3.00,
            dte=30,
            moneyness_pct=-0.15,
            breakeven=197.0,
            breakeven_pct=-0.105,
            volume=1000,
            open_interest=5000,
            iv_rank=0.70,
            delta=-0.15,
            premium_return_pct=0.015,
            annualized_return_pct=0.40,
            profit_probability=0.90,
        )

        average = BarchartCandidate(
            symbol="AVG",
            expiration=date(2026, 2, 27),
            strike=200.0,
            option_type="PUT",
            underlying_price=220.0,
            bid=2.00,
            dte=30,
            moneyness_pct=-0.10,
            breakeven=198.0,
            breakeven_pct=-0.10,
            volume=300,
            open_interest=1500,
            iv_rank=0.50,
            delta=-0.18,
            premium_return_pct=0.010,
            annualized_return_pct=0.30,
            profit_probability=0.82,
        )

        poor = BarchartCandidate(
            symbol="POOR",
            expiration=date(2026, 2, 27),
            strike=200.0,
            option_type="PUT",
            underlying_price=220.0,
            bid=0.50,
            dte=30,
            moneyness_pct=-0.05,
            breakeven=199.5,
            breakeven_pct=-0.095,
            volume=50,
            open_interest=200,
            iv_rank=0.20,
            delta=-0.30,
            premium_return_pct=0.0025,
            annualized_return_pct=0.10,
            profit_probability=0.70,
        )

        results = scorer.score_all([poor, good, average])

        # Should be sorted: good > average > poor
        assert results[0].symbol == "GOOD"
        assert results[1].symbol == "AVG"
        assert results[2].symbol == "POOR"

    def test_score_all_assigns_ranks(self, scorer, sample_candidate):
        """Test score_all assigns rank numbers."""
        candidates = [sample_candidate] * 5
        results = scorer.score_all(candidates)

        # Ranks should be 1, 2, 3, 4, 5
        ranks = [r.rank for r in results]
        assert ranks == [1, 2, 3, 4, 5]


class TestApplyDiversification:
    """Test diversification rules."""

    def test_diversification_limits_per_symbol(self, scorer):
        """Test diversification limits candidates per symbol."""
        # Create 5 candidates with same symbol
        candidates = []
        for i in range(5):
            c = BarchartCandidate(
                symbol="AAPL",
                expiration=date(2026, 2, 27),
                strike=200.0 + i * 5,  # Different strikes
                option_type="PUT",
                underlying_price=220.0,
                bid=2.50,
                dte=30,
                moneyness_pct=-0.10,
                breakeven=197.50,
                breakeven_pct=-0.102,
                volume=500,
                open_interest=2000,
                iv_rank=0.65,
                delta=-0.15,
                premium_return_pct=0.0125,
                annualized_return_pct=0.35,
                profit_probability=0.85,
            )
            candidates.append(c)

        scored = scorer.score_all(candidates)
        diversified = scorer.apply_diversification(scored)

        # Should keep only 3 (default max_per_symbol)
        assert len(diversified) == 3
        assert all(d.symbol == "AAPL" for d in diversified)

    def test_diversification_custom_max_per_symbol(self, scorer):
        """Test diversification with custom max_per_symbol."""
        # Create 5 candidates with same symbol
        candidates = []
        for i in range(5):
            c = BarchartCandidate(
                symbol="AAPL",
                expiration=date(2026, 2, 27),
                strike=200.0 + i * 5,
                option_type="PUT",
                underlying_price=220.0,
                bid=2.50,
                dte=30,
                moneyness_pct=-0.10,
                breakeven=197.50,
                breakeven_pct=-0.102,
                volume=500,
                open_interest=2000,
                iv_rank=0.65,
                delta=-0.15,
                premium_return_pct=0.0125,
                annualized_return_pct=0.35,
                profit_probability=0.85,
            )
            candidates.append(c)

        scored = scorer.score_all(candidates)
        diversified = scorer.apply_diversification(scored, max_per_symbol=2)

        # Should keep only 2
        assert len(diversified) == 2

    def test_diversification_assigns_diversified_rank(self, scorer, sample_candidate):
        """Test diversification assigns diversified_rank."""
        candidates = [sample_candidate] * 3
        scored = scorer.score_all(candidates)
        diversified = scorer.apply_diversification(scored)

        # Diversified ranks should be 1, 2, 3
        ranks = [d.diversified_rank for d in diversified]
        assert ranks == [1, 2, 3]


class TestScoredCandidateProperties:
    """Test ScoredCandidate property accessors."""

    def test_scored_candidate_properties(self, scorer, sample_candidate):
        """Test ScoredCandidate exposes candidate properties."""
        scored = scorer.score_candidate(sample_candidate)

        assert scored.symbol == "AAPL"
        assert scored.strike == 200.0
        assert scored.expiration == date(2026, 2, 27)
        assert scored.dte == 30
        assert scored.bid == 2.50

    def test_scored_candidate_to_dict(self, scorer, sample_candidate):
        """Test ScoredCandidate.to_dict() includes all data."""
        scored = scorer.score_candidate(sample_candidate)
        data = scored.to_dict()

        # Should include candidate data
        assert data["symbol"] == "AAPL"
        assert data["strike"] == 200.0

        # Should include scores
        assert "composite_score" in data
        assert "return_score" in data
        assert "probability_score" in data
        assert "grade" in data


class TestGradeAssignment:
    """Test grade assignment logic."""

    def test_grade_a_plus(self, scorer):
        """Test score >=85 gets A+."""
        scored = ScoredCandidate(candidate=None, composite_score=90.0)  # type: ignore
        grade = scorer._get_grade(scored.composite_score)
        assert grade == "A+"

    def test_grade_a(self, scorer):
        """Test score 75-84 gets A."""
        scored = ScoredCandidate(candidate=None, composite_score=80.0)  # type: ignore
        grade = scorer._get_grade(scored.composite_score)
        assert grade == "A"

    def test_grade_b(self, scorer):
        """Test score 65-74 gets B."""
        scored = ScoredCandidate(candidate=None, composite_score=70.0)  # type: ignore
        grade = scorer._get_grade(scored.composite_score)
        assert grade == "B"

    def test_grade_f(self, scorer):
        """Test score <45 gets F."""
        scored = ScoredCandidate(candidate=None, composite_score=40.0)  # type: ignore
        grade = scorer._get_grade(scored.composite_score)
        assert grade == "F"


class TestScoringMetadata:
    """Test scoring metadata retrieval."""

    def test_get_scoring_metadata(self, scorer):
        """Test get_scoring_metadata returns configuration."""
        metadata = scorer.get_scoring_metadata()

        assert "scoring_version" in metadata
        assert metadata["scoring_version"] == "1.0"
        assert "weights" in metadata
        assert "diversification" in metadata
        assert metadata["weights"]["risk_adjusted_return"] == 0.15
