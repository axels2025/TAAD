"""Two-stage validation for staged trades.

This module validates staged trades at two critical points:
- Stage 1: 9:15 AM ET (pre-market) - Checks stock price movement
- Stage 2: 9:30 AM ET (market open) - Checks premium changes

Pre-market data is unreliable for option premiums, so we need both stages.
Stage 1 catches major stock price gaps, Stage 2 confirms premium viability.
"""

import os
import time as time_mod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from loguru import logger

from src.data.opportunity_state import OpportunityState
from src.services.limit_price_calculator import LimitPriceCalculator


class ValidationStatus(Enum):
    """Status of a validation check."""

    READY = "READY"  # Passed check, proceed
    ADJUSTED = "ADJUSTED"  # Passed after adjustment
    STALE = "STALE"  # Failed check, skip this trade
    PENDING = "PENDING"  # Not yet checked


@dataclass
class ValidationConfig:
    """Configuration for validation thresholds.

    All values loaded from environment variables with sensible defaults.
    These thresholds control when trades are adjusted vs marked stale.

    Stage 1 (Pre-market) thresholds:
        max_deviation_ready: Stock price change < this → READY (default 3%)
        max_deviation_adjust: Stock price change < this → try to adjust (default 5%)
        max_deviation_aggressive: Stock price change < this → aggressive adjust (default 10%)
        max_deviation_stale: Stock price change > this → STALE (default 10%)

    Stage 2 (Market-open) thresholds — applied asymmetrically:
        When premium is HIGHER: OTM% is the gate, not deviation %. Always adjust.
        When premium is LOWER:
            max_premium_deviation_confirmed: Drop < this → CONFIRMED (default 15%)
            max_premium_deviation_adjust: Drop < this → adjust limit (default 50%)
            max_premium_deviation_stale: Drop > this → STALE (default 50%)

    General:
        min_otm_execute: Minimum OTM% to execute at any stage (default 12%)
        min_premium_execute: Minimum premium to execute (default $0.20)
    """

    # Stage 1 - Stock price deviation thresholds
    max_deviation_ready: float = 0.03  # <3% → READY
    max_deviation_adjust: float = 0.05  # 3-5% → adjust
    max_deviation_aggressive: float = 0.10  # 5-10% → aggressive adjust
    max_deviation_stale: float = 0.10  # >10% → STALE

    # Stage 2 - Premium deviation thresholds
    # Options premiums swing 20-100% between Sunday staging and Monday open
    # due to theta decay, overnight moves, and bid/ask spread dynamics
    max_premium_deviation_confirmed: float = 0.15  # <15% → CONFIRMED
    max_premium_deviation_adjust: float = 0.50  # 15-50% → adjust limit
    max_premium_deviation_stale: float = 0.50  # >50% → STALE

    # General execution thresholds
    min_otm_execute: float = 0.10  # Minimum 10% OTM to execute (shared with screener via MIN_OTM_PCT)
    min_premium_execute: float = 0.20  # Minimum $0.20 premium
    min_otm_aggressive: float = 0.12  # Minimum OTM for aggressive adjustments
    min_premium_aggressive: float = 0.20  # Minimum premium for aggressive adjustments

    @classmethod
    def from_env(cls) -> "ValidationConfig":
        """Load configuration from environment variables.

        Returns:
            ValidationConfig instance with values from .env
        """
        return cls(
            max_deviation_ready=float(os.getenv("MAX_DEVIATION_READY", "0.03")),
            max_deviation_adjust=float(os.getenv("MAX_DEVIATION_AUTO_ADJUST", "0.05")),
            max_deviation_aggressive=float(os.getenv("MAX_DEVIATION_AGGRESSIVE", "0.10")),
            max_deviation_stale=float(os.getenv("MAX_DEVIATION_STALE", "0.10")),
            max_premium_deviation_confirmed=float(
                os.getenv("MAX_PREMIUM_DEVIATION_CONFIRMED", "0.15")
            ),
            max_premium_deviation_adjust=float(
                os.getenv("MAX_PREMIUM_DEVIATION_ADJUST", "0.50")
            ),
            max_premium_deviation_stale=float(
                os.getenv("MAX_PREMIUM_DEVIATION_STALE", "0.50")
            ),
            min_otm_execute=float(os.getenv("MIN_OTM_PCT", "0.10")),
            min_premium_execute=float(os.getenv("MIN_PREMIUM_EXECUTE", "0.20")),
        )


@dataclass
class StagedOpportunity:
    """A staged opportunity for validation.

    This is an abstraction over the database model to make testing easier.
    Contains all fields needed for validation.
    """

    id: int
    symbol: str
    strike: float
    expiration: str  # YYYY-MM-DD format
    staged_stock_price: float
    staged_limit_price: float
    staged_contracts: int
    staged_margin: float
    otm_pct: float
    state: str = "STAGED"

    # Will be populated during validation
    current_stock_price: float | None = None
    current_bid: float | None = None
    current_ask: float | None = None
    adjusted_strike: float | None = None
    adjusted_limit_price: float | None = None

    # Populated by LiveStrikeSelector (adaptive strike selection)
    live_delta: float | None = None
    live_iv: float | None = None
    live_gamma: float | None = None
    live_theta: float | None = None
    live_volume: int | None = None
    live_open_interest: int | None = None
    strike_selection_method: str | None = None  # "delta", "otm_pct", "unchanged"


@dataclass
class PremarketCheckResult:
    """Result of Stage 1 pre-market validation.

    Contains all details of the stock price check and any adjustments.
    """

    opportunity: StagedOpportunity
    status: ValidationStatus
    staged_price: float
    premarket_price: float
    deviation_pct: float
    new_otm_pct: float
    adjustment_reason: str | None = None
    adjusted_strike: float | None = None
    checked_at: datetime = field(default_factory=datetime.now)

    @property
    def passed(self) -> bool:
        """Check if validation passed (READY or ADJUSTED)."""
        return self.status in (ValidationStatus.READY, ValidationStatus.ADJUSTED)


@dataclass
class OpenCheckResult:
    """Result of Stage 2 market-open validation.

    Contains all details of the premium check and any adjustments.
    """

    opportunity: StagedOpportunity
    status: ValidationStatus
    staged_limit: float
    live_bid: float
    live_ask: float
    premium_deviation_pct: float
    new_limit_price: float | None = None
    final_otm_pct: float = 0.0
    adjustment_reason: str | None = None
    checked_at: datetime = field(default_factory=datetime.now)

    @property
    def passed(self) -> bool:
        """Check if validation passed (READY, ADJUSTED, or CONFIRMED)."""
        return self.status in (
            ValidationStatus.READY,
            ValidationStatus.ADJUSTED,
        )


class IBKRClientProtocol(Protocol):
    """Protocol for IBKR client dependency injection."""

    def get_stock_price(self, symbol: str) -> float | None:
        """Get current stock price."""
        ...

    def get_option_quote(
        self, symbol: str, strike: float, expiration: str, right: str
    ) -> dict | None:
        """Get option quote with bid/ask."""
        ...

    def get_actual_margin(self, contract, quantity: int = 1) -> float | None:
        """Get actual margin via whatIfOrder."""
        ...


class PremarketValidator:
    """Two-stage validation for staged trades.

    Validates staged trades at two critical points:
    - Stage 1: 9:15 AM ET - Pre-market stock price check
    - Stage 2: 9:30 AM ET - Market-open premium check

    Example:
        >>> validator = PremarketValidator(ibkr_client)
        >>> # Stage 1: Pre-market validation
        >>> premarket_results = validator.validate_premarket(staged_opportunities)
        >>> # Stage 2: Market-open validation
        >>> open_results = validator.validate_at_open(ready_opportunities)
    """

    def __init__(
        self,
        ibkr_client: IBKRClientProtocol | None = None,
        config: ValidationConfig | None = None,
        limit_calculator: LimitPriceCalculator | None = None,
    ):
        """Initialize the validator.

        Args:
            ibkr_client: IBKR client for quotes. If None, uses mock data.
            config: Validation configuration. If None, loads from env.
            limit_calculator: Limit price calculator. If None, creates one.
        """
        self.ibkr_client = ibkr_client
        self.config = config or ValidationConfig.from_env()
        self.limit_calculator = limit_calculator or LimitPriceCalculator()

        logger.debug(
            f"PremarketValidator initialized: "
            f"stock_thresholds=[{self.config.max_deviation_ready:.0%}, "
            f"{self.config.max_deviation_adjust:.0%}, "
            f"{self.config.max_deviation_stale:.0%}], "
            f"premium_thresholds=[{self.config.max_premium_deviation_confirmed:.0%}, "
            f"{self.config.max_premium_deviation_adjust:.0%}]"
        )

    def validate_premarket(
        self,
        opportunities: list[StagedOpportunity],
    ) -> list[PremarketCheckResult]:
        """Stage 1: Pre-market validation (9:15 AM ET).

        Checks stock price movement since staging. Decision tree:
        - deviation < 3%: READY (stock looks stable)
        - deviation 3-5%: Try to adjust strike
        - deviation 5-10%: Aggressive adjustment (lower OTM threshold)
        - deviation > 10%: STALE (too much movement)

        Args:
            opportunities: List of staged opportunities to validate

        Returns:
            List of PremarketCheckResult with status for each
        """
        logger.info(f"Stage 1: Validating {len(opportunities)} staged opportunities")
        results: list[PremarketCheckResult] = []

        for opp in opportunities:
            result = self._validate_premarket_single(opp)
            results.append(result)

            log_level = "info" if result.passed else "warning"
            getattr(logger, log_level)(
                f"  {opp.symbol}: {result.status.value} "
                f"(deviation {result.deviation_pct:+.1%}, OTM {result.new_otm_pct:.1%})"
            )

        # Summary
        ready_count = sum(1 for r in results if r.status == ValidationStatus.READY)
        adjusted_count = sum(1 for r in results if r.status == ValidationStatus.ADJUSTED)
        stale_count = sum(1 for r in results if r.status == ValidationStatus.STALE)

        logger.info(
            f"Stage 1 complete: {ready_count} READY, "
            f"{adjusted_count} ADJUSTED, {stale_count} STALE"
        )

        return results

    def _validate_premarket_single(
        self, opp: StagedOpportunity
    ) -> PremarketCheckResult:
        """Validate a single opportunity in pre-market.

        Args:
            opp: The staged opportunity to validate

        Returns:
            PremarketCheckResult with validation status
        """
        # Get current stock price
        current_price = self._get_stock_price(opp.symbol)
        if current_price is None:
            current_price = opp.staged_stock_price  # Use staged if unavailable

        opp.current_stock_price = current_price

        # Calculate deviation
        deviation = (current_price - opp.staged_stock_price) / opp.staged_stock_price

        # Calculate new OTM% with current price
        effective_strike = opp.adjusted_strike or opp.strike
        new_otm_pct = (current_price - effective_strike) / current_price

        # Decision tree
        abs_deviation = abs(deviation)

        if abs_deviation < self.config.max_deviation_ready:
            # Stock is stable, proceed
            return PremarketCheckResult(
                opportunity=opp,
                status=ValidationStatus.READY,
                staged_price=opp.staged_stock_price,
                premarket_price=current_price,
                deviation_pct=deviation,
                new_otm_pct=new_otm_pct,
            )

        elif abs_deviation < self.config.max_deviation_adjust:
            # Moderate movement, try standard adjustment
            adjustment = self._try_adjust_strike(
                opp, current_price, aggressive=False
            )
            if adjustment:
                return PremarketCheckResult(
                    opportunity=opp,
                    status=ValidationStatus.ADJUSTED,
                    staged_price=opp.staged_stock_price,
                    premarket_price=current_price,
                    deviation_pct=deviation,
                    new_otm_pct=adjustment["new_otm_pct"],
                    adjusted_strike=adjustment["new_strike"],
                    adjustment_reason=adjustment["reason"],
                )
            else:
                return PremarketCheckResult(
                    opportunity=opp,
                    status=ValidationStatus.STALE,
                    staged_price=opp.staged_stock_price,
                    premarket_price=current_price,
                    deviation_pct=deviation,
                    new_otm_pct=new_otm_pct,
                    adjustment_reason="Could not find viable adjustment",
                )

        elif abs_deviation < self.config.max_deviation_stale:
            # Large movement, try aggressive adjustment
            adjustment = self._try_adjust_strike(
                opp, current_price, aggressive=True
            )
            if adjustment:
                return PremarketCheckResult(
                    opportunity=opp,
                    status=ValidationStatus.ADJUSTED,
                    staged_price=opp.staged_stock_price,
                    premarket_price=current_price,
                    deviation_pct=deviation,
                    new_otm_pct=adjustment["new_otm_pct"],
                    adjusted_strike=adjustment["new_strike"],
                    adjustment_reason=f"Aggressive: {adjustment['reason']}",
                )
            else:
                return PremarketCheckResult(
                    opportunity=opp,
                    status=ValidationStatus.STALE,
                    staged_price=opp.staged_stock_price,
                    premarket_price=current_price,
                    deviation_pct=deviation,
                    new_otm_pct=new_otm_pct,
                    adjustment_reason="Large movement, no viable adjustment",
                )

        else:
            # Extreme movement, mark stale
            return PremarketCheckResult(
                opportunity=opp,
                status=ValidationStatus.STALE,
                staged_price=opp.staged_stock_price,
                premarket_price=current_price,
                deviation_pct=deviation,
                new_otm_pct=new_otm_pct,
                adjustment_reason=f"Stock moved {abs_deviation:.1%}, exceeds threshold",
            )

    def _determine_strike_interval(
        self, symbol: str, stock_price: float
    ) -> float:
        """Determine the appropriate strike interval for a symbol.

        Tries to detect actual interval from IBKR options chain.
        Falls back to standard intervals based on stock price.

        Args:
            symbol: Stock symbol
            stock_price: Current stock price

        Returns:
            Strike interval (e.g., 0.50, 1.00, 2.50, 5.00)
        """
        if self.ibkr_client:
            try:
                # Try to get option chain and detect actual interval
                from ib_insync import Stock

                stock_contract = Stock(symbol, "SMART", "USD")
                qualified = self.ibkr_client.qualify_contract(stock_contract)

                if qualified:
                    chains = self.ibkr_client.ib.reqSecDefOptParams(
                        qualified.symbol,
                        "",
                        qualified.secType,
                        qualified.conId,
                    )

                    if chains and len(chains) > 0:
                        # Get strikes from first exchange
                        strikes = sorted(chains[0].strikes)

                        if len(strikes) >= 10:
                            # Look at strikes near current price
                            nearby_strikes = [
                                s for s in strikes if abs(s - stock_price) < stock_price * 0.3
                            ]

                            if len(nearby_strikes) >= 5:
                                # Calculate intervals between consecutive strikes
                                intervals = [
                                    nearby_strikes[i + 1] - nearby_strikes[i]
                                    for i in range(min(10, len(nearby_strikes) - 1))
                                ]

                                # Find most common interval
                                from collections import Counter

                                counter = Counter(intervals)
                                if counter:
                                    most_common_interval = counter.most_common(1)[0][0]
                                    logger.debug(
                                        f"{symbol}: Detected strike interval ${most_common_interval} "
                                        f"from options chain"
                                    )
                                    return most_common_interval

            except Exception as e:
                logger.debug(f"{symbol}: Could not detect strike interval from chain: {e}")

        # Fallback to standard intervals based on stock price
        # Most liquid stocks use $1.00 intervals regardless of price
        # Only very low-priced or very high-priced stocks differ
        if stock_price < 25:
            return 0.50  # Low-priced stocks often use $0.50
        elif stock_price < 500:
            return 1.00  # Most common for liquid stocks at any price
        else:
            return 5.00  # Very high-priced stocks may use $5.00

    def _try_adjust_strike(
        self,
        opp: StagedOpportunity,
        current_price: float,
        aggressive: bool = False,
    ) -> dict | None:
        """Try to find a viable strike adjustment.

        When stock price moves, we may need to adjust the strike to maintain
        acceptable OTM%. This finds a new strike that works.

        Args:
            opp: The opportunity to adjust
            current_price: Current stock price
            aggressive: If True, use lower thresholds

        Returns:
            Dict with new_strike, new_otm_pct, reason if viable, else None
        """
        # Determine thresholds based on mode
        min_otm = (
            self.config.min_otm_aggressive
            if aggressive
            else self.config.min_otm_execute
        )

        # Calculate what strike would give us acceptable OTM
        # OTM% = (stock_price - strike) / stock_price
        # strike = stock_price * (1 - OTM%)
        target_strike = current_price * (1 - min_otm)

        # Determine appropriate strike interval
        strike_interval = self._determine_strike_interval(opp.symbol, current_price)

        # Round to nearest interval
        new_strike = round(target_strike / strike_interval) * strike_interval

        # Make sure new strike is lower than current price (OTM for puts)
        if new_strike >= current_price:
            new_strike = current_price - strike_interval

        # Calculate actual OTM with new strike
        new_otm_pct = (current_price - new_strike) / current_price

        # Verify OTM is acceptable
        if new_otm_pct < min_otm:
            logger.debug(
                f"{opp.symbol}: Adjusted strike ${new_strike} gives OTM "
                f"{new_otm_pct:.1%} < {min_otm:.0%} minimum"
            )
            return None

        # Update opportunity
        opp.adjusted_strike = new_strike

        logger.debug(
            f"{opp.symbol}: Using strike interval ${strike_interval} "
            f"(price: ${current_price:.2f})"
        )

        return {
            "new_strike": new_strike,
            "new_otm_pct": new_otm_pct,
            "reason": (
                f"Strike adjusted ${opp.strike:.0f} → ${new_strike:.0f} "
                f"(OTM: {new_otm_pct:.1%})"
            ),
        }

    def validate_at_open(
        self,
        opportunities: list[StagedOpportunity],
        max_retries: int = 3,
        retry_delay: float = 10.0,
    ) -> list[OpenCheckResult]:
        """Stage 2: Market-open validation (9:30 AM ET).

        Checks premium changes since staging with asymmetric logic:

        Premium HIGHER than staged (favorable):
        - If OTM% >= minimum → ADJUSTED (capture better premium)
        - If OTM% < minimum → STALE (premium up because too close to money)

        Premium LOWER than staged (unfavorable):
        - deviation < 15% → CONFIRMED
        - deviation 15-50% → ADJUSTED (recalculate limit)
        - deviation > 50% → STALE (premium collapsed)

        Also performs final OTM% check with live stock price.

        Retry logic: If IBKR returns bid <= 0 (market not open yet), the
        result is PENDING and retried up to max_retries times with
        retry_delay seconds between attempts. After all retries, PENDING
        results are converted to STALE.

        Args:
            opportunities: List of READY opportunities from Stage 1
            max_retries: Max attempts for trades with invalid bids (default 3)
            retry_delay: Seconds between retries (default 10.0)

        Returns:
            List of OpenCheckResult with status for each
        """
        logger.info(f"Stage 2: Validating {len(opportunities)} ready opportunities")

        remaining = list(opportunities)
        final_results: list[OpenCheckResult] = []

        for attempt in range(max_retries):
            batch_results: list[OpenCheckResult] = []

            for opp in remaining:
                result = self._validate_at_open_single(opp)
                batch_results.append(result)

                if result.status == ValidationStatus.PENDING:
                    status_emoji = "⏳"
                    log_level = "warning"
                elif result.passed:
                    status_emoji = "✓"
                    log_level = "info"
                else:
                    status_emoji = "✗"
                    log_level = "warning"

                getattr(logger, log_level)(
                    f"  {status_emoji} {opp.symbol}: {result.status.value} "
                    f"(premium Δ {result.premium_deviation_pct:+.1%})"
                )

            # Separate PENDING from resolved results
            pending = [r for r in batch_results if r.status == ValidationStatus.PENDING]
            resolved = [r for r in batch_results if r.status != ValidationStatus.PENDING]
            final_results.extend(resolved)

            if not pending or attempt == max_retries - 1:
                # Convert remaining PENDING to STALE on final attempt
                for r in pending:
                    r.status = ValidationStatus.STALE
                    r.adjustment_reason = (
                        f"No valid bid after {max_retries} attempts "
                        f"({r.adjustment_reason})"
                    )
                final_results.extend(pending)
                break

            logger.info(
                f"  ⏳ {len(pending)} trades have no valid bids, "
                f"retrying in {retry_delay:.0f}s "
                f"(attempt {attempt + 1}/{max_retries})..."
            )
            time_mod.sleep(retry_delay)
            remaining = [r.opportunity for r in pending]

        # Summary
        confirmed = sum(
            1 for r in final_results if r.status == ValidationStatus.READY
        )
        adjusted = sum(
            1 for r in final_results if r.status == ValidationStatus.ADJUSTED
        )
        stale = sum(1 for r in final_results if r.status == ValidationStatus.STALE)

        logger.info(
            f"Stage 2 complete: {confirmed} CONFIRMED, "
            f"{adjusted} ADJUSTED, {stale} STALE"
        )

        return final_results

    def _validate_at_open_single(
        self, opp: StagedOpportunity
    ) -> OpenCheckResult:
        """Validate a single opportunity at market open.

        Args:
            opp: The opportunity to validate (should be READY from Stage 1)

        Returns:
            OpenCheckResult with validation status
        """
        # Get live quote
        quote = self._get_option_quote(opp)
        effective_strike = opp.adjusted_strike or opp.strike

        if quote is None:
            # No quote available, use staged values
            live_bid = opp.staged_limit_price
            live_ask = opp.staged_limit_price * 1.1
        else:
            live_bid = quote.get("bid", opp.staged_limit_price)
            live_ask = quote.get("ask", live_bid * 1.1)

        opp.current_bid = live_bid
        opp.current_ask = live_ask

        # Detect invalid bid (options haven't started trading yet).
        # IBKR returns bid=-1.0 before market open at 9:30 AM ET.
        if live_bid is not None and live_bid <= 0:
            staged_limit = opp.adjusted_limit_price or opp.staged_limit_price
            return OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.PENDING,
                staged_limit=staged_limit,
                live_bid=live_bid,
                live_ask=live_ask,
                premium_deviation_pct=0.0,
                adjustment_reason="No valid bid yet (market may not be open)",
            )

        # Get current stock price for final OTM check
        current_stock = self._get_stock_price(opp.symbol)
        if current_stock is None:
            current_stock = opp.current_stock_price or opp.staged_stock_price

        # Calculate final OTM%
        final_otm_pct = (current_stock - effective_strike) / current_stock

        # Calculate premium deviation
        # Premium deviation = (live_bid - staged_limit) / staged_limit
        staged_limit = opp.adjusted_limit_price or opp.staged_limit_price
        premium_deviation = (live_bid - staged_limit) / staged_limit if staged_limit > 0 else 0

        # Check minimum OTM
        if final_otm_pct < self.config.min_otm_execute:
            return OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.STALE,
                staged_limit=staged_limit,
                live_bid=live_bid,
                live_ask=live_ask,
                premium_deviation_pct=premium_deviation,
                final_otm_pct=final_otm_pct,
                adjustment_reason=(
                    f"OTM {final_otm_pct:.1%} below minimum {self.config.min_otm_execute:.0%}"
                ),
            )

        # Decision tree: treat premium increases differently from decreases.
        # Higher premium with OTM intact = better deal, not a risk signal.
        premium_higher = premium_deviation > 0
        abs_deviation = abs(premium_deviation)
        new_limit = self.limit_calculator.calculate_sell_limit(live_bid, live_ask)

        if premium_higher:
            # Premium is HIGHER than staged — favorable direction.
            # The only real risk is that we're closer to the money (OTM eroded).
            # OTM minimum was already checked above, so if we're here, OTM is fine.
            # Just adjust the limit to capture the better premium.
            opp.adjusted_limit_price = new_limit

            if abs_deviation < self.config.max_premium_deviation_confirmed:
                return OpenCheckResult(
                    opportunity=opp,
                    status=ValidationStatus.READY,
                    staged_limit=staged_limit,
                    live_bid=live_bid,
                    live_ask=live_ask,
                    premium_deviation_pct=premium_deviation,
                    new_limit_price=new_limit,
                    final_otm_pct=final_otm_pct,
                )
            else:
                return OpenCheckResult(
                    opportunity=opp,
                    status=ValidationStatus.ADJUSTED,
                    staged_limit=staged_limit,
                    live_bid=live_bid,
                    live_ask=live_ask,
                    premium_deviation_pct=premium_deviation,
                    new_limit_price=new_limit,
                    final_otm_pct=final_otm_pct,
                    adjustment_reason=(
                        f"Premium up {abs_deviation:.1%}, OTM {final_otm_pct:.1%} intact — "
                        f"limit adjusted ${staged_limit:.2f} → ${new_limit:.2f}"
                    ),
                )

        # Premium is LOWER than staged — unfavorable direction.
        # Apply stricter thresholds since we're getting paid less.
        if abs_deviation < self.config.max_premium_deviation_confirmed:
            # Small drop, confirm
            opp.adjusted_limit_price = new_limit

            return OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.READY,
                staged_limit=staged_limit,
                live_bid=live_bid,
                live_ask=live_ask,
                premium_deviation_pct=premium_deviation,
                new_limit_price=new_limit,
                final_otm_pct=final_otm_pct,
            )

        elif abs_deviation < self.config.max_premium_deviation_adjust:
            # Moderate drop, recalculate limit
            if new_limit < self.config.min_premium_execute:
                return OpenCheckResult(
                    opportunity=opp,
                    status=ValidationStatus.STALE,
                    staged_limit=staged_limit,
                    live_bid=live_bid,
                    live_ask=live_ask,
                    premium_deviation_pct=premium_deviation,
                    final_otm_pct=final_otm_pct,
                    adjustment_reason=(
                        f"New premium ${new_limit:.2f} below minimum "
                        f"${self.config.min_premium_execute:.2f}"
                    ),
                )

            opp.adjusted_limit_price = new_limit

            return OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.ADJUSTED,
                staged_limit=staged_limit,
                live_bid=live_bid,
                live_ask=live_ask,
                premium_deviation_pct=premium_deviation,
                new_limit_price=new_limit,
                final_otm_pct=final_otm_pct,
                adjustment_reason=(
                    f"Premium down {abs_deviation:.1%} — "
                    f"limit adjusted ${staged_limit:.2f} → ${new_limit:.2f}"
                ),
            )

        else:
            # Premium dropped too much, mark stale
            return OpenCheckResult(
                opportunity=opp,
                status=ValidationStatus.STALE,
                staged_limit=staged_limit,
                live_bid=live_bid,
                live_ask=live_ask,
                premium_deviation_pct=premium_deviation,
                final_otm_pct=final_otm_pct,
                adjustment_reason=(
                    f"Premium down {abs_deviation:.1%}, exceeds "
                    f"{self.config.max_premium_deviation_stale:.0%} threshold"
                ),
            )

    def _get_stock_price(self, symbol: str) -> float | None:
        """Get current stock price from IBKR.

        Args:
            symbol: Stock symbol

        Returns:
            Current price or None if unavailable
        """
        if self.ibkr_client is None:
            return None

        try:
            return self.ibkr_client.get_stock_price(symbol)
        except Exception as e:
            logger.debug(f"Error getting stock price for {symbol}: {e}")
            return None

    def _get_option_quote(self, opp: StagedOpportunity) -> dict | None:
        """Get option quote from IBKR.

        Args:
            opp: The opportunity to get quote for

        Returns:
            Dict with bid/ask or None if unavailable
        """
        if self.ibkr_client is None:
            return None

        try:
            effective_strike = opp.adjusted_strike or opp.strike

            # Convert expiration to IBKR format (YYYYMMDD)
            exp_str = opp.expiration
            if isinstance(exp_str, str):
                # Convert from ISO format "2026-02-13" to IBKR format "20260213"
                exp_str = exp_str.replace("-", "")
            else:
                # Convert datetime to IBKR format
                exp_str = exp_str.strftime("%Y%m%d")

            return self.ibkr_client.get_option_quote(
                opp.symbol,
                effective_strike,
                exp_str,
                "P",  # Put
            )
        except Exception as e:
            logger.debug(f"Error getting option quote for {opp.symbol}: {e}")
            return None

    def get_target_state_for_result(
        self,
        result: PremarketCheckResult | OpenCheckResult,
    ) -> OpportunityState:
        """Get the target opportunity state for a validation result.

        Maps validation status to opportunity state.

        Args:
            result: Validation result (pre-market or market-open)

        Returns:
            Target OpportunityState
        """
        if isinstance(result, PremarketCheckResult):
            # Stage 1 mappings
            if result.status == ValidationStatus.READY:
                return OpportunityState.READY
            elif result.status == ValidationStatus.ADJUSTED:
                return OpportunityState.READY  # Adjusted and ready
            else:
                return OpportunityState.STALE

        else:
            # Stage 2 mappings
            if result.status == ValidationStatus.READY:
                return OpportunityState.CONFIRMED
            elif result.status == ValidationStatus.ADJUSTED:
                return OpportunityState.CONFIRMED  # Adjusted and confirmed
            else:
                return OpportunityState.STALE
