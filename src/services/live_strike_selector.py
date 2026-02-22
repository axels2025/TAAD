"""Live strike selection using delta-based targeting from IBKR option chains.

Professionals parameterize by delta, not strike. This module resolves the
actual strike at execution time using live market data, replacing the static
OTM%-based strikes from overnight screening.

Integrates between Stage 2 (PremarketValidator) and Tier 1 (RapidFireExecutor)
in the two-tier execution pipeline.
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from src.services.limit_price_calculator import LimitPriceCalculator
from src.services.premarket_validator import StagedOpportunity


@dataclass
class StrikeSelectionConfig:
    """Configuration for adaptive strike selection.

    All values loaded from environment variables with sensible defaults.

    Attributes:
        target_delta: Absolute delta target (default 0.20)
        delta_tolerance: Acceptable +/- range around target (default 0.05)
        min_otm_pct: Minimum OTM% boundary (default 0.10)
        min_premium: Minimum bid premium (default $0.20)
        max_spread_pct: Maximum bid-ask spread as % of mid (default 0.30)
        min_volume: Minimum option volume (default 10)
        min_open_interest: Minimum open interest (default 50)
        max_candidates: Max strikes to evaluate per symbol (default 5)
        fallback_to_otm: Fall back to OTM% selection if delta unavailable (default True)
        enabled: Master switch for adaptive strike selection (default True)
    """

    target_delta: float = 0.20
    delta_tolerance: float = 0.05
    min_otm_pct: float = 0.10
    min_premium: float = 0.20
    max_spread_pct: float = 0.30
    min_volume: int = 10
    min_open_interest: int = 50
    max_candidates: int = 5
    fallback_to_otm: bool = True
    enabled: bool = True

    @classmethod
    def from_env(cls) -> "StrikeSelectionConfig":
        """Load configuration from environment variables.

        Uses system-wide PREMIUM_MIN as the floor for strike selection.

        Returns:
            StrikeSelectionConfig instance with values from .env
        """
        from src.config.base import get_config
        system_premium_min = get_config().premium_min

        return cls(
            target_delta=float(os.getenv("STRIKE_TARGET_DELTA", "0.20")),
            delta_tolerance=float(os.getenv("STRIKE_DELTA_TOLERANCE", "0.05")),
            min_otm_pct=float(os.getenv("MIN_OTM_PCT", "0.10")),
            min_premium=system_premium_min,
            max_spread_pct=float(os.getenv("MAX_EXECUTION_SPREAD_PCT", "0.30")),
            min_volume=int(os.getenv("STRIKE_MIN_VOLUME", "10")),
            min_open_interest=int(os.getenv("STRIKE_MIN_OI", "50")),
            max_candidates=int(os.getenv("STRIKE_MAX_CANDIDATES", "5")),
            fallback_to_otm=os.getenv("STRIKE_FALLBACK_TO_OTM", "true").lower() == "true",
            enabled=os.getenv("ADAPTIVE_STRIKE_ENABLED", "true").lower() == "true",
        )


@dataclass
class StrikeSelectionResult:
    """Result of strike selection for a single opportunity.

    Attributes:
        opportunity: The original staged opportunity (mutated with new values)
        status: SELECTED (new strike), UNCHANGED (kept original), ABANDONED (dropped)
        original_strike: Strike from overnight screening
        selected_strike: Final strike after selection
        selected_delta: Delta of the selected strike
        selected_bid: Bid price at selected strike
        selected_ask: Ask price at selected strike
        selected_otm_pct: OTM% at selected strike
        selected_volume: Option volume at selected strike
        selected_open_interest: Open interest at selected strike
        new_limit_price: Recalculated limit price
        reason: Human-readable explanation
        candidates_evaluated: Number of candidate strikes evaluated
        selection_time_ms: Time taken for selection in milliseconds
    """

    opportunity: StagedOpportunity
    status: str  # "SELECTED", "UNCHANGED", "ABANDONED"
    original_strike: float
    selected_strike: float
    selected_delta: float | None = None
    selected_bid: float | None = None
    selected_ask: float | None = None
    selected_otm_pct: float | None = None
    selected_volume: int | None = None
    selected_open_interest: int | None = None
    new_limit_price: float | None = None
    reason: str = ""
    candidates_evaluated: int = 0
    selection_time_ms: float = 0.0


class LiveStrikeSelector:
    """Select optimal strikes using live IBKR option chains and delta targeting.

    Pulls live option chains, fetches Greeks for candidate strikes, and selects
    the strike closest to the target delta that passes all boundary checks.

    Dependencies:
        - IBKRClient: For option chain data, market data, and Greeks
        - LimitPriceCalculator: For recalculating limit prices
        - StrikeSelectionConfig: For selection parameters

    Example:
        >>> selector = LiveStrikeSelector(ibkr_client)
        >>> results = await selector.select_all(confirmed_trades)
        >>> for r in results:
        ...     print(f"{r.opportunity.symbol}: {r.status} → ${r.selected_strike}")
    """

    def __init__(
        self,
        ibkr_client,
        config: StrikeSelectionConfig | None = None,
        limit_calculator: LimitPriceCalculator | None = None,
    ):
        """Initialize live strike selector.

        Args:
            ibkr_client: Connected IBKRClient instance
            config: Selection configuration (loads from env if None)
            limit_calculator: Limit price calculator (creates if None)
        """
        self.client = ibkr_client
        self.config = config or StrikeSelectionConfig.from_env()
        self.limit_calculator = limit_calculator or LimitPriceCalculator()

        logger.info(
            f"LiveStrikeSelector initialized: "
            f"target_delta={self.config.target_delta}, "
            f"tolerance=±{self.config.delta_tolerance}, "
            f"enabled={self.config.enabled}"
        )

    async def select_all(
        self,
        opportunities: list[StagedOpportunity],
    ) -> list[StrikeSelectionResult]:
        """Run strike selection for all opportunities.

        Processes symbols sequentially to respect IBKR rate limits. Within each
        symbol, candidate strikes are fetched in parallel.

        Args:
            opportunities: List of confirmed opportunities from Stage 2

        Returns:
            List of StrikeSelectionResult for each opportunity
        """
        if not self.config.enabled:
            logger.info("Adaptive strike selection is disabled — using original strikes")
            return [
                StrikeSelectionResult(
                    opportunity=opp,
                    status="UNCHANGED",
                    original_strike=opp.strike,
                    selected_strike=opp.strike,
                    reason="Adaptive strike selection disabled",
                )
                for opp in opportunities
            ]

        logger.info(f"Starting adaptive strike selection for {len(opportunities)} trades")
        results: list[StrikeSelectionResult] = []

        for opp in opportunities:
            result = await self._select_for_symbol(opp)
            results.append(result)

            log_level = "info" if result.status != "ABANDONED" else "warning"
            getattr(logger, log_level)(
                f"  {opp.symbol}: {result.status} — "
                f"${result.original_strike} → ${result.selected_strike} "
                f"(delta={result.selected_delta}, {result.reason})"
            )

        # Summary
        selected = sum(1 for r in results if r.status == "SELECTED")
        unchanged = sum(1 for r in results if r.status == "UNCHANGED")
        abandoned = sum(1 for r in results if r.status == "ABANDONED")

        logger.info(
            f"Strike selection complete: {selected} SELECTED, "
            f"{unchanged} UNCHANGED, {abandoned} ABANDONED"
        )

        return results

    async def _select_for_symbol(
        self,
        opp: StagedOpportunity,
    ) -> StrikeSelectionResult:
        """Run strike selection for a single symbol.

        Steps:
        1. Get live stock price
        2. Get option chain (reqSecDefOptParams) for expiration
        3. Filter to OTM put strikes in range
        4. Get live quotes + Greeks for candidates (parallel batch)
        5. Select strike closest to target delta (0.20 ± 0.05)
        6. Validate: premium >= floor, OTM% >= min, spread OK, liquidity OK
        7. Update StagedOpportunity with new strike/premium/delta/IV

        Args:
            opp: Staged opportunity to select strike for

        Returns:
            StrikeSelectionResult with selection outcome
        """
        start_ms = time.time() * 1000
        original_strike = opp.adjusted_strike or opp.strike

        try:
            # Step 1: Get live stock price
            stock_price = self.client.get_stock_price(opp.symbol)
            if stock_price is None:
                stock_price = opp.current_stock_price or opp.staged_stock_price

            if stock_price is None or stock_price <= 0:
                return StrikeSelectionResult(
                    opportunity=opp,
                    status="UNCHANGED",
                    original_strike=original_strike,
                    selected_strike=original_strike,
                    reason="No stock price available",
                    selection_time_ms=time.time() * 1000 - start_ms,
                )

            # Step 2: Get option chain strikes from IBKR
            chain_strikes = self._get_chain_strikes(opp.symbol, opp.expiration)

            if not chain_strikes:
                return StrikeSelectionResult(
                    opportunity=opp,
                    status="UNCHANGED",
                    original_strike=original_strike,
                    selected_strike=original_strike,
                    reason="No option chain available",
                    selection_time_ms=time.time() * 1000 - start_ms,
                )

            # Step 3: Filter to candidate strikes
            candidates = self._get_candidate_strikes(
                chain_strikes, stock_price, original_strike
            )

            if not candidates:
                return StrikeSelectionResult(
                    opportunity=opp,
                    status="UNCHANGED",
                    original_strike=original_strike,
                    selected_strike=original_strike,
                    reason="No candidate strikes in range",
                    selection_time_ms=time.time() * 1000 - start_ms,
                )

            # Step 4: Get Greeks for candidates
            greeks_data = await self._get_greeks_for_strikes(
                opp.symbol, opp.expiration, candidates
            )

            if not greeks_data:
                if self.config.fallback_to_otm:
                    return StrikeSelectionResult(
                        opportunity=opp,
                        status="UNCHANGED",
                        original_strike=original_strike,
                        selected_strike=original_strike,
                        reason="Greeks unavailable, falling back to OTM% strike",
                        candidates_evaluated=len(candidates),
                        selection_time_ms=time.time() * 1000 - start_ms,
                    )
                else:
                    return StrikeSelectionResult(
                        opportunity=opp,
                        status="ABANDONED",
                        original_strike=original_strike,
                        selected_strike=original_strike,
                        reason="Greeks unavailable, fallback disabled",
                        candidates_evaluated=len(candidates),
                        selection_time_ms=time.time() * 1000 - start_ms,
                    )

            # Step 5: Select best strike by delta
            best = self._select_best_strike(greeks_data, stock_price)

            if best is None:
                if self.config.fallback_to_otm:
                    return StrikeSelectionResult(
                        opportunity=opp,
                        status="UNCHANGED",
                        original_strike=original_strike,
                        selected_strike=original_strike,
                        reason="No strike meets delta/boundary criteria, keeping original",
                        candidates_evaluated=len(greeks_data),
                        selection_time_ms=time.time() * 1000 - start_ms,
                    )
                else:
                    return StrikeSelectionResult(
                        opportunity=opp,
                        status="ABANDONED",
                        original_strike=original_strike,
                        selected_strike=original_strike,
                        reason="No strike meets delta/boundary criteria",
                        candidates_evaluated=len(greeks_data),
                        selection_time_ms=time.time() * 1000 - start_ms,
                    )

            best_strike, best_data = best

            # Step 6: Recalculate limit price with live bid/ask
            new_limit = self.limit_calculator.calculate_sell_limit(
                best_data["bid"], best_data["ask"]
            )

            # Step 7: Update the opportunity with new values
            selected_otm_pct = (stock_price - best_strike) / stock_price

            opp.adjusted_strike = best_strike
            opp.adjusted_limit_price = new_limit
            opp.otm_pct = selected_otm_pct
            opp.live_delta = best_data["delta"]
            opp.live_iv = best_data.get("iv")
            opp.live_gamma = best_data.get("gamma")
            opp.live_theta = best_data.get("theta")
            opp.live_volume = best_data.get("volume")
            opp.live_open_interest = best_data.get("oi")
            opp.strike_selection_method = "delta"

            status = "SELECTED" if best_strike != original_strike else "UNCHANGED"
            reason = (
                f"delta={best_data['delta']:.3f} "
                f"(target={self.config.target_delta}), "
                f"OTM={selected_otm_pct:.1%}"
            )

            return StrikeSelectionResult(
                opportunity=opp,
                status=status,
                original_strike=original_strike,
                selected_strike=best_strike,
                selected_delta=best_data["delta"],
                selected_bid=best_data["bid"],
                selected_ask=best_data["ask"],
                selected_otm_pct=selected_otm_pct,
                selected_volume=best_data.get("volume"),
                selected_open_interest=best_data.get("oi"),
                new_limit_price=new_limit,
                reason=reason,
                candidates_evaluated=len(greeks_data),
                selection_time_ms=time.time() * 1000 - start_ms,
            )

        except Exception as e:
            logger.error(f"Strike selection failed for {opp.symbol}: {e}", exc_info=True)
            return StrikeSelectionResult(
                opportunity=opp,
                status="UNCHANGED",
                original_strike=original_strike,
                selected_strike=original_strike,
                reason=f"Error: {e}",
                selection_time_ms=time.time() * 1000 - start_ms,
            )

    def _get_chain_strikes(
        self,
        symbol: str,
        expiration: str,
    ) -> list[float]:
        """Get available strikes from IBKR option chain.

        Uses reqSecDefOptParams to get the full chain, then filters to
        strikes available for the target expiration.

        Args:
            symbol: Stock symbol
            expiration: Expiration date (YYYY-MM-DD format)

        Returns:
            Sorted list of available strikes, or empty list
        """
        try:
            from ib_insync import Stock

            stock_contract = Stock(symbol, "SMART", "USD")
            qualified = self.client.qualify_contract(stock_contract)

            if not qualified:
                logger.debug(f"{symbol}: Could not qualify stock contract")
                return []

            chains = self.client.ib.reqSecDefOptParams(
                qualified.symbol,
                "",
                qualified.secType,
                qualified.conId,
            )

            if not chains:
                logger.debug(f"{symbol}: No option chains returned")
                return []

            # Convert expiration format: "2026-02-20" → "20260220"
            exp_yyyymmdd = expiration.replace("-", "")

            # Find chain(s) that contain our expiration
            all_strikes = set()
            for chain in chains:
                if exp_yyyymmdd in chain.expirations:
                    all_strikes.update(chain.strikes)

            if not all_strikes:
                # Fallback: use strikes from any SMART chain
                for chain in chains:
                    if chain.exchange == "SMART":
                        all_strikes.update(chain.strikes)
                        break

            return sorted(all_strikes)

        except Exception as e:
            logger.debug(f"{symbol}: Error getting option chain: {e}")
            return []

    def _get_candidate_strikes(
        self,
        chain_strikes: list[float],
        stock_price: float,
        current_strike: float,
    ) -> list[float]:
        """Filter chain to OTM put strikes in the evaluation range.

        Selects strikes that are:
        - Below stock price (OTM for puts)
        - Above minimum OTM% threshold
        - Centered around the current/adjusted strike
        - Limited to max_candidates

        Args:
            chain_strikes: All available strikes from chain
            stock_price: Current stock price
            current_strike: Current strike from staging/adjustment

        Returns:
            List of candidate strikes to evaluate (max max_candidates)
        """
        # Filter to OTM puts with minimum OTM%
        min_strike = 0
        max_strike = stock_price * (1 - self.config.min_otm_pct)

        otm_strikes = [s for s in chain_strikes if min_strike < s <= max_strike]

        if not otm_strikes:
            return []

        # Sort by distance from current strike (prefer nearby)
        otm_strikes.sort(key=lambda s: abs(s - current_strike))

        # Take top max_candidates
        candidates = otm_strikes[: self.config.max_candidates]

        # Sort ascending for readability
        return sorted(candidates)

    async def _get_greeks_for_strikes(
        self,
        symbol: str,
        expiration: str,
        strikes: list[float],
    ) -> dict[float, dict]:
        """Get Greeks and market data for candidate strikes.

        Uses ib.reqMktData() on qualified option contracts, reads
        ticker.modelGreeks.delta after event-driven wait. Cancels
        subscriptions immediately after reading.

        Args:
            symbol: Stock symbol
            expiration: Expiration date (YYYY-MM-DD format)
            strikes: List of candidate strikes

        Returns:
            Dict mapping strike → {delta, iv, gamma, theta, bid, ask, volume, oi}
            Only includes strikes where Greeks were successfully retrieved.
        """
        exp_yyyymmdd = expiration.replace("-", "")
        results: dict[float, dict] = {}

        # Build and qualify contracts for all candidate strikes
        contracts = []
        for strike in strikes:
            contract = self.client.get_option_contract(
                symbol=symbol,
                expiration=exp_yyyymmdd,
                strike=strike,
                right="P",
            )
            contracts.append((strike, contract))

        # Qualify all at once
        raw_contracts = [c for _, c in contracts]
        try:
            qualified_list = await self.client.qualify_contracts_async(*raw_contracts)
        except Exception as e:
            logger.debug(f"{symbol}: Failed to qualify candidate contracts: {e}")
            return {}

        # Map qualified contracts back to strikes
        qualified_map: dict[float, any] = {}
        for (strike, _), qualified in zip(contracts, qualified_list):
            if qualified and qualified.conId:
                qualified_map[strike] = qualified

        if not qualified_map:
            return {}

        # Request market data with Greeks for all candidates
        tickers: dict[float, any] = {}
        for strike, contract in qualified_map.items():
            try:
                ticker = self.client.ib.reqMktData(contract, "", False, False)
                tickers[strike] = (ticker, contract)
            except Exception as e:
                logger.debug(f"{symbol} ${strike}: reqMktData failed: {e}")

        # Wait for Greeks to populate (up to 5 seconds).
        # At market open, IBKR needs time for market makers to post quotes
        # before modelGreeks can compute delta. 3s was too short — many
        # options don't have valid Greeks until ~10s after open.
        greeks_timeout = float(os.getenv("GREEKS_WAIT_TIMEOUT", "5.0"))
        wait_iterations = int(greeks_timeout / 0.5)
        for _ in range(wait_iterations):
            self.client.ib.sleep(0.5)

            # Check if all tickers have Greeks
            all_have_greeks = all(
                hasattr(t, "modelGreeks") and t.modelGreeks and t.modelGreeks.delta is not None
                for t, _ in tickers.values()
            )
            if all_have_greeks:
                break

        # Count how many tickers got Greeks
        got_greeks = sum(
            1 for t, _ in tickers.values()
            if hasattr(t, "modelGreeks") and t.modelGreeks and t.modelGreeks.delta is not None
        )
        if got_greeks < len(tickers):
            logger.info(
                f"  {symbol}: Greeks received for {got_greeks}/{len(tickers)} strikes "
                f"(waited {greeks_timeout}s)"
            )

        # Read data and cancel subscriptions
        for strike, (ticker, contract) in tickers.items():
            try:
                data: dict = {
                    "delta": None,
                    "iv": None,
                    "gamma": None,
                    "theta": None,
                    "bid": None,
                    "ask": None,
                    "volume": None,
                    "oi": None,
                }

                # Greeks from modelGreeks
                if hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
                    greeks = ticker.modelGreeks
                    if greeks.delta is not None:
                        data["delta"] = abs(greeks.delta)  # Absolute value for puts
                    data["iv"] = greeks.impliedVol
                    data["gamma"] = greeks.gamma
                    data["theta"] = greeks.theta

                # Market data
                from src.utils.market_data import safe_field

                bid = safe_field(ticker, "bid")
                ask = safe_field(ticker, "ask")
                if bid is not None and bid > 0:
                    data["bid"] = bid
                if ask is not None and ask > 0:
                    data["ask"] = ask

                vol = safe_field(ticker, "volume")
                if vol is not None and vol >= 0:
                    data["volume"] = int(vol)

                oi = safe_field(ticker, "openInterest")
                if oi is not None and oi >= 0:
                    data["oi"] = int(oi)

                # Only include if we got delta
                if data["delta"] is not None:
                    results[strike] = data

            except Exception as e:
                logger.debug(f"{symbol} ${strike}: Error reading Greeks: {e}")
            finally:
                try:
                    self.client.ib.cancelMktData(contract)
                except Exception:
                    pass

        return results

    def _select_best_strike(
        self,
        candidates: dict[float, dict],
        stock_price: float,
    ) -> tuple[float, dict] | None:
        """Select the strike closest to target delta that passes all boundaries.

        Boundary checks:
        - Delta within target ± tolerance
        - Premium (bid) >= min_premium
        - OTM% >= min_otm_pct
        - Spread % <= max_spread_pct (if both bid and ask available)
        - Volume >= min_volume (if available)
        - Open interest >= min_open_interest (if available)

        Args:
            candidates: Dict of strike → Greeks/market data
            stock_price: Current stock price

        Returns:
            Tuple of (best_strike, data_dict) or None if no candidate passes
        """
        target = self.config.target_delta
        tolerance = self.config.delta_tolerance

        passing: list[tuple[float, dict, float]] = []  # (strike, data, delta_distance)

        for strike, data in candidates.items():
            delta = data["delta"]

            # Check delta range
            if abs(delta - target) > tolerance:
                logger.debug(
                    f"  ${strike}: delta={delta:.3f} outside range "
                    f"[{target - tolerance:.2f}, {target + tolerance:.2f}]"
                )
                continue

            # Check premium floor
            bid = data.get("bid")
            if bid is None or bid < self.config.min_premium:
                logger.debug(f"  ${strike}: bid=${bid} below min ${self.config.min_premium}")
                continue

            # Check OTM%
            otm_pct = (stock_price - strike) / stock_price
            if otm_pct < self.config.min_otm_pct:
                logger.debug(f"  ${strike}: OTM={otm_pct:.1%} below min {self.config.min_otm_pct:.0%}")
                continue

            # Check spread
            ask = data.get("ask")
            if bid and ask and ask > 0:
                mid = (bid + ask) / 2
                if mid > 0:
                    spread_pct = (ask - bid) / mid
                    if spread_pct > self.config.max_spread_pct:
                        logger.debug(
                            f"  ${strike}: spread={spread_pct:.0%} exceeds "
                            f"max {self.config.max_spread_pct:.0%}"
                        )
                        continue

            # Check liquidity: OI is a reliable filter (set overnight).
            # Volume is NOT checked — at 9:30 AM most options have 0 volume
            # because nobody has traded yet. Filtering by volume at open
            # rejects every strike.
            oi = data.get("oi")
            if oi is not None and oi < self.config.min_open_interest:
                logger.debug(f"  ${strike}: OI={oi} below min {self.config.min_open_interest}")
                continue

            # Passed all checks
            delta_distance = abs(delta - target)
            passing.append((strike, data, delta_distance))

        if not passing:
            # Log why no candidates passed — this is important for diagnosing
            # "No strike meets delta/boundary criteria" at market open
            if candidates:
                sample = list(candidates.items())[:3]
                for strike, data in sample:
                    delta = data["delta"]
                    bid = data.get("bid")
                    ask = data.get("ask")
                    oi = data.get("oi")
                    logger.info(
                        f"    Rejected ${strike}: delta={delta:.3f} "
                        f"(need {target - tolerance:.2f}-{target + tolerance:.2f}), "
                        f"bid=${bid}, ask=${ask}, OI={oi}"
                    )
            return None

        # Sort by delta distance (closest to target wins)
        passing.sort(key=lambda x: x[2])
        best_strike, best_data, _ = passing[0]

        return best_strike, best_data
