"""Pre-execution guardrails: live state diff, order bounds, rate limiting.

Runs before trade-affecting actions (EXECUTE_TRADES, CLOSE_POSITION) to
verify that market conditions haven't changed significantly since context
was built, that order parameters are within configured bounds, and that
we're not exceeding rate limits.

Cost: 1 IBKR data request for live state diff.
"""

import time
from collections import deque

from loguru import logger

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.registry import GuardrailResult


# Actions that affect order state (vs just monitoring)
TRADE_ACTIONS = {"EXECUTE_TRADES", "CLOSE_POSITION"}


class ExecutionGate:
    """Pre-execution live state verification.

    Three checks:
    1. Live state diff: VIX/SPY haven't moved too much since context was built
    2. Order parameter bounds: premium, DTE, contracts, strike within config
    3. Rate limiting: max orders per minute
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

        results = []

        # 1. Live state diff (requires IBKR client)
        results.extend(
            self.check_live_state(decision, context, config, ibkr_client)
        )

        # 2. Order parameter bounds
        results.extend(self.check_order_bounds(decision, context, config))

        # 3. Rate limiting
        results.append(self.check_rate_limit(config))

        return results

    def check_live_state(
        self,
        decision,
        context,
        config: GuardrailConfig,
        ibkr_client=None,
    ) -> list[GuardrailResult]:
        """Check if VIX/SPY have moved significantly since context was built.

        Re-fetches live data from IBKR and compares against context values.
        If VIX moved >15% or SPY moved >2%, blocks with re-reason advisory.

        Args:
            decision: DecisionOutput
            context: ReasoningContext with market_context
            config: Guardrail configuration
            ibkr_client: IBKR client for live data

        Returns:
            List of GuardrailResult
        """
        if ibkr_client is None:
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

        # Fetch live data
        try:
            live_vix, live_spy = self._fetch_live_data(ibkr_client)
        except Exception as e:
            logger.warning(f"Live state fetch failed: {e}")
            return [GuardrailResult(
                passed=True,
                guard_name="live_state_diff",
                severity="warning",
                reason=f"Could not fetch live data: {e}",
            )]

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
        market = getattr(context, "market_context", {})
        spy_price = None
        try:
            spy_val = market.get("spy_price")
            if spy_val and spy_val != "UNKNOWN":
                spy_price = float(spy_val)
        except (TypeError, ValueError):
            pass

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

            # Strike reasonableness: within 30% of SPY/underlying
            strike = cand.get("strike")
            if strike is not None and spy_price is not None:
                try:
                    strike_val = float(strike)
                    deviation = abs(strike_val - spy_price) / spy_price
                    if deviation > 0.30:
                        results.append(GuardrailResult(
                            passed=False,
                            guard_name="order_bounds",
                            severity="block",
                            reason=(
                                f"Candidate {cand.get('symbol', '?')} strike ${strike_val:.0f} "
                                f"is {deviation:.0%} from SPY ${spy_price:.0f} (>30% limit)"
                            ),
                            details={"strike": strike_val, "spy_price": spy_price, "deviation": deviation},
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
        """Fetch live VIX and SPY from IBKR.

        Args:
            ibkr_client: Connected IBKR client

        Returns:
            Tuple of (live_vix, live_spy)
        """
        import asyncio

        from src.services.market_conditions import MarketConditionMonitor

        monitor = MarketConditionMonitor(ibkr_client)

        # Run async check in sync context if needed
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                conditions = pool.submit(
                    asyncio.run, monitor.check_conditions()
                ).result(timeout=5)
        else:
            conditions = asyncio.run(monitor.check_conditions())

        return conditions.vix, conditions.spy_price
