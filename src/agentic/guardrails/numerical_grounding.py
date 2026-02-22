"""Numerical grounding checker: verifies numbers in reasoning match context.

Extracts numerical claims from Claude's reasoning text and compares them
against the actual values in the ReasoningContext. Flags mismatches that
suggest hallucinated numbers.

Zero additional Claude API calls. All pure Python/regex logic.
"""

import re

from loguru import logger

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.registry import GuardrailResult


# Regex patterns for extracting numerical claims from reasoning text
_PATTERNS = [
    # "VIX is 22.5", "VIX at 22.5", "VIX of 22.5", "VIX = 22.5"
    (r"VIX\s+(?:is|at|of|=|:)\s*(\d+\.?\d*)", "vix"),
    # "SPY is 580", "SPY at $580.50", "SPY price of 580"
    (r"SPY\s+(?:is|at|price\s+(?:of|at|is))?\s*\$?(\d+\.?\d*)", "spy_price"),
    # "premium of $0.40", "premium is 0.40"
    (r"premium\s+(?:of|is|at|=|:)\s*\$?(\d+\.?\d*)", "premium"),
    # "strike at 580", "strike of 580", "580 strike", "$580 strike"
    (r"(?:strike\s+(?:at|of|is|=|:)\s*\$?(\d+\.?\d*)|\$?(\d+\.?\d*)\s+strike)", "strike"),
    # "5 contracts", "contracts: 5"
    (r"(\d+)\s+contracts?|contracts?\s*[:=]\s*(\d+)", "contracts"),
    # "DTE of 10", "10 DTE", "DTE is 10"
    (r"(?:DTE\s+(?:of|is|=|:)\s*(\d+)|(\d+)\s+DTE)", "dte"),
]


class NumericalGroundingChecker:
    """Verifies that numbers cited in reasoning match the context data.

    Extracts numerical claims via regex, builds a truth map from context,
    and compares with tolerance. Multiple mismatches or critical field
    mismatches result in a block.
    """

    def validate(
        self,
        decision,
        context,
        config: GuardrailConfig,
    ) -> list[GuardrailResult]:
        """Run numerical grounding check.

        Args:
            decision: DecisionOutput from Claude
            context: ReasoningContext with ground truth values
            config: Guardrail configuration

        Returns:
            List of GuardrailResult
        """
        if not config.numerical_grounding_enabled:
            return []

        reasoning = decision.reasoning or ""
        if not reasoning:
            return [GuardrailResult(
                passed=True,
                guard_name="numerical_grounding",
                severity="info",
                reason="No reasoning text to check",
            )]

        # Build truth map from context
        truth = self._build_truth_map(context)
        if not truth:
            return [GuardrailResult(
                passed=True,
                guard_name="numerical_grounding",
                severity="info",
                reason="No numerical context data to compare against",
            )]

        # Extract claims from reasoning
        claims = self._extract_claims(reasoning)
        if not claims:
            return [GuardrailResult(
                passed=True,
                guard_name="numerical_grounding",
                severity="info",
                reason="No numerical claims found in reasoning",
            )]

        # Compare claims against truth
        tolerance = config.numerical_tolerance_pct
        max_before_block = config.numerical_max_mismatches_before_block
        critical_fields = {"vix", "strike", "premium", "spy_price"}

        mismatches = []
        for claim_type, claim_value in claims:
            truth_values = truth.get(claim_type, [])
            if not truth_values:
                continue  # No ground truth to compare against

            # Find nearest truth value
            closest = min(truth_values, key=lambda v: abs(v - claim_value))
            is_integer_field = claim_type in ("contracts", "dte")

            if is_integer_field:
                # Integer fields: allow tolerance of 1
                if abs(claim_value - closest) > 1:
                    mismatches.append((claim_type, claim_value, closest))
            else:
                # Float fields: percentage tolerance
                if closest > 0:
                    diff_pct = abs(claim_value - closest) / closest
                    if diff_pct > tolerance:
                        mismatches.append((claim_type, claim_value, closest))
                elif claim_value != 0:
                    mismatches.append((claim_type, claim_value, closest))

        if not mismatches:
            return [GuardrailResult(
                passed=True,
                guard_name="numerical_grounding",
                severity="info",
                reason="All numerical claims match context data",
            )]

        # Determine severity
        has_critical = any(m[0] in critical_fields for m in mismatches)
        should_block = len(mismatches) >= max_before_block or has_critical

        results = []
        for claim_type, claimed, actual in mismatches:
            results.append(GuardrailResult(
                passed=False,
                guard_name="numerical_grounding",
                severity="block" if should_block else "warning",
                reason=f"Reasoning claims {claim_type}={claimed} but context has {actual}",
                details={
                    "field": claim_type,
                    "claimed": claimed,
                    "actual": actual,
                    "critical": claim_type in critical_fields,
                },
            ))

        return results

    def _build_truth_map(self, context) -> dict[str, list[float]]:
        """Build a map of field names to known values from context.

        Returns:
            Dict mapping field types to lists of known values
        """
        truth: dict[str, list[float]] = {}
        market = context.market_context or {}

        # Market data
        vix = market.get("vix")
        if vix is not None and vix != "UNKNOWN":
            try:
                truth.setdefault("vix", []).append(float(vix))
            except (TypeError, ValueError):
                pass

        spy = market.get("spy_price")
        if spy is not None and spy != "UNKNOWN":
            try:
                truth.setdefault("spy_price", []).append(float(spy))
            except (TypeError, ValueError):
                pass

        # Position data
        for pos in (context.open_positions or []):
            strike = pos.get("strike")
            if strike is not None:
                try:
                    truth.setdefault("strike", []).append(float(strike))
                except (TypeError, ValueError):
                    pass

            premium = pos.get("entry_premium")
            if premium is not None:
                try:
                    truth.setdefault("premium", []).append(float(premium))
                except (TypeError, ValueError):
                    pass

            contracts = pos.get("contracts")
            if contracts is not None:
                try:
                    truth.setdefault("contracts", []).append(float(contracts))
                except (TypeError, ValueError):
                    pass

            dte = pos.get("dte")
            if dte is not None:
                try:
                    truth.setdefault("dte", []).append(float(dte))
                except (TypeError, ValueError):
                    pass

        # Staged candidates
        for cand in (context.staged_candidates or []):
            strike = cand.get("strike")
            if strike is not None:
                try:
                    truth.setdefault("strike", []).append(float(strike))
                except (TypeError, ValueError):
                    pass

            price = cand.get("limit_price")
            if price is not None:
                try:
                    truth.setdefault("premium", []).append(float(price))
                except (TypeError, ValueError):
                    pass

            contracts = cand.get("contracts")
            if contracts is not None:
                try:
                    truth.setdefault("contracts", []).append(float(contracts))
                except (TypeError, ValueError):
                    pass

        return truth

    def _extract_claims(self, reasoning: str) -> list[tuple[str, float]]:
        """Extract numerical claims from reasoning text.

        Args:
            reasoning: Claude's reasoning text

        Returns:
            List of (claim_type, claimed_value) tuples
        """
        claims = []
        for pattern, claim_type in _PATTERNS:
            for match in re.finditer(pattern, reasoning, re.IGNORECASE):
                # Get the first non-None group
                for group in match.groups():
                    if group is not None:
                        try:
                            claims.append((claim_type, float(group)))
                        except ValueError:
                            pass
                        break  # Only take first non-None group per match

        return claims
