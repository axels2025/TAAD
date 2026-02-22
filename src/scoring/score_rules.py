"""Individual dimension scoring functions.

Each function returns a score from 0-100 based on the research-backed
criteria defined in naked_put_ranking_rules_v2.md.
"""

from src.scoring.score_config import ScoreThresholds


def score_risk_adjusted_return(
    ann_return: float, thresholds: ScoreThresholds | None = None
) -> float:
    """Score annualized return. Sweet spot is 30-50%.

    Args:
        ann_return: Annualized return as decimal (0.35 = 35%)
        thresholds: Optional custom thresholds (reserved for future use)

    Returns:
        Score from 0-100
    """
    # thresholds parameter reserved for future customization
    pct = ann_return * 100  # Convert to percentage for readability

    # Sweet spot: 30-50%
    if 30 <= pct <= 50:
        return 100.0
    elif 50 < pct <= 75:
        return 90.0
    elif 25 <= pct < 30:
        return 85.0
    elif 75 < pct < 100:
        return 75.0
    elif 20 <= pct < 25:
        return 70.0
    elif 100 <= pct <= 150:
        return 60.0  # Getting risky
    elif 15 <= pct < 20:
        return 50.0
    elif 150 < pct <= 200:
        return 40.0  # Red flag territory
    elif 10 <= pct < 15:
        return 30.0
    elif pct > 200:
        return 20.0  # Almost certainly hidden risk
    else:  # < 10%
        return 10.0


def score_probability(
    profit_prob: float, thresholds: ScoreThresholds | None = None
) -> float:
    """Score profit probability. Higher is better, target 85%+.

    Args:
        profit_prob: Probability of profit as decimal (0.85 = 85%)
        thresholds: Optional custom thresholds (reserved for future use)

    Returns:
        Score from 0-100
    """
    # thresholds parameter reserved for future customization
    pct = profit_prob * 100

    if pct >= 90:
        return 100.0
    elif pct >= 85:
        return 90.0
    elif pct >= 80:
        return 80.0
    elif pct >= 75:
        return 65.0
    elif pct >= 70:
        return 50.0
    elif pct >= 65:
        return 35.0
    elif pct >= 60:
        return 20.0
    else:
        return 10.0


def score_iv_rank(iv_rank: float, thresholds: ScoreThresholds | None = None) -> float:
    """Score IV Rank. Optimal is 60-80% (rich premium, likely contraction).

    Args:
        iv_rank: IV Rank as decimal (0.72 = 72%)
        thresholds: Optional custom thresholds (reserved for future use)

    Returns:
        Score from 0-100
    """
    # thresholds parameter reserved for future customization
    pct = iv_rank * 100

    # Optimal: 60-80%
    if 60 <= pct <= 80:
        return 100.0
    elif pct > 80:
        return 90.0  # Very high IV - good premium but watch for events
    elif 50 <= pct < 60:
        return 85.0
    elif 40 <= pct < 50:
        return 70.0
    elif 30 <= pct < 40:
        return 50.0
    elif 20 <= pct < 30:
        return 30.0
    else:  # < 20%
        return 15.0  # Poor selling environment


def score_liquidity(
    open_interest: int, volume: int, thresholds: ScoreThresholds | None = None
) -> float:
    """Score liquidity based on OI and Volume. Uses minimum of both scores.

    Args:
        open_interest: Open interest
        volume: Daily volume
        thresholds: Optional custom thresholds

    Returns:
        Score from 0-100 (minimum of OI and Volume scores)
    """
    t = thresholds or ScoreThresholds()

    # Score Open Interest
    if open_interest >= t.oi_excellent:
        oi_score = 100.0
    elif open_interest >= 3000:
        oi_score = 90.0
    elif open_interest >= t.oi_good:
        oi_score = 80.0
    elif open_interest >= t.oi_adequate:
        oi_score = 65.0
    elif open_interest >= 500:
        oi_score = 45.0
    elif open_interest >= t.oi_minimum:
        oi_score = 25.0
    else:
        oi_score = 10.0

    # Score Volume
    if volume >= t.volume_excellent:
        vol_score = 100.0
    elif volume >= 300:
        vol_score = 90.0
    elif volume >= t.volume_good:
        vol_score = 80.0
    elif volume >= t.volume_adequate:
        vol_score = 65.0
    elif volume >= 50:
        vol_score = 45.0
    elif volume >= t.volume_minimum:
        vol_score = 25.0
    else:
        vol_score = 10.0

    # Return minimum - both dimensions must be adequate
    return min(oi_score, vol_score)


def score_capital_efficiency(
    bid: float, strike: float, thresholds: ScoreThresholds | None = None
) -> float:
    """Score capital efficiency (premium return %).

    Note: This is a proxy. True margin efficiency requires IBKR margin data.

    Args:
        bid: Option bid price
        strike: Strike price
        thresholds: Optional custom thresholds (reserved for future use)

    Returns:
        Score from 0-100
    """
    # thresholds parameter reserved for future customization
    return_pct = (bid / strike) * 100 if strike > 0 else 0

    if return_pct >= 3.0:
        return 100.0
    elif return_pct >= 2.5:
        return 90.0
    elif return_pct >= 2.0:
        return 80.0
    elif return_pct >= 1.5:
        return 65.0
    elif return_pct >= 1.0:
        return 50.0
    elif return_pct >= 0.5:
        return 30.0
    else:
        return 15.0


def score_safety_buffer(
    moneyness_pct: float, thresholds: ScoreThresholds | None = None
) -> float:
    """Score safety buffer (OTM distance). Optimal is 12-18% OTM.

    Args:
        moneyness_pct: OTM percentage as decimal (negative from Barchart)
        thresholds: Optional custom thresholds (reserved for future use)

    Returns:
        Score from 0-100
    """
    # thresholds parameter reserved for future customization
    # Convert to positive OTM percentage
    otm = abs(moneyness_pct) * 100

    # Optimal: 12-18% OTM
    if 12 <= otm <= 18:
        return 100.0
    elif 18 < otm <= 22:
        return 90.0
    elif 10 <= otm < 12:
        return 85.0
    elif 22 < otm <= 28:
        return 75.0
    elif 8 <= otm < 10:
        return 60.0
    elif 5 <= otm < 8:
        return 40.0
    elif otm > 28:
        return 30.0  # Too far - minimal premium
    else:  # < 5%
        return 15.0  # Too close to ATM
