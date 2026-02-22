"""Scoring configuration with tunable weights and thresholds."""

from dataclasses import dataclass


@dataclass
class ScoreWeights:
    """Weights for composite score calculation.

    All weights must sum to 1.0 for proper normalization.

    Updated weights prioritize capital efficiency and IV rank:
    - Capital efficiency (25%): Maximize premium/margin ratio
    - IV rank (20%): Elevated volatility for better premiums
    - Probability (20%): High success rate
    - Return (15%): Annualized return
    - Safety (15%): OTM buffer
    - Liquidity (5%): Assumes most options sufficiently liquid
    """

    capital_efficiency: float = 0.25
    iv_rank: float = 0.20
    probability: float = 0.20
    risk_adjusted_return: float = 0.15
    safety_buffer: float = 0.15
    liquidity: float = 0.05

    def validate(self) -> bool:
        """Ensure weights sum to 1.0.

        Returns:
            True if weights sum to 1.0 (within tolerance)
        """
        total = (
            self.risk_adjusted_return
            + self.probability
            + self.iv_rank
            + self.liquidity
            + self.capital_efficiency
            + self.safety_buffer
        )
        return abs(total - 1.0) < 0.001


@dataclass
class ScoreThresholds:
    """Thresholds for individual dimension scoring."""

    # Annualized return thresholds (as decimal, e.g., 0.30 = 30%)
    ann_return_optimal: tuple[float, float] = (0.30, 0.50)
    ann_return_good: tuple[float, float] = (0.25, 0.75)
    ann_return_red_flag: float = 1.50  # >150% is suspicious

    # Probability thresholds
    prob_excellent: float = 0.90
    prob_good: float = 0.85
    prob_acceptable: float = 0.70

    # IV Rank thresholds
    iv_rank_optimal: tuple[float, float] = (0.60, 0.80)
    iv_rank_good: tuple[float, float] = (0.50, 0.90)

    # Liquidity minimums
    oi_excellent: int = 5000
    oi_good: int = 2000
    oi_adequate: int = 1000
    oi_minimum: int = 250
    volume_excellent: int = 500
    volume_good: int = 200
    volume_adequate: int = 100
    volume_minimum: int = 25

    # Capital efficiency (return %)
    efficiency_excellent: float = 0.03
    efficiency_good: float = 0.02
    efficiency_acceptable: float = 0.01

    # Safety buffer (OTM %)
    otm_optimal: tuple[float, float] = (0.12, 0.18)
    otm_good: tuple[float, float] = (0.10, 0.22)
    otm_too_close: float = 0.05
    otm_too_far: float = 0.28


@dataclass
class DiversificationRules:
    """Rules for portfolio diversification."""

    max_positions_per_symbol: int = 3
    max_same_expiration_pct: float = 0.50  # Max 50% in same expiration
    max_sector_concentration_pct: float = 0.40  # Future: sector tracking


# Default configuration
DEFAULT_WEIGHTS = ScoreWeights()
DEFAULT_THRESHOLDS = ScoreThresholds()
DEFAULT_DIVERSIFICATION = DiversificationRules()
