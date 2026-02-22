"""Market condition monitoring for intelligent execution timing.

This module provides VIX and spread monitoring to determine optimal
execution timing for Tier 2 retries. Instead of blindly executing at
a fixed time, we wait for favorable market conditions.

Key Concepts:
- VIX < 18: Very favorable (tight spreads, low volatility)
- VIX 18-25: Moderate (acceptable conditions)
- VIX > 25: Unfavorable (high volatility, wide spreads)

Usage:
    monitor = MarketConditionMonitor(ibkr_client)
    conditions = await monitor.check_conditions(sample_contracts)

    if conditions.conditions_favorable:
        # Execute Tier 2
        await execute_orders()
"""

import os
import statistics
from dataclasses import dataclass
from datetime import datetime
from zoneinfo import ZoneInfo

from ib_insync import Contract
from loguru import logger

from src.tools.ibkr_client import IBKRClient


@dataclass
class MarketConditions:
    """Snapshot of current market conditions at a specific time.

    Attributes:
        timestamp: When conditions were checked (ET timezone)
        vix: Current VIX level
        spy_price: Current SPY price (market direction indicator)
        avg_spread: Average bid-ask spread across sample contracts
        conditions_favorable: True if conditions good for execution
        reason: Human-readable explanation of favorable/unfavorable assessment
    """
    timestamp: datetime
    vix: float
    spy_price: float
    avg_spread: float
    conditions_favorable: bool
    reason: str

    def __str__(self) -> str:
        """Format conditions for logging."""
        status = "✓ FAVORABLE" if self.conditions_favorable else "✗ UNFAVORABLE"
        return (
            f"Market Conditions at {self.timestamp.strftime('%H:%M:%S')} ET:\n"
            f"  Status: {status}\n"
            f"  VIX: {self.vix:.1f}\n"
            f"  SPY: ${self.spy_price:.2f}\n"
            f"  Avg Spread: ${self.avg_spread:.3f}\n"
            f"  Reason: {self.reason}"
        )


class MarketConditionMonitor:
    """Monitor market conditions for execution timing decisions.

    Used by TwoTierExecutionScheduler to determine when to execute Tier 2
    retries. Monitors VIX (volatility) and bid-ask spreads to wait for
    optimal conditions rather than executing at a fixed time.

    Thresholds (configurable via .env):
        TIER2_VIX_LOW: VIX below this = very favorable (default: 18)
        TIER2_VIX_HIGH: VIX above this = unfavorable (default: 25)
        TIER2_MAX_SPREAD: Max acceptable average spread (default: 0.08)

    Example:
        monitor = MarketConditionMonitor(ibkr_client)

        # Check conditions
        conditions = await monitor.check_conditions(contracts)

        if conditions.conditions_favorable:
            logger.info(f"✓ Favorable: {conditions.reason}")
            await execute_tier2()
        else:
            logger.info(f"✗ Unfavorable: {conditions.reason}")
            await asyncio.sleep(300)  # Wait 5 minutes, check again
    """

    def __init__(self, ibkr_client: IBKRClient):
        """Initialize market condition monitor.

        Args:
            ibkr_client: Connected IBKRClient instance for fetching quotes
        """
        self.client = ibkr_client

        # Load configurable thresholds from environment
        self.vix_low_threshold = float(os.getenv("TIER2_VIX_LOW", "18"))
        self.vix_high_threshold = float(os.getenv("TIER2_VIX_HIGH", "25"))
        self.max_spread = float(os.getenv("TIER2_MAX_SPREAD", "0.08"))

        logger.info(
            f"MarketConditionMonitor initialized: "
            f"VIX thresholds ({self.vix_low_threshold}-{self.vix_high_threshold}), "
            f"max spread ${self.max_spread:.3f}"
        )

    async def check_conditions(
        self,
        sample_contracts: list[Contract] | None = None
    ) -> MarketConditions:
        """Check if market conditions are favorable for execution.

        Fetches current VIX, SPY price, and optionally checks spreads on
        sample contracts to determine if conditions are good for Tier 2
        execution.

        Args:
            sample_contracts: Optional list of contracts to check spreads.
                             If provided, uses first 5 for spread calculation.

        Returns:
            MarketConditions with favorable flag and detailed reason

        Example:
            # Check with sample contracts
            conditions = await monitor.check_conditions([contract1, contract2, contract3])

            # Check without spreads (VIX only)
            conditions = await monitor.check_conditions()
        """
        now = datetime.now(ZoneInfo("America/New_York"))

        # Get VIX level
        vix = await self._get_vix()

        # Get SPY price (market direction indicator)
        spy_price = await self._get_spy_price()

        # Check spreads on sample contracts (if provided)
        avg_spread = 0.0
        if sample_contracts:
            avg_spread = await self._calculate_average_spread(sample_contracts)

        # Evaluate if conditions favorable
        favorable, reason = self._evaluate_conditions(vix, avg_spread)

        conditions = MarketConditions(
            timestamp=now,
            vix=vix,
            spy_price=spy_price,
            avg_spread=avg_spread,
            conditions_favorable=favorable,
            reason=reason
        )

        logger.debug(str(conditions))

        return conditions

    def _evaluate_conditions(self, vix: float, avg_spread: float) -> tuple[bool, str]:
        """Evaluate if current conditions are favorable for execution.

        Logic:
        1. VIX > high_threshold → Unfavorable (too volatile)
        2. Spreads > max_spread → Unfavorable (execution quality poor)
        3. VIX < low_threshold → Very favorable
        4. Otherwise → Favorable (moderate conditions)

        Args:
            vix: Current VIX level
            avg_spread: Average bid-ask spread across sample contracts

        Returns:
            Tuple of (favorable: bool, reason: str)
        """
        # Check VIX too high (market panic/fear)
        if vix > self.vix_high_threshold:
            return False, (
                f"VIX too high: {vix:.1f} "
                f"(threshold: {self.vix_high_threshold})"
            )

        # Check spreads too wide (poor execution quality)
        if avg_spread > self.max_spread:
            return False, (
                f"Spreads too wide: ${avg_spread:.3f} "
                f"(threshold: ${self.max_spread:.3f})"
            )

        # Conditions favorable
        if vix < self.vix_low_threshold:
            return True, (
                f"VIX low ({vix:.1f}), "
                f"spreads tight (${avg_spread:.3f})"
            )
        else:
            return True, (
                f"VIX moderate ({vix:.1f}), "
                f"spreads acceptable (${avg_spread:.3f})"
            )

    async def _get_vix(self) -> float:
        """Get current VIX level from IBKR.

        Returns:
            Current VIX value, or 20.0 (conservative default) if unavailable
        """
        try:
            # Create VIX index contract
            vix_contract = Contract()
            vix_contract.symbol = "VIX"
            vix_contract.secType = "IND"
            vix_contract.exchange = "CBOE"
            vix_contract.currency = "USD"

            # Get quote
            quote = await self.client.get_quote(vix_contract, timeout=2.0)

            if quote.is_valid and quote.last > 0:
                logger.debug(f"VIX fetched: {quote.last:.2f}")
                return quote.last

            # Invalid quote
            logger.warning("Invalid VIX quote, using default 20.0")
            return 20.0

        except Exception as e:
            logger.error(f"Failed to get VIX: {e}")
            return 20.0  # Conservative default (moderate volatility)

    async def _get_spy_price(self) -> float:
        """Get current SPY price from IBKR.

        SPY price is used as a market direction indicator and for
        logging/debugging purposes.

        Returns:
            Current SPY price, or 0.0 if unavailable
        """
        try:
            # Create SPY stock contract
            spy_contract = Contract()
            spy_contract.symbol = "SPY"
            spy_contract.secType = "STK"
            spy_contract.exchange = "SMART"
            spy_contract.currency = "USD"

            # Get quote
            quote = await self.client.get_quote(spy_contract, timeout=2.0)

            if quote.is_valid and quote.last > 0:
                logger.debug(f"SPY fetched: ${quote.last:.2f}")
                return quote.last

            # Invalid quote
            logger.debug("Invalid SPY quote")
            return 0.0

        except Exception as e:
            logger.error(f"Failed to get SPY price: {e}")
            return 0.0

    async def _calculate_average_spread(
        self,
        contracts: list[Contract]
    ) -> float:
        """Calculate average bid-ask spread across sample contracts.

        Args:
            contracts: List of contracts to check (uses first 5)

        Returns:
            Average spread in dollars, or 0.0 if no valid quotes
        """
        spreads = []

        # Sample first 5 contracts (don't need to check all)
        sample = contracts[:5]

        for contract in sample:
            try:
                quote = await self.client.get_quote(contract, timeout=1.0)

                if quote.is_valid and quote.ask > quote.bid > 0:
                    spread = quote.ask - quote.bid
                    spreads.append(spread)
                    logger.debug(
                        f"{contract.symbol} spread: ${spread:.3f} "
                        f"(bid: ${quote.bid:.2f}, ask: ${quote.ask:.2f})"
                    )

            except Exception as e:
                logger.warning(f"Failed to get quote for {contract.symbol}: {e}")
                continue

        if spreads:
            avg_spread = statistics.mean(spreads)
            logger.debug(
                f"Average spread across {len(spreads)} contracts: ${avg_spread:.3f}"
            )
            return avg_spread

        # No valid spreads
        logger.warning("No valid spreads found in sample")
        return 0.0
