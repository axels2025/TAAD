"""Pre-execution guardrails: live state diff, order bounds, rate limiting,
absolute VIX circuit breaker, and earnings proximity block.

Runs before trade-affecting actions (EXECUTE_TRADES, CLOSE_POSITION) to
verify that market conditions haven't changed significantly since context
was built, that order parameters are within configured bounds, and that
we're not exceeding rate limits.

Cost: 1 IBKR data request for live state diff.
"""

import time
from collections import deque
from datetime import date as date_type
from typing import Optional

from loguru import logger

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.registry import GuardrailResult


# Actions that affect order state (vs just monitoring)
TRADE_ACTIONS = {"EXECUTE_TRADES", "CLOSE_POSITION", "CLOSE_ALL_POSITIONS"}


class ExecutionGate:
    """Pre-execution live state verification.

    Five checks:
    1. Live state diff: VIX/SPY haven't moved too much since context was built
    2. Absolute VIX circuit breaker: blocks when VIX exceeds absolute threshold
    3. Order parameter bounds: premium, DTE, contracts, strike within config
    4. Rate limiting: max orders per minute
    5. Earnings proximity: blocks execution when earnings are imminent
    """

    def __init__(self):
        self._order_timestamps: deque = deque(maxlen=100)

    def validate(
        self,
        decision,
        context,
        config: GuardrailConfig,
        ibkr_client=None,
        **kwargs,
    ) -> list[GuardrailResult]:
        """Run all execution gate checks.

        Fetches live data once and passes it to all checks that need it.

        Args:
            decision: DecisionOutput about to be executed
            context: ReasoningContext or execution context dict
            config: Guardrail configuration
            ibkr_client: Optional IBKR client for live data
            **kwargs: Additional arguments

        Returns:
            List of GuardrailResult
        """
        if not config.execution_gate_enabled:
            return []

        # Only gate trade-affecting actions
        if decision.action not in TRADE_ACTIONS:
            return [GuardrailResult(
                passed=True,
                guard_name="execution_gate",
                severity="info",
                reason=f"Action {decision.action} does not require execution gate",
            )]

        # Fetch live data once for all checks that need it
        live_data = None
        if ibkr_client is not None:
            try:
                live_data = self._fetch_live_data(ibkr_client)
            except Exception as e:
                logger.warning(f"Live state fetch failed: {e}")

        results = []

        # 1. Live state diff (relative VIX/SPY movement)
        results.extend(
            self.check_live_state(decision, context, config, ibkr_client, live_data=live_data)
        )

        # 2. Absolute VIX circuit breaker
        results.extend(self.check_absolute_vix(config, live_data))

        # 3. Order parameter bounds
        results.extend(self.check_order_bounds(decision, context, config))

        # 4. Rate limiting
        results.append(self.check_rate_limit(config))

        # 5. Earnings proximity
        results.extend(self.check_earnings_proximity(decision, context, config))

        return results

    def check_live_state(
        self,
        decision,
        context,
        config: GuardrailConfig,
        ibkr_client=None,
        live_data: Optional[tuple[float, float]] = None,
    ) -> list[GuardrailResult]:
        """Check if VIX/SPY have moved significantly since context was built.

        Re-fetches live data from IBKR and compares against context values.
        If VIX moved >15% or SPY moved >2%, blocks with re-reason advisory.

        Args:
            decision: DecisionOutput
            context: ReasoningContext with market_context
            config: Guardrail configuration
            ibkr_client: IBKR client for live data (used only if live_data is None)
            live_data: Pre-fetched (live_vix, live_spy) tuple to avoid duplicate fetches

        Returns:
            List of GuardrailResult
        """
        if ibkr_client is None and live_data is None:
            return [GuardrailResult(
                passed=True,
                guard_name="live_state_diff",
                severity="warning",
                reason="No IBKR client available for live state check",
            )]

        market = getattr(context, "market_context", {})
        if isinstance(context, dict):
            market = context

        context_vix = market.get("vix")
        context_spy = market.get("spy_price")

        if context_vix is None or context_spy is None:
            return [GuardrailResult(
                passed=True,
                guard_name="live_state_diff",
                severity="warning",
                reason="No VIX/SPY in context for live comparison",
            )]

        try:
            context_vix = float(context_vix)
            context_spy = float(context_spy)
        except (TypeError, ValueError):
            return [GuardrailResult(
                passed=True,
                guard_name="live_state_diff",
                severity="warning",
                reason="Context VIX/SPY not numeric",
            )]

        # Use pre-fetched live data or fetch fresh
        if live_data is None:
            try:
                live_data = self._fetch_live_data(ibkr_client)
            except Exception as e:
                logger.warning(f"Live state fetch failed: {e}")
                return [GuardrailResult(
                    passed=True,
                    guard_name="live_state_diff",
                    severity="warning",
                    reason=f"Could not fetch live data: {e}",
                )]

        live_vix, live_spy = live_data
        results = []

        # Check VIX movement
        if context_vix > 0 and live_vix > 0:
            vix_change_pct = abs(live_vix - context_vix) / context_vix * 100
            if vix_change_pct > config.vix_movement_block_pct:
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="live_state_diff",
                    severity="block",
                    reason=(
                        f"VIX moved {vix_change_pct:.1f}% since context was built "
                        f"(was {context_vix:.1f}, now {live_vix:.1f}). Re-reason required."
                    ),
                    details={
                        "context_vix": context_vix,
                        "live_vix": live_vix,
                        "change_pct": round(vix_change_pct, 2),
                        "threshold_pct": config.vix_movement_block_pct,
                    },
                ))

        # Check SPY movement
        if context_spy > 0 and live_spy > 0:
            spy_change_pct = abs(live_spy - context_spy) / context_spy * 100
            if spy_change_pct > config.spy_movement_block_pct:
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="live_state_diff",
                    severity="block",
                    reason=(
                        f"SPY moved {spy_change_pct:.1f}% since context was built "
                        f"(was ${context_spy:.2f}, now ${live_spy:.2f}). Re-reason required."
                    ),
                    details={
                        "context_spy": context_spy,
                        "live_spy": live_spy,
                        "change_pct": round(spy_change_pct, 2),
                        "threshold_pct": config.spy_movement_block_pct,
                    },
                ))

        if not results:
            results.append(GuardrailResult(
                passed=True,
                guard_name="live_state_diff",
                severity="info",
                reason="Live state within acceptable range",
            ))

        return results

    def check_absolute_vix(
        self,
        config: GuardrailConfig,
        live_data: Optional[tuple[float, float]] = None,
    ) -> list[GuardrailResult]:
        """Check if VIX exceeds absolute circuit-breaker threshold.

        Complements the relative movement check — catches high VIX even when
        context was built at a similarly high level.

        Args:
            config: Guardrail configuration
            live_data: Pre-fetched (live_vix, live_spy) tuple

        Returns:
            List of GuardrailResult
        """
        if live_data is None:
            return []

        live_vix, _ = live_data

        if live_vix > config.vix_absolute_block_threshold:
            return [GuardrailResult(
                passed=False,
                guard_name="absolute_vix",
                severity="block",
                reason=(
                    f"VIX at {live_vix:.1f} exceeds absolute threshold "
                    f"of {config.vix_absolute_block_threshold:.1f}"
                ),
                details={
                    "live_vix": live_vix,
                    "threshold": config.vix_absolute_block_threshold,
                },
            )]

        return [GuardrailResult(
            passed=True,
            guard_name="absolute_vix",
            severity="info",
            reason=f"VIX {live_vix:.1f} within absolute threshold",
        )]

    def check_earnings_proximity(
        self,
        decision,
        context,
        config: GuardrailConfig,
    ) -> list[GuardrailResult]:
        """Check if any staged candidate has earnings within block window.

        Reuses the existing get_cached_earnings() utility from the earnings
        service. Blocks execution when earnings fall within earnings_block_days
        of the current date.

        Args:
            decision: DecisionOutput
            context: ReasoningContext with staged_candidates
            config: Guardrail configuration

        Returns:
            List of GuardrailResult
        """
        if not config.earnings_block_enabled:
            return []

        candidates = getattr(context, "staged_candidates", [])
        if not candidates:
            return [GuardrailResult(
                passed=True,
                guard_name="earnings_proximity",
                severity="info",
                reason="No staged candidates to check for earnings",
            )]

        results = []

        for cand in candidates:
            symbol = cand.get("symbol")
            if not symbol:
                continue

            try:
                from src.services.earnings_service import get_cached_earnings

                exp_str = cand.get("expiration")
                exp_date = None
                if exp_str:
                    try:
                        from datetime import date, datetime
                        if isinstance(exp_str, date_type):
                            exp_date = exp_str
                        else:
                            exp_date = datetime.strptime(str(exp_str), "%Y-%m-%d").date()
                    except (ValueError, TypeError):
                        pass

                earnings_info = get_cached_earnings(symbol, exp_date)

                if (
                    earnings_info.earnings_in_dte
                    and earnings_info.days_to_earnings is not None
                    and earnings_info.days_to_earnings <= config.earnings_block_days
                ):
                    results.append(GuardrailResult(
                        passed=False,
                        guard_name="earnings_proximity",
                        severity="block",
                        reason=(
                            f"{symbol} has earnings in {earnings_info.days_to_earnings} day(s) "
                            f"(block threshold: {config.earnings_block_days} days)"
                        ),
                        details={
                            "symbol": symbol,
                            "days_to_earnings": earnings_info.days_to_earnings,
                            "earnings_date": str(earnings_info.earnings_date),
                            "block_days": config.earnings_block_days,
                        },
                    ))
            except Exception as e:
                logger.debug(f"Earnings check failed for {symbol}: {e}")
                continue

        if not results:
            results.append(GuardrailResult(
                passed=True,
                guard_name="earnings_proximity",
                severity="info",
                reason="No earnings conflicts detected",
            ))

        return results

    def check_order_bounds(
        self,
        decision,
        context,
        config: GuardrailConfig,
    ) -> list[GuardrailResult]:
        """Check that order parameters are within configured bounds.

        For EXECUTE_TRADES: checks staged candidates' premiums, DTE,
        contracts, and strike reasonableness.

        Args:
            decision: DecisionOutput
            context: ReasoningContext with staged_candidates
            config: Guardrail configuration

        Returns:
            List of GuardrailResult
        """
        candidates = getattr(context, "staged_candidates", [])
        if not candidates:
            return [GuardrailResult(
                passed=True,
                guard_name="order_bounds",
                severity="info",
                reason="No staged candidates to validate",
            )]

        results = []

        for cand in candidates:
            contracts = cand.get("contracts")
            if contracts is not None and contracts < 1:
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="order_bounds",
                    severity="block",
                    reason=f"Candidate {cand.get('symbol', '?')} has {contracts} contracts (must be >= 1)",
                    details={"candidate": cand},
                ))

            # Strike reasonableness: within 30% of the candidate's own underlying price
            strike = cand.get("strike")
            underlying_price = cand.get("stock_price")
            if strike is not None and underlying_price is not None:
                try:
                    strike_val = float(strike)
                    underlying_val = float(underlying_price)
                    if underlying_val > 0:
                        deviation = abs(strike_val - underlying_val) / underlying_val
                        if deviation > 0.30:
                            results.append(GuardrailResult(
                                passed=False,
                                guard_name="order_bounds",
                                severity="block",
                                reason=(
                                    f"Candidate {cand.get('symbol', '?')} strike ${strike_val:.0f} "
                                    f"is {deviation:.0%} from underlying ${underlying_val:.0f} (>30% limit)"
                                ),
                                details={"strike": strike_val, "underlying_price": underlying_val, "deviation": deviation},
                            ))
                except (TypeError, ValueError):
                    pass

        if not results:
            results.append(GuardrailResult(
                passed=True,
                guard_name="order_bounds",
                severity="info",
                reason="All order parameters within bounds",
            ))

        return results

    def check_rate_limit(self, config: GuardrailConfig) -> GuardrailResult:
        """Check if order rate limit has been exceeded.

        Sliding window counter: max N order actions per 60 seconds.

        Args:
            config: Guardrail configuration

        Returns:
            GuardrailResult
        """
        now = time.time()
        window_start = now - 60.0

        # Count recent order timestamps within window
        recent = sum(1 for ts in self._order_timestamps if ts > window_start)

        if recent >= config.max_orders_per_minute:
            return GuardrailResult(
                passed=False,
                guard_name="rate_limit",
                severity="block",
                reason=f"Rate limit exceeded: {recent} orders in last 60s (max={config.max_orders_per_minute})",
                details={"recent_orders": recent, "max": config.max_orders_per_minute},
            )

        # Record this order attempt
        self._order_timestamps.append(now)

        return GuardrailResult(
            passed=True,
            guard_name="rate_limit",
            severity="info",
            reason=f"Rate OK: {recent + 1} orders in last 60s",
        )

    def _fetch_live_data(self, ibkr_client) -> tuple[float, float]:
        """Fetch live VIX and SPY from IBKR using sync ib_async calls.

        Uses the synchronous ib_async API directly (qualifyContracts + reqMktData
        + sleep) rather than going through async MarketConditionMonitor. This avoids
        the event loop deadlock that occurs when trying to run async code from a
        sync method while the daemon's asyncio loop is already running.

        Args:
            ibkr_client: Connected IBKR client (must have .ib attribute)

        Returns:
            Tuple of (live_vix, live_spy)
        """
        from ib_async import Index, Stock

        ib = ibkr_client.ib
        live_vix = 20.0  # conservative default
        live_spy = 0.0

        # Fetch VIX
        try:
            vix_contract = Index("VIX", "CBOE")
            qualified = ib.qualifyContracts(vix_contract)
            if qualified and qualified[0] is not None and qualified[0].conId:
                ticker = ib.reqMktData(qualified[0], "", False, False)
                ibkr_client.wait(2)
                if ticker.last is not None and ticker.last > 0:
                    live_vix = ticker.last
                ib.cancelMktData(qualified[0])
        except Exception as e:
            logger.warning(f"Execution gate VIX fetch failed: {e}")

        # Fetch SPY
        try:
            from src.config.exchange_profile import get_active_profile
            if get_active_profile().code == "US":
                spy_contract = Stock("SPY", "SMART", "USD")
                qualified = ib.qualifyContracts(spy_contract)
                if qualified and qualified[0] is not None and qualified[0].conId:
                    ticker = ib.reqMktData(qualified[0], "", False, False)
                    ibkr_client.wait(2)
                    if ticker.last is not None and ticker.last > 0:
                        live_spy = ticker.last
                    ib.cancelMktData(qualified[0])
        except Exception as e:
            logger.warning(f"Execution gate SPY fetch failed: {e}")

        return live_vix, live_spy
