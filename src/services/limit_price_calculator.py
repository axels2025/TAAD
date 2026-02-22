"""Centralized limit price calculation for option selling.

This module provides the LimitPriceCalculator class that handles all
limit price calculations for selling options throughout the workflow.

The limit price is calculated between the bid and mid price, closer to
the bid. This provides a better-than-bid price while still having a
reasonable chance of being filled.
"""

import os
from dataclasses import dataclass

from loguru import logger


@dataclass
class LimitPriceConfig:
    """Configuration for limit price calculation.

    All values loaded from environment variables with sensible defaults.

    Attributes:
        bid_mid_ratio: Ratio between bid and mid (0.0=bid, 1.0=mid, default 0.3)
        adjustment_increment: Amount to decrease limit per adjustment (default $0.01)
        max_adjustments: Maximum number of price adjustments allowed (default 2)
        min_premium: Minimum acceptable premium after adjustments (default $0.20)
    """

    bid_mid_ratio: float = 0.3
    adjustment_increment: float = 0.01
    max_adjustments: int = 2
    min_premium: float = 0.20

    @classmethod
    def from_env(cls) -> "LimitPriceConfig":
        """Load configuration from the central Config singleton.

        Shared values (price adjustments, premium floor) come from
        ``get_config()`` so there is one source of truth.
        ``bid_mid_ratio`` stays as ``os.getenv`` since it is only used here.

        Returns:
            LimitPriceConfig instance
        """
        from src.config.base import get_config

        cfg = get_config()
        return cls(
            bid_mid_ratio=float(os.getenv("LIMIT_BID_MID_RATIO", "0.3")),
            adjustment_increment=cfg.price_adjustment_increment,
            max_adjustments=cfg.max_price_adjustments,
            min_premium=cfg.premium_floor,
        )


class LimitPriceCalculator:
    """Calculate optimal limit prices for selling options.

    This class centralizes all limit price logic used throughout the
    Sunday-to-Monday trading workflow. It calculates prices between
    the bid and mid, and handles price adjustments for fill improvement.

    Example:
        >>> calc = LimitPriceCalculator()
        >>> limit = calc.calculate_sell_limit(bid=0.45, ask=0.55)
        >>> print(limit)  # 0.47 (30% of the way from bid to mid)

        >>> # Adjust if not filled
        >>> new_limit = calc.adjust_limit_for_fill(limit, current_bid=0.44)
        >>> print(new_limit)  # 0.46
    """

    def __init__(self, config: LimitPriceConfig | None = None):
        """Initialize the calculator.

        Args:
            config: Optional configuration. If None, loads from environment.
        """
        self.config = config or LimitPriceConfig.from_env()

        logger.debug(
            f"LimitPriceCalculator initialized: "
            f"ratio={self.config.bid_mid_ratio}, "
            f"increment=${self.config.adjustment_increment}, "
            f"max_adjustments={self.config.max_adjustments}"
        )

    def calculate_sell_limit(self, bid: float, ask: float) -> float:
        """Calculate sell limit price between bid and mid.

        The formula is: bid + (mid - bid) * ratio

        This gives a price that's better than the bid (so we get more premium)
        but still has a good chance of being filled since it's closer to bid
        than to mid.

        Args:
            bid: Current bid price for the option
            ask: Current ask price for the option

        Returns:
            Calculated limit price, rounded to $0.01, never below bid

        Raises:
            ValueError: If bid > ask (invalid spread)

        Example:
            >>> calc = LimitPriceCalculator()
            >>> calc.calculate_sell_limit(0.45, 0.55)
            0.47  # With default ratio of 0.3
        """
        if bid > ask:
            raise ValueError(f"Invalid spread: bid ({bid}) > ask ({ask})")

        if bid <= 0:
            logger.warning(f"Zero or negative bid: {bid}")
            return 0.0

        mid = (bid + ask) / 2
        limit = bid + (mid - bid) * self.config.bid_mid_ratio

        # Round to penny and ensure not below bid
        result = max(round(limit, 2), round(bid, 2))

        logger.debug(
            f"Limit calculation: bid=${bid:.2f}, ask=${ask:.2f}, "
            f"mid=${mid:.2f}, ratio={self.config.bid_mid_ratio} → ${result:.2f}"
        )

        return result

    def adjust_limit_for_fill(
        self,
        current_limit: float,
        current_bid: float,
        adjustment_number: int = 1,
    ) -> float | None:
        """Adjust limit price down to improve fill probability.

        Called when the initial limit order didn't fill within the wait
        period. Decreases the limit by the configured increment but never
        goes below the current bid.

        Args:
            current_limit: The current unfilled limit price
            current_bid: The current market bid price
            adjustment_number: Which adjustment this is (1, 2, etc.)

        Returns:
            New adjusted limit price, or None if:
            - Max adjustments exceeded
            - Would go below minimum premium threshold
            - Would go below current bid

        Example:
            >>> calc = LimitPriceCalculator()
            >>> calc.adjust_limit_for_fill(0.47, 0.45, adjustment_number=1)
            0.46  # Decreased by $0.01
            >>> calc.adjust_limit_for_fill(0.46, 0.45, adjustment_number=2)
            0.45  # Decreased to bid
            >>> calc.adjust_limit_for_fill(0.45, 0.45, adjustment_number=3)
            None  # Max adjustments exceeded
        """
        # Check if we've exceeded max adjustments
        if adjustment_number > self.config.max_adjustments:
            logger.info(
                f"Max adjustments ({self.config.max_adjustments}) exceeded, "
                "leaving order working"
            )
            return None

        # Calculate new limit
        adjusted = current_limit - self.config.adjustment_increment
        adjusted = round(adjusted, 2)

        # Never go below current bid
        if adjusted < current_bid:
            adjusted = round(current_bid, 2)

        # Check minimum premium threshold
        if adjusted < self.config.min_premium:
            logger.warning(
                f"Adjusted limit ${adjusted:.2f} below minimum premium "
                f"${self.config.min_premium:.2f}, rejecting adjustment"
            )
            return None

        logger.info(
            f"Adjustment #{adjustment_number}: "
            f"${current_limit:.2f} → ${adjusted:.2f} "
            f"(bid=${current_bid:.2f})"
        )

        return adjusted

    def calculate_premium_income(
        self,
        limit_price: float,
        contracts: int,
    ) -> float:
        """Calculate expected premium income from a trade.

        Args:
            limit_price: The limit price per share
            contracts: Number of option contracts

        Returns:
            Total premium income in dollars (limit * 100 * contracts)
        """
        return limit_price * 100 * contracts

    def validate_limit_vs_bid(
        self,
        limit_price: float,
        current_bid: float,
        tolerance: float = 0.10,
    ) -> bool:
        """Validate that limit price is still reasonable vs current bid.

        Used during market-open validation (Stage 2) to ensure the staged
        limit price hasn't become unrealistic due to market movement.

        Args:
            limit_price: The staged limit price
            current_bid: The current market bid
            tolerance: Maximum percentage the limit can exceed bid (default 10%)

        Returns:
            True if limit is reasonable, False if too far from bid

        Example:
            >>> calc = LimitPriceCalculator()
            >>> calc.validate_limit_vs_bid(0.50, 0.45)  # 11% above bid
            False  # Exceeds 10% tolerance
            >>> calc.validate_limit_vs_bid(0.48, 0.45)  # 6.7% above bid
            True
        """
        if current_bid <= 0:
            return False

        deviation = (limit_price - current_bid) / current_bid

        if deviation > tolerance:
            logger.warning(
                f"Limit ${limit_price:.2f} is {deviation:.1%} above bid "
                f"${current_bid:.2f} (tolerance: {tolerance:.0%})"
            )
            return False

        return True

    def recalculate_from_fresh_quotes(
        self,
        bid: float,
        ask: float,
        original_limit: float | None = None,
    ) -> tuple[float, str]:
        """Recalculate limit price from fresh market quotes.

        Used during market-open validation when we need to update the
        limit price based on current market conditions.

        Args:
            bid: Fresh bid price
            ask: Fresh ask price
            original_limit: Original staged limit (for comparison)

        Returns:
            Tuple of (new_limit_price, reason_string)

        Example:
            >>> calc = LimitPriceCalculator()
            >>> new_limit, reason = calc.recalculate_from_fresh_quotes(
            ...     bid=0.42, ask=0.48, original_limit=0.45
            ... )
            >>> print(new_limit, reason)
            0.44 "Recalculated from fresh quotes (was $0.45)"
        """
        new_limit = self.calculate_sell_limit(bid, ask)

        if original_limit is not None:
            change_pct = (new_limit - original_limit) / original_limit * 100
            reason = (
                f"Recalculated from fresh quotes "
                f"(was ${original_limit:.2f}, "
                f"change: {change_pct:+.1f}%)"
            )
        else:
            reason = "Calculated from fresh quotes"

        return new_limit, reason


# Module-level convenience function
def calculate_limit_price(bid: float, ask: float) -> float:
    """Calculate limit price using default configuration.

    Convenience function for simple use cases. For more control,
    instantiate LimitPriceCalculator directly.

    Args:
        bid: Current bid price
        ask: Current ask price

    Returns:
        Calculated limit price
    """
    return LimitPriceCalculator().calculate_sell_limit(bid, ask)
