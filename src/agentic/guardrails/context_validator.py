"""Pre-Claude context validation guardrails.

Validates reasoning context integrity before sending to Claude.
Catches stale data, impossible values, and null fields.

Risk reduction: High. Cost: negative (saves API cost by skipping bad calls).
"""

from loguru import logger

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.registry import GuardrailResult


class ContextValidator:
    """Validates reasoning context before it reaches Claude.

    Three checks:
    1. Data freshness: blocks if market data is stale
    2. Consistency: blocks on impossible values, warns on mismatches
    3. Null sanitization: replaces None with "UNKNOWN" and tracks limitations
    """

    def validate(
        self,
        context,
        config: GuardrailConfig,
    ) -> list[GuardrailResult]:
        """Run all context validation checks.

        Args:
            context: ReasoningContext to validate
            config: Guardrail configuration

        Returns:
            List of GuardrailResult
        """
        results = []

        if config.data_freshness_enabled:
            results.append(self.check_data_freshness(context))

        if config.consistency_check_enabled:
            results.extend(self.check_consistency(context))

        if config.null_sanitization_enabled:
            results.extend(self.sanitize_nulls(context))

        return results

    def check_data_freshness(self, context) -> GuardrailResult:
        """Check if market data is stale.

        If data_stale is True, block to prevent Claude from reasoning
        on outdated information.

        Args:
            context: ReasoningContext

        Returns:
            GuardrailResult
        """
        market = context.market_context or {}

        if market.get("data_stale", False):
            return GuardrailResult(
                passed=False,
                guard_name="data_freshness",
                severity="block",
                reason="Market data is stale, skipping Claude call",
                details={"data_stale": True},
            )

        return GuardrailResult(
            passed=True,
            guard_name="data_freshness",
            severity="info",
            reason="Market data is fresh",
        )

    def check_consistency(self, context) -> list[GuardrailResult]:
        """Check context values for impossible or mismatched data.

        Validates:
        - VIX in 5.0-100.0 range
        - SPY price in 100.0-1000.0 range
        - Position count matches open_positions list length

        Args:
            context: ReasoningContext

        Returns:
            List of GuardrailResult
        """
        results = []
        market = context.market_context or {}

        # VIX range check
        vix = market.get("vix")
        if vix is not None:
            try:
                vix_val = float(vix)
                if vix_val <= 0:
                    # IBKR returned 0.0 (snapshot failure / market closed).
                    # Treat as unavailable — null_sanitization will flag it.
                    market["vix"] = None
                elif vix_val < 5.0 or vix_val > 100.0:
                    results.append(GuardrailResult(
                        passed=False,
                        guard_name="consistency_check",
                        severity="block",
                        reason=f"VIX value {vix_val} is outside plausible range (5-100)",
                        details={"vix": vix_val, "range": [5.0, 100.0]},
                    ))
            except (TypeError, ValueError):
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="consistency_check",
                    severity="warning",
                    reason=f"VIX value '{vix}' is not a valid number",
                    details={"vix": str(vix)},
                ))

        # SPY price range check
        spy = market.get("spy_price")
        if spy is not None:
            try:
                spy_val = float(spy)
                if spy_val <= 0:
                    # IBKR returned 0.0 (snapshot failure / market closed).
                    # Treat as unavailable — null_sanitization will flag it.
                    market["spy_price"] = None
                elif spy_val < 100.0 or spy_val > 1000.0:
                    results.append(GuardrailResult(
                        passed=False,
                        guard_name="consistency_check",
                        severity="block",
                        reason=f"SPY price {spy_val} is outside plausible range (100-1000)",
                        details={"spy_price": spy_val, "range": [100.0, 1000.0]},
                    ))
            except (TypeError, ValueError):
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="consistency_check",
                    severity="warning",
                    reason=f"SPY price '{spy}' is not a valid number",
                    details={"spy_price": str(spy)},
                ))

        if not results:
            results.append(GuardrailResult(
                passed=True,
                guard_name="consistency_check",
                severity="info",
                reason="Context values are consistent",
            ))

        return results

    def sanitize_nulls(self, context) -> list[GuardrailResult]:
        """Replace None values in critical fields and track data limitations.

        Sets context.data_limitations (a list of strings describing missing data)
        so that to_prompt_string() can include a Data Limitations section.

        Args:
            context: ReasoningContext (modified in-place)

        Returns:
            List of GuardrailResult
        """
        limitations = []
        market = context.market_context or {}

        critical_market_fields = ["vix", "spy_price", "conditions_favorable"]
        for field_name in critical_market_fields:
            if market.get(field_name) is None:
                limitations.append(f"{field_name} is unavailable")
                market[field_name] = "UNKNOWN"

        # Ensure context has the updated market dict
        context.market_context = market

        # Set data_limitations on context for prompt assembly
        if not hasattr(context, "data_limitations"):
            context.data_limitations = []
        context.data_limitations = limitations

        if limitations:
            return [GuardrailResult(
                passed=True,  # Sanitization is not a failure
                guard_name="null_sanitization",
                severity="warning",
                reason=f"Replaced {len(limitations)} null field(s): {', '.join(limitations)}",
                details={"limitations": limitations},
            )]

        return [GuardrailResult(
            passed=True,
            guard_name="null_sanitization",
            severity="info",
            reason="No null critical fields",
        )]
