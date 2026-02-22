"""Price deviation validation for manual trades and pre-market opportunities.

This module validates that opportunities haven't experienced excessive price
movement since creation, which could indicate stale data or changed market conditions.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger


@dataclass
class PriceDeviationCheck:
    """Result of a price deviation check.

    Attributes:
        passed: Whether the check passed
        current_price: Current underlying stock price
        original_price: Price when opportunity was created
        deviation_pct: Percentage deviation (positive = moved up)
        deviation_amount: Absolute dollar deviation
        limit_pct: Maximum allowed deviation percentage
        message: Human-readable explanation
        warning: Optional warning message
    """

    passed: bool
    current_price: float
    original_price: float
    deviation_pct: float
    deviation_amount: float
    limit_pct: float
    message: str
    warning: Optional[str] = None


@dataclass
class StalenessCheck:
    """Result of a staleness check.

    Attributes:
        passed: Whether the check passed
        age_hours: Hours since opportunity was created
        limit_hours: Maximum allowed age in hours
        created_at: When opportunity was created
        checked_at: When check was performed
        message: Human-readable explanation
    """

    passed: bool
    age_hours: float
    limit_hours: float
    created_at: datetime
    checked_at: datetime
    message: str


class PriceDeviationValidator:
    """Validates price movement since opportunity creation.

    This validator checks if the underlying stock price has moved too much
    since an opportunity was identified, which could indicate:
    - Stale manual trade data
    - Market regime change
    - News/events affecting the stock
    """

    def __init__(
        self,
        max_deviation_pct: float = 0.03,  # 3% default
        manual_staleness_hours: float = 24.0,
    ):
        """Initialize price deviation validator.

        Args:
            max_deviation_pct: Maximum allowed price deviation (e.g., 0.03 = 3%)
            manual_staleness_hours: Maximum age for manual trades (hours)
        """
        self.max_deviation_pct = max_deviation_pct
        self.manual_staleness_hours = manual_staleness_hours

    def check_deviation(
        self,
        current_price: float,
        original_price: float,
        max_deviation_pct: Optional[float] = None,
    ) -> PriceDeviationCheck:
        """Check if price deviation is within acceptable limits.

        Args:
            current_price: Current underlying stock price
            original_price: Price when opportunity was created
            max_deviation_pct: Override default max deviation (optional)

        Returns:
            PriceDeviationCheck result
        """
        if max_deviation_pct is None:
            max_deviation_pct = self.max_deviation_pct

        # Calculate deviation
        deviation_amount = current_price - original_price
        deviation_pct = deviation_amount / original_price if original_price > 0 else 0.0

        # Check if within limits (use small epsilon for floating point comparison)
        abs_deviation_pct = abs(deviation_pct)
        # Allow for floating point precision issues
        passed = (
            abs_deviation_pct <= max_deviation_pct
            or abs(abs_deviation_pct - max_deviation_pct) < 1e-10
        )

        # Format message
        direction = "up" if deviation_pct > 0 else "down"
        if passed:
            if abs_deviation_pct < 0.01:  # Less than 1%
                message = f"Price stable: {abs_deviation_pct:.2%} deviation"
                warning = None
            elif abs_deviation_pct < max_deviation_pct * 0.8:
                message = f"Price acceptable: {abs_deviation_pct:.2%} {direction}"
                warning = None
            else:
                message = f"Price near limit: {abs_deviation_pct:.2%} {direction}"
                warning = f"Approaching max deviation of {max_deviation_pct:.2%}"
        else:
            message = (
                f"Price moved too much: {abs_deviation_pct:.2%} {direction}, "
                f"exceeds limit of {max_deviation_pct:.2%}"
            )
            warning = None

        logger.debug(
            f"Price deviation check: {message}",
            extra={
                "current_price": current_price,
                "original_price": original_price,
                "deviation_pct": deviation_pct,
                "limit_pct": max_deviation_pct,
                "passed": passed,
            },
        )

        return PriceDeviationCheck(
            passed=passed,
            current_price=current_price,
            original_price=original_price,
            deviation_pct=deviation_pct,
            deviation_amount=deviation_amount,
            limit_pct=max_deviation_pct,
            message=message,
            warning=warning,
        )

    def check_staleness(
        self,
        created_at: datetime,
        checked_at: Optional[datetime] = None,
        max_age_hours: Optional[float] = None,
    ) -> StalenessCheck:
        """Check if opportunity is too old.

        Args:
            created_at: When opportunity was created
            checked_at: When to check (defaults to now)
            max_age_hours: Override default max age (optional)

        Returns:
            StalenessCheck result
        """
        if checked_at is None:
            checked_at = datetime.now()

        if max_age_hours is None:
            max_age_hours = self.manual_staleness_hours

        # Calculate age
        age = checked_at - created_at
        age_hours = age.total_seconds() / 3600

        # Check if within limits
        passed = age_hours <= max_age_hours

        # Format message
        if passed:
            if age_hours < 1:
                message = f"Fresh: {int(age.total_seconds() / 60)} minutes old"
            elif age_hours < max_age_hours * 0.8:
                message = f"Recent: {age_hours:.1f} hours old"
            else:
                message = f"Aging: {age_hours:.1f} hours old, approaching limit"
        else:
            message = (
                f"Too old: {age_hours:.1f} hours, "
                f"exceeds limit of {max_age_hours:.1f} hours"
            )

        logger.debug(
            f"Staleness check: {message}",
            extra={
                "created_at": created_at.isoformat(),
                "checked_at": checked_at.isoformat(),
                "age_hours": age_hours,
                "limit_hours": max_age_hours,
                "passed": passed,
            },
        )

        return StalenessCheck(
            passed=passed,
            age_hours=age_hours,
            limit_hours=max_age_hours,
            created_at=created_at,
            checked_at=checked_at,
            message=message,
        )

    def validate_opportunity(
        self,
        current_price: float,
        original_price: float,
        created_at: datetime,
        source: str = "manual",
    ) -> tuple[bool, list[str]]:
        """Validate both deviation and staleness for an opportunity.

        Args:
            current_price: Current underlying stock price
            original_price: Price when opportunity was created
            created_at: When opportunity was created
            source: Source of opportunity ("manual", "barchart", etc.)

        Returns:
            Tuple of (passed, list of messages)
        """
        messages = []
        all_passed = True

        # Check price deviation
        deviation_check = self.check_deviation(current_price, original_price)
        if not deviation_check.passed:
            all_passed = False
            messages.append(f"❌ {deviation_check.message}")
        else:
            messages.append(f"✓ {deviation_check.message}")
            if deviation_check.warning:
                messages.append(f"⚠️  {deviation_check.warning}")

        # Check staleness (only for manual trades)
        if source == "manual":
            staleness_check = self.check_staleness(created_at)
            if not staleness_check.passed:
                all_passed = False
                messages.append(f"❌ {staleness_check.message}")
            else:
                messages.append(f"✓ {staleness_check.message}")

        return all_passed, messages
