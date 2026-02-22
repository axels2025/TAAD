"""Rolling position management for naked puts.

Provides defensive adjustment by closing an expiring position and
opening a new one further out in time (and optionally at a lower strike).

Roll types:
- Roll out: Same strike, later expiration (more time)
- Roll down and out: Lower strike, later expiration (more safety + time)

Rules:
- ONLY roll for net credit (new premium > buyback cost)
- Max rolls per position tracked via roll_count
- New position must pass earnings check
- Sequential execution: close first, then open (safest)
"""

import os
from dataclasses import dataclass
from datetime import datetime, timedelta

from loguru import logger

from src.execution.position_monitor import PositionStatus
from src.tools.ibkr_client import IBKRClient


# Configuration defaults
MAX_ROLLS = int(os.getenv("ROLL_MAX_TIMES", "2"))
ROLL_DTE_THRESHOLD = int(os.getenv("ROLL_DTE_THRESHOLD", "7"))
ROLL_DAYS_FORWARD = int(os.getenv("ROLL_DAYS_FORWARD", "7"))
ROLL_MIN_PROFIT_PCT = float(os.getenv("ROLL_MIN_PROFIT_PCT", "0.30"))


@dataclass
class RollTarget:
    """Target for a position roll.

    Attributes:
        symbol: Stock symbol
        new_strike: Strike price for new position
        new_expiration: Expiration for new position (YYYYMMDD)
        new_premium_estimate: Estimated premium for new position
        close_cost_estimate: Estimated cost to close current position
        net_credit_estimate: Estimated net credit (positive = credit)
        contracts: Number of contracts
    """

    symbol: str
    new_strike: float
    new_expiration: str  # YYYYMMDD
    new_premium_estimate: float
    close_cost_estimate: float
    net_credit_estimate: float
    contracts: int


@dataclass
class RollDecision:
    """Decision on whether to roll a position.

    Attributes:
        should_roll: Whether the position should be rolled
        reason: Reason for decision
        target: Roll target if should_roll is True
    """

    should_roll: bool
    reason: str
    target: RollTarget | None = None


@dataclass
class RollResult:
    """Result of a roll execution.

    Attributes:
        success: Whether both legs completed
        close_price: Actual price paid to close old position
        open_price: Actual premium received on new position
        net_credit: Net credit/debit (positive = credit)
        new_trade_id: Trade ID of the new position
        error_message: Error if failed
    """

    success: bool
    close_price: float | None = None
    open_price: float | None = None
    net_credit: float | None = None
    new_trade_id: str | None = None
    error_message: str | None = None


class RollManager:
    """Manage defensive position rolls for naked puts.

    Evaluates whether positions approaching expiration should be
    rolled forward rather than simply closed. Only recommends rolls
    that result in a net credit.

    Example:
        >>> manager = RollManager(ibkr_client)
        >>> decision = manager.evaluate_roll(position, roll_count=0)
        >>> if decision.should_roll:
        ...     print(f"Roll to {decision.target.new_strike} exp {decision.target.new_expiration}")
    """

    def __init__(self, ibkr_client: IBKRClient):
        """Initialize roll manager.

        Args:
            ibkr_client: Connected IBKR client
        """
        self.ibkr_client = ibkr_client
        self.max_rolls = MAX_ROLLS
        self.dte_threshold = ROLL_DTE_THRESHOLD
        self.days_forward = ROLL_DAYS_FORWARD
        self.min_profit_pct = ROLL_MIN_PROFIT_PCT

        logger.info(
            f"Initialized RollManager: max_rolls={self.max_rolls}, "
            f"dte_threshold={self.dte_threshold}, days_forward={self.days_forward}"
        )

    def evaluate_roll(
        self,
        position: PositionStatus,
        roll_count: int = 0,
    ) -> RollDecision:
        """Evaluate whether a position should be rolled.

        Checks:
        1. Roll count not exceeded
        2. Position is profitable (minimum threshold)
        3. DTE is at or below threshold
        4. Delta is not too deep ITM (< 0.50)
        5. A viable roll target exists with net credit

        Args:
            position: Current position status
            roll_count: Number of times this position has been rolled

        Returns:
            RollDecision with recommendation
        """
        symbol = position.symbol

        # Check 1: Max rolls
        if roll_count >= self.max_rolls:
            return RollDecision(
                should_roll=False,
                reason=f"Max rolls reached ({roll_count}/{self.max_rolls})",
            )

        # Check 2: Position should be profitable
        if position.current_pnl_pct < self.min_profit_pct:
            return RollDecision(
                should_roll=False,
                reason=f"Insufficient profit ({position.current_pnl_pct:.0%} < {self.min_profit_pct:.0%})",
            )

        # Check 3: DTE at or below threshold
        if position.dte > self.dte_threshold:
            return RollDecision(
                should_roll=False,
                reason=f"DTE too high ({position.dte} > {self.dte_threshold})",
            )

        # Check 4: Delta not too deep ITM
        if position.delta is not None and abs(position.delta) > 0.50:
            return RollDecision(
                should_roll=False,
                reason=f"Delta too deep ({position.delta:.2f} > 0.50, too risky to roll)",
            )

        # Check 5: Find a viable roll target
        target = self._find_roll_target(position)
        if target is None:
            return RollDecision(
                should_roll=False,
                reason="No viable roll target found (no net credit available)",
            )

        # Check 6: Earnings check on new expiration
        if not self._check_earnings_safe(symbol, target.new_expiration):
            return RollDecision(
                should_roll=False,
                reason="Earnings within new DTE window",
            )

        logger.info(
            f"Roll recommended for {symbol} ${position.strike}: "
            f"→ ${target.new_strike} exp {target.new_expiration} "
            f"(net credit est ${target.net_credit_estimate:.2f})"
        )

        return RollDecision(
            should_roll=True,
            reason="Viable roll target found with net credit",
            target=target,
        )

    def _find_roll_target(self, position: PositionStatus) -> RollTarget | None:
        """Find a viable roll target for the position.

        Looks for same-strike options at the next expiration cycle that
        would result in a net credit (new premium > close cost).

        Args:
            position: Current position status

        Returns:
            RollTarget if viable target found, None otherwise
        """
        symbol = position.symbol
        strike = position.strike
        contracts = position.contracts
        current_premium = position.current_premium  # Cost to close

        # Calculate new expiration (~1 week forward from current)
        try:
            current_exp = datetime.strptime(position.expiration_date, "%Y%m%d").date()
        except ValueError:
            logger.error(f"Cannot parse expiration: {position.expiration_date}")
            return None

        new_exp = current_exp + timedelta(days=self.days_forward)
        new_exp_str = new_exp.strftime("%Y%m%d")

        # Get quote for the new contract
        try:
            new_contract = self.ibkr_client.get_option_contract(
                symbol=symbol,
                expiration=new_exp_str,
                strike=strike,
                right="P",
            )
            qualified = self.ibkr_client.qualify_contract(new_contract)
            if not qualified:
                logger.debug(
                    f"Cannot qualify roll target: {symbol} ${strike} exp {new_exp_str}"
                )
                return None

            # Get quote
            import asyncio

            quote = asyncio.run(
                self.ibkr_client.get_quote(qualified, timeout=5.0)
            )

            # Determine new premium from bid (we're selling)
            new_premium = None
            if quote.is_valid and quote.bid and quote.bid > 0:
                new_premium = quote.bid
            elif quote.last and quote.last > 0:
                new_premium = quote.last

            if new_premium is None:
                logger.debug(f"No pricing for roll target {symbol} ${strike} {new_exp_str}")
                return None

        except Exception as e:
            logger.debug(f"Error getting roll target quote: {e}")
            return None

        # Calculate net credit
        net_credit = (new_premium - current_premium) * contracts * 100

        if net_credit <= 0:
            logger.debug(
                f"Roll not viable: net debit ${net_credit:.2f} "
                f"(close ${current_premium:.2f}, new ${new_premium:.2f})"
            )
            return None

        return RollTarget(
            symbol=symbol,
            new_strike=strike,
            new_expiration=new_exp_str,
            new_premium_estimate=new_premium,
            close_cost_estimate=current_premium,
            net_credit_estimate=net_credit,
            contracts=contracts,
        )

    def _check_earnings_safe(self, symbol: str, expiration_str: str) -> bool:
        """Check that no earnings fall within the new DTE window.

        Args:
            symbol: Stock symbol
            expiration_str: New expiration in YYYYMMDD format

        Returns:
            True if safe (no earnings within DTE), False if earnings conflict
        """
        try:
            from datetime import date

            from src.services.earnings_service import get_cached_earnings

            exp_date = datetime.strptime(expiration_str, "%Y%m%d").date()
            earnings_info = get_cached_earnings(symbol, exp_date)
            if earnings_info.earnings_in_dte:
                logger.info(
                    f"Roll blocked: {symbol} has earnings on {earnings_info.earnings_date} "
                    f"within new DTE window (exp {expiration_str})"
                )
                return False
            return True
        except Exception as e:
            # Don't block on earnings data gaps
            logger.warning(f"Earnings data unavailable for {symbol}: {e}")
            return True
