"""Baseline naked put strategy configuration.

This module defines position management and exit rules for the trading strategy.

IMPORTANT: Entry criteria (OTM range, premium range, DTE, etc.) have been moved to
naked_put_options_config.py and are now handled by the Barchart + IBKR workflow.

This file now only contains:
- Position sizing rules
- Exit criteria (profit target, stop loss, time exit)
- Risk management parameters
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings


class PositionSettings(BaseSettings):
    """Position management settings loaded from environment variables."""

    # Position Sizing
    position_size: int = Field(default=5, ge=1, le=100)
    max_positions: int = Field(default=10, ge=1, le=100)

    # Exit Rules
    profit_target: float = Field(default=0.50, ge=0.0, le=1.0)
    stop_loss: float = Field(default=-2.00, le=0.0)
    time_exit_dte: int = Field(default=2, ge=0, le=14)

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


class ExitRules(BaseModel):
    """Exit rules for the naked put strategy."""

    profit_target: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Exit at 50% of max profit (0.50 = 50%)",
    )
    stop_loss: float = Field(
        default=-2.00,
        le=0.0,
        description="Stop loss at -200% of premium received",
    )
    time_exit_dte: int = Field(
        default=2, ge=0, le=14, description="Exit N days before expiration"
    )


class BaselineStrategy(BaseModel):
    """Position management and exit rules for naked put strategy.

    This configuration handles:
    - Position sizing (how many contracts per trade)
    - Maximum concurrent positions
    - Exit rules (when to close trades)
    - Risk management per trade

    Entry criteria (what trades to take) are now handled by:
    - Barchart API screening (fast market scan)
    - IBKR validation (real-time verification)
    See: src/config/naked_put_options_config.py

    Example:
        >>> strategy = BaselineStrategy.from_env()
        >>> print(strategy.position_size)
        5
        >>> print(strategy.exit_rules.profit_target)
        0.50
    """

    name: str = Field(
        default="Naked Put - Weekly", description="Strategy display name"
    )

    option_type: Literal["PUT", "CALL"] = Field(
        default="PUT", description="Option type to trade"
    )

    # Position sizing (loaded from env)
    position_size: int = Field(
        default=5, ge=1, le=100, description="Number of contracts per trade"
    )

    max_positions: int = Field(
        default=10, ge=1, le=100, description="Maximum concurrent open positions"
    )

    # Exit rules (loaded from env)
    exit_rules: ExitRules = Field(
        default_factory=ExitRules, description="Exit criteria for trades"
    )

    # Risk management — actively enforced by PositionSizer in strike_finder
    max_risk_per_trade_pct: float = Field(
        default=0.02,
        ge=0.001,
        le=0.10,
        description="Maximum risk per trade as % of portfolio (enforced via position sizing)",
    )

    # Legacy fields (kept for backward compatibility with trade command / NakedPutStrategy)
    # The new scan command uses Barchart + IBKR instead
    # These are optional and only loaded if present in environment
    otm_range: tuple[float, Optional[float]] = Field(
        default=(0.10, 0.30),
        description="DEPRECATED: Use Barchart config instead. Out-of-the-money range for legacy trade command",
    )
    premium_range: tuple[float, Optional[float]] = Field(
        default=(0.20, 2.00),
        description="DEPRECATED: Use Barchart config instead. Premium range for legacy trade command",
    )
    dte_range: tuple[int, Optional[int]] = Field(
        default=(0, 30),
        description="DEPRECATED: Use Barchart config instead. DTE range for legacy trade command",
    )
    trend_filter: Literal["uptrend", "downtrend", "any", "sideways"] = Field(
        default="uptrend",
        description="DEPRECATED: Use Barchart config instead. Trend filter for legacy trade command",
    )
    min_stock_price: float = Field(
        default=30.0,
        description="DEPRECATED: Use Barchart config instead. Min stock price for legacy trade command",
    )
    max_stock_price: Optional[float] = Field(
        default=250.0,
        description="DEPRECATED: Use Barchart config instead. Max stock price for legacy trade command",
    )
    min_daily_volume: int = Field(
        default=1_000_000,
        ge=100_000,
        description="DEPRECATED: Minimum average daily volume for legacy trade command",
    )

    @classmethod
    def from_env(cls) -> "BaselineStrategy":
        """Create BaselineStrategy from environment variables.

        Returns:
            BaselineStrategy: Strategy configured from .env file

        Example:
            >>> strategy = BaselineStrategy.from_env()
            >>> print(strategy.position_size)
            5
        """
        settings = PositionSettings()

        return cls(
            position_size=settings.position_size,
            max_positions=settings.max_positions,
            exit_rules=ExitRules(
                profit_target=settings.profit_target,
                stop_loss=settings.stop_loss,
                time_exit_dte=settings.time_exit_dte,
            ),
        )

    def should_exit_profit_target(
        self, entry_premium: float, current_premium: float
    ) -> bool:
        """Check if profit target has been reached.

        Args:
            entry_premium: Premium received at entry
            current_premium: Current premium to buy back

        Returns:
            bool: True if profit target reached

        Example:
            >>> strategy = BaselineStrategy()
            >>> strategy.should_exit_profit_target(0.50, 0.25)
            True
        """
        profit_pct = (entry_premium - current_premium) / entry_premium
        return profit_pct >= self.exit_rules.profit_target

    def should_exit_stop_loss(
        self, entry_premium: float, current_premium: float
    ) -> bool:
        """Check if stop loss has been hit.

        Args:
            entry_premium: Premium received at entry
            current_premium: Current premium to buy back

        Returns:
            bool: True if stop loss triggered

        Example:
            >>> strategy = BaselineStrategy()
            >>> strategy.should_exit_stop_loss(0.30, 0.90)
            True
        """
        loss_multiple = (current_premium - entry_premium) / entry_premium
        return loss_multiple >= abs(self.exit_rules.stop_loss)

    def should_exit_time(self, current_dte: int) -> bool:
        """Check if time exit should trigger.

        Args:
            current_dte: Current days to expiration

        Returns:
            bool: True if time exit should trigger

        Example:
            >>> strategy = BaselineStrategy()
            >>> strategy.should_exit_time(2)
            True
        """
        return current_dte <= self.exit_rules.time_exit_dte

    def validate_opportunity_with_reason(self, opportunity: dict) -> tuple[bool, str]:
        """Validate opportunity and return detailed reason if rejected.

        Args:
            opportunity: Dictionary with keys: otm_pct, premium, dte, trend, etc.

        Returns:
            Tuple of (is_valid, rejection_reason)
            - is_valid: True if passes all criteria
            - rejection_reason: Empty string if valid, otherwise explains why rejected

        Example:
            >>> strategy = BaselineStrategy()
            >>> opp = {"otm_pct": 0.05, "premium": 0.45, "dte": 10}
            >>> valid, reason = strategy.validate_opportunity_with_reason(opp)
            >>> print(reason)
            "OTM 5.0% below minimum 10.0%"
        """
        # Get values, with defaults
        otm_pct = opportunity.get("otm_pct") or 0
        premium = opportunity.get("premium") or 0
        dte = opportunity.get("dte") or 0
        trend = opportunity.get("trend", "unknown")

        # Calculate OTM on the fly if missing and we have stock_price + strike
        if otm_pct == 0 and opportunity.get("stock_price") and opportunity.get("strike"):
            stock_price = opportunity["stock_price"]
            strike = opportunity["strike"]
            option_type = opportunity.get("option_type", "PUT").upper()

            if option_type == "PUT":
                # For puts: OTM when stock price > strike
                otm_pct = (stock_price - strike) / stock_price if stock_price > 0 else 0
            else:
                # For calls: OTM when strike > stock price
                otm_pct = (strike - stock_price) / stock_price if stock_price > 0 else 0

            # Ensure it's positive (can't be negative OTM)
            otm_pct = max(0, otm_pct)

        # Check OTM range
        if otm_pct < self.otm_range[0]:
            return False, f"OTM {otm_pct*100:.1f}% below minimum {self.otm_range[0]*100:.1f}%"
        if self.otm_range[1] is not None and otm_pct > self.otm_range[1]:
            return False, f"OTM {otm_pct*100:.1f}% above maximum {self.otm_range[1]*100:.1f}%"

        # Check premium range
        if premium < self.premium_range[0]:
            return False, f"Premium ${premium:.2f} below minimum ${self.premium_range[0]:.2f}"
        if self.premium_range[1] is not None and premium > self.premium_range[1]:
            return False, f"Premium ${premium:.2f} above maximum ${self.premium_range[1]:.2f}"

        # Check DTE range
        if dte < self.dte_range[0]:
            return False, f"DTE {dte} below minimum {self.dte_range[0]}"
        if self.dte_range[1] is not None and dte > self.dte_range[1]:
            return False, f"DTE {dte} above maximum {self.dte_range[1]}"

        # Check trend filter
        if self.trend_filter != "any" and trend != self.trend_filter:
            return False, f"Trend '{trend}' does not match required '{self.trend_filter}'"

        return True, ""

    def validate_opportunity(self, opportunity: dict) -> bool:
        """Validate if an opportunity matches strategy criteria.

        DEPRECATED: This method is for backward compatibility with the
        legacy trade command. The new scan command uses Barchart + IBKR
        validation instead.

        Args:
            opportunity: Dictionary with keys: otm_pct, premium, dte, trend, etc.

        Returns:
            bool: True if opportunity matches all criteria

        Example:
            >>> strategy = BaselineStrategy()
            >>> opp = {
            ...     "otm_pct": 0.18,
            ...     "premium": 0.45,
            ...     "dte": 10,
            ...     "trend": "uptrend"
            ... }
            >>> strategy.validate_opportunity(opp)
            True
        """
        is_valid, _ = self.validate_opportunity_with_reason(opportunity)
        return is_valid


# Global baseline strategy instance
_baseline_strategy: BaselineStrategy | None = None


def get_baseline_strategy() -> BaselineStrategy:
    """Get the global baseline strategy instance loaded from .env.

    Returns:
        BaselineStrategy: The baseline strategy configuration from environment

    Example:
        >>> strategy = get_baseline_strategy()
        >>> print(strategy.position_size)
        5
    """
    global _baseline_strategy
    if _baseline_strategy is None:
        _baseline_strategy = BaselineStrategy.from_env()
    return _baseline_strategy
