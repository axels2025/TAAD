"""Main scoring engine for naked put candidates.

Calculates composite scores for BarchartCandidate objects using
research-backed scoring rules.
"""

from dataclasses import dataclass

from loguru import logger

from src.data.candidates import BarchartCandidate
from src.scoring.score_config import (
    DEFAULT_DIVERSIFICATION,
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    DiversificationRules,
    ScoreThresholds,
    ScoreWeights,
)
from src.scoring.score_rules import (
    score_capital_efficiency,
    score_iv_rank,
    score_liquidity,
    score_probability,
    score_risk_adjusted_return,
    score_safety_buffer,
)


@dataclass
class ScoredCandidate:
    """Candidate with scoring breakdown."""

    candidate: BarchartCandidate

    # Individual dimension scores (0-100)
    return_score: float = 0.0
    probability_score: float = 0.0
    iv_rank_score: float = 0.0
    liquidity_score: float = 0.0
    efficiency_score: float = 0.0
    safety_score: float = 0.0

    # Composite score (0-100)
    composite_score: float = 0.0

    # Ranking info
    rank: int | None = None
    diversified_rank: int | None = None

    # Grade interpretation
    grade: str = ""

    @property
    def symbol(self) -> str:
        """Get symbol from candidate."""
        return self.candidate.symbol

    @property
    def strike(self) -> float:
        """Get strike from candidate."""
        return self.candidate.strike

    @property
    def expiration(self):
        """Get expiration from candidate."""
        return self.candidate.expiration

    @property
    def dte(self) -> int:
        """Get DTE from candidate."""
        return self.candidate.dte

    @property
    def bid(self) -> float:
        """Get bid from candidate."""
        return self.candidate.bid

    def to_dict(self) -> dict:
        """Convert to dictionary for storage/display.

        Returns:
            Dictionary with all candidate and score data
        """
        return {
            **self.candidate.to_dict(),
            "return_score": round(self.return_score, 1),
            "probability_score": round(self.probability_score, 1),
            "iv_rank_score": round(self.iv_rank_score, 1),
            "liquidity_score": round(self.liquidity_score, 1),
            "efficiency_score": round(self.efficiency_score, 1),
            "safety_score": round(self.safety_score, 1),
            "composite_score": round(self.composite_score, 1),
            "rank": self.rank,
            "diversified_rank": self.diversified_rank,
            "grade": self.grade,
        }


class NakedPutScorer:
    """Scores naked put candidates using research-backed rules."""

    SCORING_VERSION = "1.0"  # Track for reproducibility

    def __init__(
        self,
        weights: ScoreWeights | None = None,
        thresholds: ScoreThresholds | None = None,
        diversification: DiversificationRules | None = None,
    ):
        """Initialize scorer with configuration.

        Args:
            weights: Score dimension weights (must sum to 1.0)
            thresholds: Scoring thresholds
            diversification: Diversification rules

        Raises:
            ValueError: If weights do not sum to 1.0
        """
        self.weights = weights or DEFAULT_WEIGHTS
        self.thresholds = thresholds or DEFAULT_THRESHOLDS
        self.diversification = diversification or DEFAULT_DIVERSIFICATION

        if not self.weights.validate():
            raise ValueError("Score weights must sum to 1.0")

        logger.info(
            f"Initialized NakedPutScorer v{self.SCORING_VERSION} with weights: "
            f"return={self.weights.risk_adjusted_return}, "
            f"prob={self.weights.probability}, "
            f"iv={self.weights.iv_rank}, "
            f"liq={self.weights.liquidity}, "
            f"eff={self.weights.capital_efficiency}, "
            f"safety={self.weights.safety_buffer}"
        )

    def score_candidate(self, candidate: BarchartCandidate) -> ScoredCandidate:
        """Score a single candidate.

        Args:
            candidate: BarchartCandidate to score

        Returns:
            ScoredCandidate with all scores calculated
        """
        scored = ScoredCandidate(candidate=candidate)

        # Calculate individual dimension scores
        scored.return_score = score_risk_adjusted_return(
            candidate.annualized_return_pct, self.thresholds
        )
        scored.probability_score = score_probability(
            candidate.profit_probability, self.thresholds
        )
        scored.iv_rank_score = score_iv_rank(candidate.iv_rank, self.thresholds)
        scored.liquidity_score = score_liquidity(
            candidate.open_interest, candidate.volume, self.thresholds
        )
        scored.efficiency_score = score_capital_efficiency(
            candidate.bid, candidate.strike, self.thresholds
        )
        scored.safety_score = score_safety_buffer(
            candidate.moneyness_pct, self.thresholds
        )

        # Calculate weighted composite
        scored.composite_score = (
            scored.return_score * self.weights.risk_adjusted_return
            + scored.probability_score * self.weights.probability
            + scored.iv_rank_score * self.weights.iv_rank
            + scored.liquidity_score * self.weights.liquidity
            + scored.efficiency_score * self.weights.capital_efficiency
            + scored.safety_score * self.weights.safety_buffer
        )

        # Assign grade
        scored.grade = self._get_grade(scored.composite_score)

        return scored

    def score_all(self, candidates: list[BarchartCandidate]) -> list[ScoredCandidate]:
        """Score and rank all candidates.

        Args:
            candidates: List of BarchartCandidate objects

        Returns:
            List of ScoredCandidate sorted by composite score (descending)
        """
        logger.info(f"Scoring {len(candidates)} candidates...")

        # Score all
        scored = [self.score_candidate(c) for c in candidates]

        # Sort by composite score (descending), then by tiebreakers
        scored.sort(
            key=lambda x: (
                -x.composite_score,
                -x.probability_score,  # Tiebreaker 1: Higher probability
                -x.efficiency_score,  # Tiebreaker 2: Higher efficiency
                -x.candidate.open_interest,  # Tiebreaker 3: Higher OI
                x.candidate.dte,  # Tiebreaker 4: Lower DTE
            )
        )

        # Assign ranks
        for i, s in enumerate(scored, 1):
            s.rank = i

        logger.info(
            f"Scoring complete. Top score: {scored[0].composite_score:.1f} "
            f"({scored[0].symbol}), Bottom: {scored[-1].composite_score:.1f}"
        )

        return scored

    def apply_diversification(
        self,
        scored: list[ScoredCandidate],
        max_per_symbol: int | None = None,
    ) -> list[ScoredCandidate]:
        """Apply diversification rules to scored candidates.

        Args:
            scored: List of ScoredCandidate (should be pre-sorted by score)
            max_per_symbol: Override max positions per symbol

        Returns:
            Filtered list respecting diversification rules
        """
        max_sym = max_per_symbol or self.diversification.max_positions_per_symbol

        symbol_counts = {}
        diversified = []

        for s in scored:
            sym = s.symbol
            current_count = symbol_counts.get(sym, 0)

            if current_count < max_sym:
                symbol_counts[sym] = current_count + 1
                s.diversified_rank = len(diversified) + 1
                diversified.append(s)

        logger.info(
            f"Diversification applied: {len(diversified)} of {len(scored)} "
            f"candidates retained (max {max_sym} per symbol)"
        )

        return diversified

    def _get_grade(self, score: float) -> str:
        """Convert numeric score to letter grade.

        Args:
            score: Composite score (0-100)

        Returns:
            Letter grade (A+, A, B, C, D, F)
        """
        if score >= 85:
            return "A+"
        elif score >= 75:
            return "A"
        elif score >= 65:
            return "B"
        elif score >= 55:
            return "C"
        elif score >= 45:
            return "D"
        else:
            return "F"

    def get_scoring_metadata(self) -> dict:
        """Return scoring configuration for audit/reproducibility.

        Returns:
            Dictionary with scoring version, weights, and diversification rules
        """
        return {
            "scoring_version": self.SCORING_VERSION,
            "weights": {
                "risk_adjusted_return": self.weights.risk_adjusted_return,
                "probability": self.weights.probability,
                "iv_rank": self.weights.iv_rank,
                "liquidity": self.weights.liquidity,
                "capital_efficiency": self.weights.capital_efficiency,
                "safety_buffer": self.weights.safety_buffer,
            },
            "diversification": {
                "max_per_symbol": self.diversification.max_positions_per_symbol,
            },
        }
