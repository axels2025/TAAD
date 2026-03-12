"""Fixed fractional position sizing with VIX awareness.

Calculates the safe number of contracts based on account equity,
per-trade risk limits, and current market volatility (VIX).

The system uses the SMALLER of:
- Price-based max (e.g., 3 for stocks >$90, 5 for cheaper)
- Risk-based max (account_equity * max_risk_pct / practical_max_loss)

Then applies a configurable VIX scaling factor.

All parameters are configurable via Scan Config → Budget in the dashboard.
"""

import math
import os

from loguru import logger

# Legacy env var support (overridden by scanner_settings if present)
_ENV_MAX_RISK_PCT = float(os.getenv("MAX_RISK_PER_TRADE_PCT", "0.02"))
_ENV_VIX_HALT = float(os.getenv("VIX_HALT_THRESHOLD", "40.0"))

# Defaults (used when no BudgetSettings provided)
DEFAULT_MAX_RISK_PCT = 0.02
DEFAULT_LOSS_FACTOR = 0.25
DEFAULT_VIX_SCALING = [
    (35.0, 0.25),
    (25.0, 0.50),
    (15.0, 0.80),
    (0.0, 1.00),
]


class PositionSizer:
    """Calculate position size using fixed fractional risk management with VIX awareness.

    All parameters can be overridden via BudgetSettings from scanner_settings.yaml.

    Example:
        >>> sizer = PositionSizer(account_equity=100_000, max_risk_pct=0.05)
        >>> sizer.calculate_contracts(strike=60.0, price_based_max=5)
        3
    """

    def __init__(
        self,
        account_equity: float,
        max_risk_pct: float | None = None,
        loss_factor: float | None = None,
        vix_scaling: list[tuple[float, float]] | None = None,
        vix_halt_threshold: float | None = None,
    ):
        """Initialize position sizer.

        Args:
            account_equity: Total account equity (NetLiquidation value)
            max_risk_pct: Max risk per trade as fraction of equity
            loss_factor: Assumed max stock drop for risk calc (0.25 = 25%)
            vix_scaling: List of (vix_threshold, factor) tuples, descending
            vix_halt_threshold: VIX level that stops all new positions
        """
        self.account_equity = account_equity
        self.max_risk_pct = max_risk_pct or _ENV_MAX_RISK_PCT
        self.loss_factor = loss_factor or DEFAULT_LOSS_FACTOR
        self.vix_scaling = vix_scaling or DEFAULT_VIX_SCALING
        self.vix_halt_threshold = vix_halt_threshold or _ENV_VIX_HALT

    @classmethod
    def from_budget_settings(
        cls,
        account_equity: float,
        budget: "BudgetSettings",
    ) -> "PositionSizer":
        """Create a PositionSizer from dashboard-configurable BudgetSettings.

        Args:
            account_equity: Total account equity (NLV)
            budget: BudgetSettings from scanner_settings.yaml
        """
        vix_scaling = [
            (35.0, budget.vix_scale_extreme),
            (25.0, budget.vix_scale_elevated),
            (15.0, budget.vix_scale_normal),
            (0.0, 1.00),
        ]
        return cls(
            account_equity=account_equity,
            max_risk_pct=budget.risk_per_trade_pct,
            loss_factor=budget.loss_assumption_pct,
            vix_scaling=vix_scaling,
        )

    def get_vix_scaling_factor(self, vix: float) -> float:
        """Get position sizing scaling factor based on VIX level.

        Args:
            vix: Current VIX value

        Returns:
            Scaling factor (0.25 to 1.0)
        """
        for threshold, factor in self.vix_scaling:
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
        if vix is not None and vix >= self.vix_halt_threshold:
            logger.warning(
                f"VIX={vix:.1f} >= halt threshold {self.vix_halt_threshold:.0f} "
                f"— no new positions allowed"
            )
            return 0

        max_risk_amount = self.account_equity * self.max_risk_pct
        practical_max_loss = strike * self.loss_factor * 100

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
            f"vix_factor={vix_factor:.0%}, risk_pct={self.max_risk_pct:.1%}, "
            f"loss_assumption={self.loss_factor:.0%})"
        )

        return final
