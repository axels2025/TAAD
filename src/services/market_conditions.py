"""Market condition monitoring for intelligent execution timing.

This module provides volatility complex monitoring (VIX, VVIX, VIX3M)
and spread monitoring to determine optimal execution timing.

Key Signals:
- VIX: Fear level (< 18 favorable, > 25 unfavorable)
- VVIX: VIX stability (> 130 = VIX itself is unstable, block entries)
- VIX3M: 3-month VIX for term structure (VIX/VIX3M > 1.0 = backwardation = fear)
- Bid-ask spreads: Execution quality on sample contracts

Usage:
    monitor = MarketConditionMonitor(ibkr_client)
    conditions = await monitor.check_conditions(sample_contracts)

    if conditions.conditions_favorable:
        await execute_orders()
"""

import asyncio
import os
import statistics
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from src.broker.types import Contract
from loguru import logger

from src.broker.protocols import BrokerClient


@dataclass
class MarketConditions:
    """Snapshot of current market conditions at a specific time.

    Attributes:
        timestamp: When conditions were checked (ET timezone)
        vix: Current VIX level
        vvix: Volatility of VIX (normal: 80-100, extreme: >130)
        vix3m: 3-month VIX for term structure comparison
        term_structure: "contango" or "backwardation"
        term_structure_ratio: VIX/VIX3M ratio (>1.0 = backwardation)
        spy_price: Current SPY price (market direction indicator)
        avg_spread: Average bid-ask spread across sample contracts
        conditions_favorable: True if conditions good for execution
        reason: Human-readable explanation of favorable/unfavorable assessment
        warnings: Non-fatal warning messages (e.g. VVIX elevated but not extreme)
    """
    timestamp: datetime
    vix: float
    spy_price: float
    avg_spread: float
    conditions_favorable: bool
    reason: str
    vvix: float = 0.0
    vix3m: float = 0.0
    term_structure: str = ""
    term_structure_ratio: float = 0.0
    warnings: list[str] = field(default_factory=list)

    def __str__(self) -> str:
        """Format conditions for logging."""
        status = "✓ FAVORABLE" if self.conditions_favorable else "✗ UNFAVORABLE"
        ts_label = self.term_structure or "unknown"
        parts = [
            f"Market Conditions at {self.timestamp.strftime('%H:%M:%S')} ET:",
            f"  Status: {status}",
            f"  VIX: {self.vix:.1f}  VVIX: {self.vvix:.0f}  VIX3M: {self.vix3m:.1f}",
            f"  Term Structure: {ts_label} (ratio: {self.term_structure_ratio:.2f})",
            f"  SPY: ${self.spy_price:.2f}",
            f"  Avg Spread: ${self.avg_spread:.3f}",
            f"  Reason: {self.reason}",
        ]
        if self.warnings:
            parts.append(f"  Warnings: {'; '.join(self.warnings)}")
        return "\n".join(parts)


class MarketConditionMonitor:
    """Monitor market conditions for execution timing decisions.

    Uses a volatility complex (VIX + VVIX + VIX3M) for deeper market
    health assessment beyond just the VIX level.

    Thresholds (configurable via .env):
        TIER2_VIX_LOW: VIX below this = very favorable (default: 18)
        TIER2_VIX_HIGH: VIX above this = unfavorable (default: 25)
        TIER2_MAX_SPREAD: Max acceptable average spread (default: 0.08)
        VVIX_WARN: VVIX above this = warning (default: 100)
        VVIX_EXTREME: VVIX above this = block entries (default: 130)
        TERM_STRUCTURE_WARN: Backwardation ratio above this = block (default: 1.05)

    Example:
        monitor = MarketConditionMonitor(ibkr_client)
        conditions = await monitor.check_conditions(contracts)

        if conditions.conditions_favorable:
            logger.info(f"✓ Favorable: {conditions.reason}")
            await execute_tier2()
    """

    def __init__(self, ibkr_client: BrokerClient):
        """Initialize market condition monitor.

        Args:
            ibkr_client: Connected IBKRClient instance for fetching quotes
        """
        self.client = ibkr_client

        # VIX thresholds
        self.vix_low_threshold = float(os.getenv("TIER2_VIX_LOW", "18"))
        self.vix_high_threshold = float(os.getenv("TIER2_VIX_HIGH", "25"))
        self.max_spread = float(os.getenv("TIER2_MAX_SPREAD", "0.08"))

        # VVIX thresholds
        self.vvix_warn_threshold = float(os.getenv("VVIX_WARN", "100"))
        self.vvix_extreme_threshold = float(os.getenv("VVIX_EXTREME", "130"))

        # Term structure threshold (VIX/VIX3M ratio)
        self.term_structure_block_threshold = float(
            os.getenv("TERM_STRUCTURE_BLOCK", "1.05")
        )

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

        Fetches the volatility complex (VIX, VVIX, VIX3M) in parallel,
        SPY price, and optionally checks spreads on sample contracts.

        Args:
            sample_contracts: Optional list of contracts to check spreads.
                             If provided, uses first 5 for spread calculation.

        Returns:
            MarketConditions with favorable flag and detailed reason
        """
        now = datetime.now(ZoneInfo("America/New_York"))

        # Fetch volatility complex (VIX + VVIX + VIX3M) in parallel
        vix, vvix, vix3m = await self._get_volatility_complex()

        # Calculate term structure
        if vix3m > 0:
            term_structure_ratio = round(vix / vix3m, 3)
            term_structure = "backwardation" if term_structure_ratio > 1.0 else "contango"
        else:
            term_structure_ratio = 0.0
            term_structure = "unknown"

        # Get SPY price (market direction indicator)
        spy_price = await self._get_spy_price()

        # Check spreads on sample contracts (if provided)
        avg_spread = 0.0
        if sample_contracts:
            avg_spread = await self._calculate_average_spread(sample_contracts)

        # Evaluate if conditions favorable
        favorable, reason, warnings = self._evaluate_conditions(
            vix, vvix, vix3m, term_structure_ratio, avg_spread
        )

        conditions = MarketConditions(
            timestamp=now,
            vix=vix,
            vvix=vvix,
            vix3m=vix3m,
            term_structure=term_structure,
            term_structure_ratio=term_structure_ratio,
            spy_price=spy_price,
            avg_spread=avg_spread,
            conditions_favorable=favorable,
            reason=reason,
            warnings=warnings,
        )

        logger.debug(str(conditions))

        return conditions

    def _evaluate_conditions(
        self,
        vix: float,
        vvix: float,
        vix3m: float,
        term_structure_ratio: float,
        avg_spread: float,
    ) -> tuple[bool, str, list[str]]:
        """Evaluate if current conditions are favorable for execution.

        Checks signals in priority order:
        1. VVIX extreme (>130) → Unfavorable (VIX itself unstable)
        2. Term structure backwardation (>1.05) → Unfavorable (fear signal)
        3. VIX > high_threshold → Unfavorable (too volatile)
        4. Spreads > max_spread → Unfavorable (execution quality poor)
        5. Otherwise → Favorable with optional warnings

        Args:
            vix: Current VIX level
            vvix: Current VVIX level
            vix3m: Current 3-month VIX level
            term_structure_ratio: VIX/VIX3M ratio
            avg_spread: Average bid-ask spread across sample contracts

        Returns:
            Tuple of (favorable, reason, warnings)
        """
        warnings: list[str] = []

        # Check 1: VVIX extreme — VIX itself is unstable
        if vvix > self.vvix_extreme_threshold:
            return False, (
                f"VVIX extreme ({vvix:.0f}): VIX unstable, "
                f"entries blocked (threshold: {self.vvix_extreme_threshold:.0f})"
            ), warnings

        # Check 2: Term structure backwardation — fear signal
        if term_structure_ratio > self.term_structure_block_threshold:
            return False, (
                f"Term structure backwardation (ratio: {term_structure_ratio:.2f}): "
                f"market pricing imminent trouble "
                f"(threshold: {self.term_structure_block_threshold:.2f})"
            ), warnings

        # Check 3: VIX too high (market panic/fear)
        if vix > self.vix_high_threshold:
            return False, (
                f"VIX too high: {vix:.1f} "
                f"(threshold: {self.vix_high_threshold})"
            ), warnings

        # Check 4: Spreads too wide (poor execution quality)
        if avg_spread > self.max_spread:
            return False, (
                f"Spreads too wide: ${avg_spread:.3f} "
                f"(threshold: ${self.max_spread:.3f})"
            ), warnings

        # Collect non-fatal warnings
        if vvix > self.vvix_warn_threshold:
            warnings.append(
                f"VVIX elevated ({vvix:.0f}) — VIX may be unstable"
            )

        if term_structure_ratio > 1.0:
            warnings.append(
                f"Mild backwardation (ratio: {term_structure_ratio:.2f}) — "
                f"monitor for deterioration"
            )

        # Conditions favorable
        if vix < self.vix_low_threshold:
            return True, (
                f"VIX low ({vix:.1f}), VVIX={vvix:.0f}, "
                f"spreads tight (${avg_spread:.3f})"
            ), warnings
        else:
            return True, (
                f"VIX moderate ({vix:.1f}), VVIX={vvix:.0f}, "
                f"spreads acceptable (${avg_spread:.3f})"
            ), warnings

    async def _get_volatility_complex(self) -> tuple[float, float, float]:
        """Fetch VIX, VVIX, and VIX3M in parallel from IBKR.

        Batch-qualifies all three Index contracts in one call, then
        fetches quotes in parallel via asyncio.gather.

        Returns:
            Tuple of (vix, vvix, vix3m) with conservative defaults:
            - VIX default: 20.0 (moderate volatility)
            - VVIX default: 90.0 (normal VIX stability)
            - VIX3M default: 22.0 (slightly above VIX default = contango)
        """
        VIX_DEFAULT = 20.0
        VVIX_DEFAULT = 90.0
        VIX3M_DEFAULT = 22.0

        try:
            from ib_async import Index

            contracts = [
                Index("VIX", "CBOE"),
                Index("VVIX", "CBOE"),
                Index("VIX3M", "CBOE"),
            ]

            # Batch-qualify all three in one call
            qualified = await self.client.qualify_contracts_async(*contracts)

            if not qualified:
                logger.warning(
                    "Could not qualify volatility contracts, using defaults"
                )
                return VIX_DEFAULT, VVIX_DEFAULT, VIX3M_DEFAULT

            # Build a map of symbol → qualified contract
            qual_map: dict[str, object] = {}
            for q in qualified:
                if q and hasattr(q, "symbol") and q.conId:
                    qual_map[q.symbol] = q

            # Fetch quotes in parallel for qualified contracts
            async def _safe_quote(symbol: str, default: float) -> float:
                contract = qual_map.get(symbol)
                if not contract:
                    logger.warning(f"No qualified contract for {symbol}, using default {default}")
                    return default
                try:
                    quote = await self.client.get_quote(contract, timeout=3.0)
                    if quote.is_valid and quote.last and quote.last > 0:
                        return quote.last
                    logger.warning(f"Invalid {symbol} quote, using default {default}")
                    return default
                except Exception as e:
                    logger.warning(f"Failed to get {symbol}: {e}")
                    return default

            vix, vvix, vix3m = await asyncio.gather(
                _safe_quote("VIX", VIX_DEFAULT),
                _safe_quote("VVIX", VVIX_DEFAULT),
                _safe_quote("VIX3M", VIX3M_DEFAULT),
            )

            logger.debug(
                f"Volatility complex: VIX={vix:.1f}, "
                f"VVIX={vvix:.0f}, VIX3M={vix3m:.1f}"
            )
            return vix, vvix, vix3m

        except Exception as e:
            logger.error(f"Failed to fetch volatility complex: {e}")
            return VIX_DEFAULT, VVIX_DEFAULT, VIX3M_DEFAULT

    async def _get_spy_price(self) -> float:
        """Get current SPY price from IBKR.

        SPY price is used as a market direction indicator and for
        logging/debugging purposes. Only fetched for US market profile —
        SPY is a US-listed ETF and is not meaningful on other exchanges.

        Returns:
            Current SPY price, or 0.0 if unavailable or not applicable
        """
        from src.config.exchange_profile import get_active_profile

        profile = get_active_profile()
        if profile.code != "US":
            logger.debug(f"SPY not applicable for {profile.code} market — skipping")
            return 0.0

        try:
            from ib_async import Stock

            spy_contract = Stock("SPY", "SMART", "USD")
            qualified = await self.client.qualify_contracts_async(spy_contract)

            if not qualified or not qualified[0].conId:
                logger.debug("Could not qualify SPY contract")
                return 0.0

            # Get quote with qualified contract
            quote = await self.client.get_quote(qualified[0], timeout=3.0)

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
