"""Fixed fractional position sizing with VIX awareness.

Calculates the safe number of contracts based on account equity,
per-trade risk limits, and current market volatility (VIX).

The system uses the SMALLER of:
- Price-based max (e.g., 3 for stocks >$90, 5 for cheaper)
- Risk-based max (account_equity * max_risk_pct / practical_max_loss)

Then applies a VIX scaling factor:
- VIX < 15 (low vol): 1.0x (normal sizing)
- VIX 15-25 (normal): 0.8x (reduce 20%)
- VIX 25-35 (elevated): 0.5x (reduce 50%)
- VIX > 35 (extreme): 0.25x (reduce 75%)
"""

import math
import os

from loguru import logger

# 25% stock drop = approximate margin call level for naked puts
PRACTICAL_LOSS_FACTOR = 0.25

DEFAULT_MAX_RISK_PCT = 0.02  # 2% of account equity per trade

# VIX scaling thresholds
VIX_SCALING = [
    (35.0, 0.25),  # Extreme: 75% reduction
    (25.0, 0.50),  # Elevated: 50% reduction
    (15.0, 0.80),  # Normal: 20% reduction
    (0.0, 1.00),   # Low vol: full sizing
]

VIX_HALT_THRESHOLD = float(os.getenv("VIX_HALT_THRESHOLD", "40.0"))


class PositionSizer:
    """Calculate position size using fixed fractional risk management with VIX awareness.

    Uses the formula:
        max_risk_amount = account_equity * max_risk_per_trade_pct
        practical_max_loss = strike * 0.25 * 100  (25% stock drop per contract)
        risk_based_contracts = floor(max_risk_amount / practical_max_loss)
        final = min(risk_based_contracts, price_based_max) * vix_factor, minimum 1

    Example:
        >>> sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.02)
        >>> sizer.calculate_contracts(strike=500.0, price_based_max=3)
        1
        >>> sizer.calculate_contracts(strike=30.0, price_based_max=5, vix=30.0)
        1
    """

    def __init__(
        self,
        account_equity: float,
        max_risk_pct: float | None = None,
    ):
        """Initialize position sizer.

        Args:
            account_equity: Total account equity (NetLiquidation value)
            max_risk_pct: Max risk per trade as fraction of equity (default from env or 0.02)
        """
        self.account_equity = account_equity
        self.max_risk_pct = max_risk_pct or float(
            os.getenv("MAX_RISK_PER_TRADE_PCT", str(DEFAULT_MAX_RISK_PCT))
        )

    @staticmethod
    def get_vix_scaling_factor(vix: float) -> float:
        """Get position sizing scaling factor based on VIX level.

        Args:
            vix: Current VIX value

        Returns:
            Scaling factor (0.25 to 1.0)
        """
        for threshold, factor in VIX_SCALING:
            if vix >= threshold:
                return factor
        return 1.0

    def calculate_contracts(
        self,
        strike: float,
        price_based_max: int,
        vix: float | None = None,
    ) -> int:
        """Calculate the safe number of contracts for a trade.

        Returns min(price_based_max, risk_based_contracts) * vix_factor, minimum 1.
        If VIX exceeds halt threshold, returns 0 (no new positions).

        Args:
            strike: Option strike price
            price_based_max: Maximum contracts from price-based rule
            vix: Current VIX level (None = no VIX adjustment)

        Returns:
            Number of contracts to trade (0 if VIX halts, else >= 1)
        """
        # VIX halt check
        if vix is not None and vix >= VIX_HALT_THRESHOLD:
            logger.warning(
                f"VIX={vix:.1f} >= halt threshold {VIX_HALT_THRESHOLD:.0f} "
                f"— no new positions allowed"
            )
            return 0

        max_risk_amount = self.account_equity * self.max_risk_pct
        practical_max_loss = strike * PRACTICAL_LOSS_FACTOR * 100

        if practical_max_loss <= 0:
            logger.warning(f"Invalid strike {strike}, using price-based max {price_based_max}")
            return max(1, price_based_max)

        risk_based = math.floor(max_risk_amount / practical_max_loss)
        final = min(risk_based, price_based_max)
        final = max(1, final)  # Always trade at least 1 contract

        # Apply VIX scaling
        vix_factor = 1.0
        if vix is not None:
            vix_factor = self.get_vix_scaling_factor(vix)
            if vix_factor < 1.0:
                scaled = max(1, math.floor(final * vix_factor))
                logger.info(
                    f"VIX={vix:.1f}, scaling={vix_factor:.0%}: "
                    f"{final} → {scaled} contracts"
                )
                final = scaled

        logger.info(
            f"Position size: {final} contracts "
            f"(fixed={price_based_max}, risk-based={risk_based}, "
            f"vix_factor={vix_factor:.0%})"
        )

        return final
