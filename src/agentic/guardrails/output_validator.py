"""Post-Claude output validation guardrails.

Validates that Claude's decision output is grounded in the context
it received. Three checks:
1. Action plausibility: does the action make sense given context?
2. Symbol cross-reference: are referenced symbols in the context?
3. Reasoning-action coherence: does reasoning match the action?

Zero additional Claude API calls. All pure Python logic.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from loguru import logger

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.registry import GuardrailResult

if TYPE_CHECKING:
    from src.agentic.reasoning_engine import DecisionOutput
    from src.agentic.working_memory import ReasoningContext
from src.config.exchange_profile import get_active_profile

# Common financial abbreviations that should NOT be flagged as unknown symbols
COMMON_ABBREVIATIONS = {
    "VIX", "SPY", "SPX", "DTE", "OTM", "ITM", "ATM", "IV", "HV",
    "ETF", "EPS", "PE", "RSI", "MACD", "SMA", "EMA", "ADX", "ATR",
    "FOMC", "CPI", "PPI", "GDP", "PCE", "NFP", "OPEX",
    "ROI", "NAV", "AUM", "EOD", "YTD", "MTD", "QTD",
    "BTO", "STC", "STO", "BTC",  # Options order types
    "USD", "EUR", "GBP", "JPY", "AUD",  # Currencies
    "NYSE", "NASDAQ", "CBOE", "CME", "ASX",  # Exchanges
    "IBKR", "TWS", "API",  # Platforms
    "SEC", "FINRA", "IRS", "ASIC",  # Regulators
    "JSON", "HTML", "CSV",  # Formats
    "PM", "AM", "ET", "EST", "EDT", "UTC", "AEDT", "AEST",  # Time / timezones
    "QQQ", "IWM", "XSP", "XLE", "XLF", "XLK", "XLV",  # Common ETFs (US)
    "XJO", "NDQ",  # Common ASX ETFs / indices
    "TAAD",  # Our system
    "MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN",  # Days
    "JAN", "FEB", "MAR", "APR", "MAY", "JUN",
    "JUL", "AUG", "SEP", "OCT", "NOV", "DEC",  # Months
}

# Regex to extract potential ticker symbols: 1-5 uppercase letters bounded by word boundaries
_TICKER_RE = re.compile(r"\b([A-Z]{1,5})\b")

# Phrases in reasoning that suggest monitoring/inaction
_MONITOR_PHRASES = re.compile(
    r"\b(no action|wait|monitoring|hold|observe|stand down|do nothing|no trade|skip)\b",
    re.IGNORECASE,
)

# Phrases in reasoning that suggest execution/action
_EXECUTE_PHRASES = re.compile(
    r"\b(execute|buy|sell|place order|open position|enter trade|submit)\b",
    re.IGNORECASE,
)


class OutputValidator:
    """Validates Claude's decision output against the context it received.

    Checks action plausibility, symbol cross-references, and
    reasoning-action coherence. All checks are pure Python.
    """

    def validate(
        self,
        decision: DecisionOutput,
        context: ReasoningContext,
        config: GuardrailConfig,
    ) -> list[GuardrailResult]:
        """Run all output validation checks.

        Args:
            decision: DecisionOutput from Claude
            context: ReasoningContext sent to Claude
            config: Guardrail configuration

        Returns:
            List of GuardrailResult
        """
        results = []

        if config.action_plausibility_enabled:
            results.append(self.check_action_plausibility(decision, context))

        if config.symbol_crossref_enabled:
            results.extend(self.check_symbol_crossref(decision, context))

        if config.reasoning_coherence_enabled:
            results.extend(self.check_reasoning_coherence(decision))

        return results

    def check_action_plausibility(self, decision, context) -> GuardrailResult:
        """Check that the action makes sense given context data.

        - EXECUTE_TRADES requires non-empty staged_candidates
        - CLOSE_POSITION requires position_id matching an open position
        - RUN_EXPERIMENT requires experiment parameters in metadata

        Args:
            decision: DecisionOutput from Claude
            context: ReasoningContext

        Returns:
            GuardrailResult
        """
        action = decision.action
        metadata = decision.metadata or {}

        # EXECUTE_TRADES requires staged candidates
        if action == "EXECUTE_TRADES":
            if not context.staged_candidates:
                return GuardrailResult(
                    passed=False,
                    guard_name="action_plausibility",
                    severity="block",
                    reason="EXECUTE_TRADES requested but no staged candidates in context",
                    details={"action": action, "staged_count": 0},
                )

        # CLOSE_POSITION requires a valid position identifier
        if action == "CLOSE_POSITION":
            position_id = (
                metadata.get("position_id")
                or metadata.get("trade_id")
            )
            if not position_id:
                return GuardrailResult(
                    passed=False,
                    guard_name="action_plausibility",
                    severity="block",
                    reason="CLOSE_POSITION requested but no position_id or trade_id in metadata",
                    details={"action": action, "metadata": metadata},
                )
            # Check that position_id matches an open position in context
            open_trade_ids = {
                str(p.get("trade_id", "")) for p in context.open_positions
            }
            open_symbols = {p.get("symbol", "") for p in context.open_positions}
            # position_id could be a trade_id or a symbol
            if (
                str(position_id) not in open_trade_ids
                and str(position_id) not in open_symbols
            ):
                if not context.open_positions:
                    return GuardrailResult(
                        passed=False,
                        guard_name="action_plausibility",
                        severity="block",
                        reason="CLOSE_POSITION requested but no open positions in context",
                        details={"action": action, "position_id": position_id},
                    )
                return GuardrailResult(
                    passed=False,
                    guard_name="action_plausibility",
                    severity="warning",
                    reason=f"CLOSE_POSITION: '{position_id}' not found in open positions (may use a different ID format)",
                    details={
                        "action": action,
                        "position_id": position_id,
                        "open_trade_ids": sorted(open_trade_ids),
                    },
                )

        # CLOSE_ALL_POSITIONS requires a reason in metadata
        if action == "CLOSE_ALL_POSITIONS":
            if not metadata.get("reason"):
                return GuardrailResult(
                    passed=False,
                    guard_name="action_plausibility",
                    severity="block",
                    reason="CLOSE_ALL_POSITIONS requested but no reason in metadata",
                    details={"action": action, "metadata": metadata},
                )
            if not context.open_positions:
                return GuardrailResult(
                    passed=True,
                    guard_name="action_plausibility",
                    severity="warning",
                    reason="CLOSE_ALL_POSITIONS requested but no open positions in context",
                    details={"action": action, "open_positions": 0},
                )

        # MONITOR_ONLY on entry day with VIX<40 — may miss opportunity
        if action == "MONITOR_ONLY":
            try:
                profile = get_active_profile()
                now_market = datetime.now(profile.timezone)
                day_of_week = now_market.weekday()  # 0=Monday, 1=Tuesday
                vix = (context.market_context or {}).get("vix")
                if day_of_week in (0, 1) and vix is not None and vix < 40:
                    return GuardrailResult(
                        passed=True,
                        guard_name="action_plausibility",
                        severity="warning",
                        reason=(
                            f"MONITOR_ONLY on entry day with VIX {vix} (<40) "
                            f"— may miss opportunity"
                        ),
                        details={"action": action, "day_of_week": day_of_week, "vix": vix},
                    )
            except Exception:
                pass  # Non-critical warning — don't block on import/timezone errors

        # RUN_EXPERIMENT requires experiment parameters
        if action == "RUN_EXPERIMENT":
            experiment = metadata.get("experiment", {})
            if not experiment or not experiment.get("parameter"):
                return GuardrailResult(
                    passed=False,
                    guard_name="action_plausibility",
                    severity="block",
                    reason="RUN_EXPERIMENT requested but no experiment parameters in metadata",
                    details={"action": action, "metadata": metadata},
                )

        return GuardrailResult(
            passed=True,
            guard_name="action_plausibility",
            severity="info",
            reason="Action is plausible given context",
        )

    def check_symbol_crossref(self, decision, context) -> list[GuardrailResult]:
        """Check that symbols mentioned in reasoning exist in context.

        Extracts ticker-like patterns from reasoning + key_factors,
        filters against common abbreviations, and flags unknown symbols.

        Args:
            decision: DecisionOutput from Claude
            context: ReasoningContext

        Returns:
            List of GuardrailResult (one per unknown symbol, or one pass)
        """
        # Build set of known symbols from context
        known_symbols = set()
        for pos in context.open_positions:
            sym = pos.get("symbol", "")
            if sym:
                known_symbols.add(sym.upper())
        for cand in context.staged_candidates:
            sym = cand.get("symbol", "")
            if sym:
                known_symbols.add(sym.upper())
        for trade in context.recent_trades:
            sym = trade.get("symbol", "")
            if sym:
                known_symbols.add(sym.upper())

        # Extract symbols from reasoning text + key_factors
        text = (decision.reasoning or "") + " " + " ".join(decision.key_factors or [])
        found_symbols = set(_TICKER_RE.findall(text))

        # Filter out common abbreviations, short words, and domain-specific tokens.
        # Single letters appear in option notation (150P = PUT, 200C = CALL) and
        # autonomy levels (L1, L2). Common English words appear when Claude
        # uses natural language in uppercase context (e.g. "STEP 1 - POSITION CHECK").
        short_words = {
            # Single letters (option notation: P=PUT, C=CALL; autonomy: L=Level)
            "A", "B", "C", "D", "E", "F", "G", "H", "I", "J", "K", "L", "M",
            "N", "O", "P", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z",
            # 2-letter common words
            "AN", "AM", "AS", "AT", "BE", "BY", "DO", "GO",
            "IF", "IN", "IS", "IT", "MY", "NO", "OF", "OK", "ON", "OR",
            "SO", "TO", "UP", "US", "WE", "HE",
            # 3-letter English words that appear in reasoning
            "NOT", "AND", "BUT", "FOR", "THE", "HAS", "HAD", "ARE", "WAS",
            "ALL", "ANY", "CAN", "MAY", "NEW", "NOW", "OLD", "OUR", "OUT",
            "OWN", "SAY", "TOO", "TWO", "WAY", "WHO", "DAY", "GET", "HIS",
            "HOW", "ITS", "LET", "PUT", "SET", "TRY", "USE", "YET",
            "LOW", "HIGH", "MAX", "MIN", "NET", "PER", "PRE", "RUN",
            # 4-letter English words (appear in Claude's structured reasoning headers)
            "ALSO", "BACK", "BEEN", "BEST", "BOTH", "CALL", "CASE", "COME",
            "COST", "DATA", "DATE", "DAYS", "DOES", "DONE", "DOWN", "EACH",
            "EVEN", "EXIT", "FIND", "FIVE", "FROM", "FULL", "GIVE", "GOOD",
            "HALF", "HAVE", "HELD", "HERE", "HOLD", "INTO", "JUST", "KEEP",
            "LAST", "LESS", "LIKE", "LONG", "LOSS", "MADE", "MAKE", "MORE",
            "MOST", "MUCH", "MUST", "NEAR", "NEED", "NEXT", "NINE", "NONE",
            "ONCE", "ONLY", "OPEN", "OVER", "PAST", "PICK", "PLAN", "POST",
            "RISK", "SEEN", "SHOW", "SIDE", "SIGN", "SOME", "STEP", "STOP",
            "SURE", "TAKE", "THAN", "THAT", "THEM", "THEN", "THEY", "THIS",
            "TIME", "TOOK", "VERY", "WAIT", "WELL", "WENT", "WERE", "WHAT",
            "WHEN", "WILL", "WITH", "WORK", "YEAR", "ZERO",
            # 5-letter English words (common in trading reasoning)
            "ABOVE", "AFTER", "BEING", "BELOW", "CHECK", "CLEAR", "CLOSE",
            "COULD", "ENTRY", "EVERY", "FIRST", "GIVEN", "GOING", "GREAT",
            "LARGE", "LEVEL", "LIMIT", "LOSES", "LOWER", "MIGHT", "MONEY",
            "NEVER", "OTHER", "POINT", "PRICE", "PRIME", "PRIOR", "QUICK",
            "RANGE", "SINCE", "SMALL", "STILL", "STOCK", "THERE", "THESE",
            "THOSE", "THREE", "TODAY", "TOTAL", "TRADE", "TREND", "UNDER",
            "UNTIL", "UPPER", "VALUE", "WATCH", "WHERE", "WHICH", "WHILE",
            "WHOLE", "WHOSE", "WOULD", "WORST",
            # Plurals of trading terms that appear in options reasoning
            "PUTS", "CALLS", "SELLS", "BUYS",
        }
        unknown_symbols = found_symbols - known_symbols - COMMON_ABBREVIATIONS - short_words

        if not unknown_symbols:
            return [GuardrailResult(
                passed=True,
                guard_name="symbol_crossref",
                severity="info",
                reason="All referenced symbols found in context",
            )]

        results = []
        # Check if unknown symbols appear in action-critical context
        # CLOSE_POSITION gets "warning" not "block" because action_plausibility
        # already validates the trade_id matches an actual open position.
        action = decision.action
        action_block = action in ("EXECUTE_TRADES", "STAGE_CANDIDATES")

        for sym in unknown_symbols:
            severity = "block" if action_block else "warning"
            results.append(GuardrailResult(
                passed=False,
                guard_name="symbol_crossref",
                severity=severity,
                reason=f"Symbol '{sym}' in reasoning not found in context data",
                details={
                    "unknown_symbol": sym,
                    "known_symbols": sorted(known_symbols),
                    "action": action,
                },
            ))

        return results

    def check_reasoning_coherence(self, decision) -> list[GuardrailResult]:
        """Check that reasoning text is consistent with the chosen action.

        - If reasoning says "no action"/"wait" but action is EXECUTE_TRADES -> flag
        - If action is MONITOR_ONLY but reasoning says "execute"/"buy" -> warning
        - If confidence > 0.85 but key_factors < 3 items -> warning
        - If confidence > 0.85 but reasoning < 100 chars -> warning

        Args:
            decision: DecisionOutput from Claude

        Returns:
            List of GuardrailResult
        """
        results = []
        reasoning = decision.reasoning or ""
        action = decision.action
        confidence = decision.confidence or 0.0
        key_factors = decision.key_factors or []

        # Check for contradictory reasoning vs action
        has_monitor_language = bool(_MONITOR_PHRASES.search(reasoning))
        has_execute_language = bool(_EXECUTE_PHRASES.search(reasoning))

        if has_monitor_language and action == "EXECUTE_TRADES":
            results.append(GuardrailResult(
                passed=False,
                guard_name="reasoning_coherence",
                severity="warning",
                reason="Reasoning suggests monitoring/waiting but action is EXECUTE_TRADES",
                details={"action": action, "reasoning_excerpt": reasoning[:200]},
            ))

        if has_execute_language and action == "MONITOR_ONLY":
            results.append(GuardrailResult(
                passed=False,
                guard_name="reasoning_coherence",
                severity="warning",
                reason="Reasoning suggests execution but action is MONITOR_ONLY",
                details={"action": action, "reasoning_excerpt": reasoning[:200]},
            ))

        # High confidence with insufficient support
        if confidence > 0.90:
            if len(key_factors) < 3:
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="reasoning_coherence",
                    severity="warning",
                    reason=f"High confidence ({confidence:.2f}) with only {len(key_factors)} key factors (expected >= 3)",
                    details={"confidence": confidence, "key_factors_count": len(key_factors)},
                ))

            if len(reasoning) < 100:
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="reasoning_coherence",
                    severity="warning",
                    reason=f"High confidence ({confidence:.2f}) with short reasoning ({len(reasoning)} chars, expected >= 100)",
                    details={"confidence": confidence, "reasoning_length": len(reasoning)},
                ))

        if not results:
            results.append(GuardrailResult(
                passed=True,
                guard_name="reasoning_coherence",
                severity="info",
                reason="Reasoning is coherent with action",
            ))

        return results
