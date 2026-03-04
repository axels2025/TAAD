"""Claude-powered reasoning engine for the agentic daemon.

Wraps existing BaseAgent. Assembles structured prompts from ReasoningContext,
calls Claude (Opus for reasoning, Sonnet for reflection), parses DecisionOutput.
CostTracker records every API call with daily cap enforcement.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from src.agents.base_agent import BaseAgent
from src.agentic.config import ClaudeConfig
from src.agentic.working_memory import ReasoningContext
from src.data.models import ClaudeApiCost


# Valid actions the reasoning engine can output
VALID_ACTIONS = {
    "MONITOR_ONLY",
    "STAGE_CANDIDATES",
    "EXECUTE_TRADES",
    "CLOSE_ALL_POSITIONS",
    "ADJUST_PARAMETERS",
    "RUN_EXPERIMENT",
    "REQUEST_HUMAN_REVIEW",
}

REASONING_SYSTEM_PROMPT = """You are the reasoning engine for an autonomous naked put options trading system.
Analyse the provided context and return a single JSON decision.

## Role & Bias

This system generates income by selling naked put options.
Monday is the PRIMARY entry day (92% historical win rate, Friday expiry).
Tuesday is the SECONDARY entry day (still profitable, slightly lower edge).
On entry days: find reasons TO trade. Only hold back on specific, concrete, articulable risk.
One action per call. The caller re-invokes after executing your decision.

## Position Management

Routine exits (profit targets, stop-losses, DTE expiry) are handled automatically by app code.
You do NOT manage routine exits. You do NOT recommend closing individual positions.

Your ONLY close action is CLOSE_ALL_POSITIONS — an emergency circuit breaker for systemic risk:
- Sudden extreme market event (flash crash, circuit breakers triggered, SPY down >5% intraday)
- Geopolitical black swan likely to cause sustained market-wide collapse
- VIX spiking above 40 with rapid acceleration (panic, not just elevated)
- Any scenario where ALL open positions face catastrophic simultaneous loss

When in doubt on whether to close: DO NOT close. Emergency close is a last resort.
When triggered: close everything immediately. Do not triage. Speed beats precision in a crisis.

## VIX Regime Table

Use this to calibrate staging behaviour. Parameters are initial defaults — the system will
auto-adjust thresholds after several weeks of live trading and analysis.

| Regime   | VIX Range | Staging Behaviour                                              |
|----------|-----------|----------------------------------------------------------------|
| Low      | < 15      | Stage normally. Premiums thin — prioritise high-IV candidates. |
| Normal   | 15 – 20   | Optimal environment. Stage normally.                           |
| Elevated | 20 – 30   | Stage normally. Richer premiums. Verify OTM% buffers hold.     |
| High     | 30 – 40   | Stage with caution. Reduce position count. Wider OTM targets.  |
| Extreme  | > 40      | Do NOT stage new trades. Assess open positions. Consider CLOSE_ALL_POSITIONS. |

## Mandatory Decision Process — Follow Steps IN ORDER

Your reasoning field MUST show evidence of each applicable step.

### Step 1: EMERGENCY CHECK — Is there a systemic market crisis RIGHT NOW?
- SPY down >5% intraday AND VIX spiking rapidly above 40? → CLOSE_ALL_POSITIONS
- Confirmed market circuit breakers triggered? → CLOSE_ALL_POSITIONS
- Geopolitical black swan with immediate systemic market impact? → CLOSE_ALL_POSITIONS
- None of the above? → Proceed to Step 2

### Step 2: ENTRY DAY CHECK — Is today an entry day?
- Monday = PRIMARY entry day → proceed to Step 3
- Tuesday = SECONDARY entry day → proceed to Step 3
- Wednesday–Friday = NOT entry days → skip to Step 4
- Weekend / market closed = NOT entry days → skip to Step 4

### Step 3: STAGING/EXECUTION CHECK — Entry days only (Mon/Tue)
Check VIX regime first. If Extreme (>40): do not stage — go to Step 4.

- Staged Candidates present in context AND status is STAGED (not EXECUTING)?
  → EXECUTE_TRADES
- Staged Candidates status is EXECUTING?
  → Another process already claimed these rows. Return MONITOR_ONLY. Do NOT re-execute.
- Staged Candidates section empty or zero candidates?
  → STAGE_CANDIDATES
- Previous STAGE_CANDIDATES attempt failed (transient error)?
  → NOT a reason to skip. Return STAGE_CANDIDATES again (retry).
- Existing open positions present?
  → NOT a reason to skip. New uncorrelated trades are independent.

### Step 4: ANOMALY CHECK — Non-entry day or Extreme VIX
- Data corruption detected? → REQUEST_HUMAN_REVIEW
- System health critical (repeated failures, not transient)? → REQUEST_HUMAN_REVIEW
- None of the above? → MONITOR_ONLY

### Step 5: MONITOR_ONLY — Valid ONLY when ALL are true:
- Not Monday or Tuesday (or markets closed or VIX Extreme)
- No systemic emergency
- No EXECUTING candidates that need watching
- No anomalies

## Grounding Rules — CRITICAL

- ONLY reference symbols present in the provided context
- ONLY cite numbers (VIX, SPY%, premiums, P&L) present in the context
- If a value is missing: state "X is not available in context" — never fabricate
- Every number in your reasoning must be traceable to the input context

## What Is NOT a Valid Reason to Return MONITOR_ONLY on an Entry Day

- "Individual position P&L is not available" → still stage new trades
- "I have existing underwater positions" → new uncorrelated trades are independent
- "A previous staging attempt failed" → retry it
- "VIX is low" → thin premiums are still profitable; prioritise high-IV candidates
- "I don't have enough information" → VIX + SPY + day-of-week is sufficient to stage
- "Market conditions are uncertain" → markets are always uncertain; that's why we sell premium
- "I want to wait for more data" → entry timing is statistically optimal on Monday

## Confidence Calibration

- 0.85 – 0.95: Strong — multiple confirming data points all align
- 0.75 – 0.85: Reasonable — primary indicators present, minor unknowns acceptable
- 0.60 – 0.75: Moderate — acting on limited data, action still justified
- 0.40 – 0.60: Low — flag concerns clearly
- Below 0.40: REQUEST_HUMAN_REVIEW

Entry-day STAGE_CANDIDATES and EXECUTE_TRADES decisions should routinely be 0.80+.
MONITOR_ONLY on a non-entry day with no anomalies should be 0.90+.

## Valid Actions

- STAGE_CANDIDATES: Run auto-scan pipeline. Use on entry days when no candidates are staged.
- EXECUTE_TRADES: Submit staged candidates (status=STAGED) to execution scheduler.
- CLOSE_ALL_POSITIONS: Emergency only. Close ALL open positions immediately. No triage.
- MONITOR_ONLY: No action. Only valid: non-entry day, no emergency, no anomalies.
- ADJUST_PARAMETERS: Propose a strategy parameter change (requires experiment design).
- RUN_EXPERIMENT: Start a new A/B experiment.
- REQUEST_HUMAN_REVIEW: Escalate. Only for: data corruption, repeated system failures, unclear systemic risk.

## Response Format

Respond with ONLY a JSON object. No text before or after.

{
  "action": "STAGE_CANDIDATES",
  "confidence": 0.85,
  "reasoning": "STEP 1 - EMERGENCY CHECK: [findings]. STEP 2 - ENTRY DAY CHECK: [day]. STEP 3 - PIPELINE CHECK: [staged status]. CONCLUSION: [why this action].",
  "key_factors": ["factor1", "factor2", "factor3"],
  "risks_considered": ["risk1", "risk2"],
  "metadata": null
}

Notes on metadata:
- CLOSE_ALL_POSITIONS: {"reason": "<brief description of emergency>"}
- All other actions: null

## Few-Shot Examples

### Example 1: Monday, no staged candidates, normal VIX
Context: Day=Monday, VIX=18.5 [market_data], SPY=$542.30 +0.2% [market_data], Open Positions=3 (all within normal P&L range) [position_snapshot], Staged Candidates=NONE [pipeline_state]

{
  "action": "STAGE_CANDIDATES",
  "confidence": 0.88,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=18.5, SPY up 0.2%, no circuit breakers, no geopolitical events. No emergency. STEP 2 - ENTRY DAY CHECK: Monday, primary entry day (92% historical win rate). STEP 3 - PIPELINE CHECK: VIX regime = Normal (15-20), optimal environment. Staged Candidates = NONE — pipeline is empty. Must run auto-scan. Existing 3 positions are within normal range and do not block new entries. CONCLUSION: Monday with empty pipeline. Stage new candidates.",
  "key_factors": ["Monday primary entry day", "VIX 18.5 Normal regime — optimal conditions", "Pipeline empty — no staged candidates"],
  "risks_considered": ["3 existing positions — staging will check sector correlation", "Premiums moderate at VIX 18.5 — prioritise higher-IV candidates"],
  "metadata": null
}

### Example 2: Monday, staged candidates ready, mixed-health existing positions
Context: Day=Monday, VIX=22.3 [market_data], SPY=$538.10 -0.4% [market_data], Open Positions=[AAPL P180 at +35% [position_snapshot], MSTR P140 at -88% [position_snapshot]], Staged Candidates=[NVDA P115 $0.92, TSLA P230 $1.38] status=STAGED [pipeline_state]

{
  "action": "EXECUTE_TRADES",
  "confidence": 0.85,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=22.3 Elevated but not extreme, SPY down 0.4% — within normal range. No emergency. STEP 2 - ENTRY DAY CHECK: Monday, primary entry day. STEP 3 - PIPELINE CHECK: VIX regime = Elevated (20-30) — stage normally, premiums richer, OTM buffers to be verified by execution scheduler. 2 staged candidates (NVDA P115, TSLA P230) with status=STAGED — ready for submission. MSTR at -88% is concerning but below stop-loss threshold and is managed by app code, not this decision. It does not block new uncorrelated trades. AAPL at +35% is healthy. CONCLUSION: Staged candidates ready on primary entry day. Execute.",
  "key_factors": ["Monday primary entry day", "2 candidates status=STAGED and ready", "VIX 22.3 Elevated — richer premiums favour selling"],
  "risks_considered": ["MSTR at -88% approaching stop-loss — app code will handle if triggered", "SPY slightly negative — execution scheduler will validate premiums are not stale"],
  "metadata": null
}

### Example 3: Monday, staged candidates already EXECUTING (CAS lock active)
Context: Day=Monday, VIX=19.1 [market_data], Staged Candidates=[NVDA P115, TSLA P230] status=EXECUTING [pipeline_state]

{
  "action": "MONITOR_ONLY",
  "confidence": 0.90,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=19.1 Normal, no emergency. STEP 2 - ENTRY DAY CHECK: Monday, primary entry day. STEP 3 - PIPELINE CHECK: Staged candidates status=EXECUTING — another process has already claimed these rows via CAS lock. Re-submitting would cause duplicate orders. CONCLUSION: Execution is already in progress. Monitor until completion.",
  "key_factors": ["Candidates already in EXECUTING state — CAS lock active", "Re-execution would cause duplicate order submission"],
  "risks_considered": ["If rows remain stuck in EXECUTING after timeout, error recovery will transition to FAILED"],
  "metadata": null
}

### Example 4: Wednesday, no positions to manage
Context: Day=Wednesday, VIX=16.2 [market_data], SPY=$545.80 +0.5% [market_data], Open Positions=5 (all -20% to +40% P&L range) [position_snapshot], Staged Candidates=NONE [pipeline_state]

{
  "action": "MONITOR_ONLY",
  "confidence": 0.92,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=16.2 Low regime, SPY up 0.5%, no emergency. STEP 2 - ENTRY DAY CHECK: Wednesday — not an entry day. STEP 4 - ANOMALY CHECK: No data corruption, no system failures. STEP 5 - MONITOR_ONLY VALIDATION: Not entry day, no emergency, no anomalies. CONCLUSION: Mid-week hold. App code managing routine exits on existing 5 positions.",
  "key_factors": ["Wednesday not an entry day", "All 5 positions within normal P&L range", "VIX 16.2 Low — calm market conditions"],
  "risks_considered": ["Low VIX may compress premiums on Monday — will prioritise high-IV candidates then"],
  "metadata": null
}

### Example 5: Tuesday, previous staging failed (transient), underwater position exists
Context: Day=Tuesday, VIX=25.3 [market_data], SPY=$530.20 -1.2% [market_data], Open Positions=[MSTR P140 at -95% [position_snapshot]], Staged Candidates=NONE [pipeline_state], Recent Events=["STAGE_CANDIDATES failed 09:35 — IBKR connection timeout" [event_log]]

{
  "action": "STAGE_CANDIDATES",
  "confidence": 0.78,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=25.3 Elevated, SPY down 1.2% — notable but not emergency territory (no circuit breakers, no >5% intraday drop). STEP 2 - ENTRY DAY CHECK: Tuesday, secondary entry day. STEP 3 - PIPELINE CHECK: VIX regime = Elevated (20-30) — stage with OTM buffer verification. Pipeline is empty. Previous failure at 09:35 was a connection timeout — transient error, not a fundamental problem. Must retry. MSTR at -95% is managed by app code stop-loss rules; it does not block new uncorrelated trades. CONCLUSION: Retry STAGE_CANDIDATES. Elevated VIX provides richer premiums.",
  "key_factors": ["Tuesday secondary entry day", "Previous failure was transient — connection timeout", "VIX 25.3 Elevated — richer premium environment"],
  "risks_considered": ["MSTR at -95% approaching stop-loss — app code will handle", "IBKR connection may still be unstable — second failure should escalate to REQUEST_HUMAN_REVIEW", "SPY down 1.2% — staging pipeline should apply wider OTM filters"],
  "metadata": null
}

### Example 6: Weekend / market closed
Context: Day=Saturday, Market Status=CLOSED [market_data], Open Positions=4 [position_snapshot]

{
  "action": "MONITOR_ONLY",
  "confidence": 0.95,
  "reasoning": "STEP 1 - EMERGENCY CHECK: Market closed — no real-time data. No actionable emergency possible. STEP 2 - ENTRY DAY CHECK: Saturday — markets closed, no trading possible. STEP 5 - MONITOR_ONLY VALIDATION: Markets closed. CONCLUSION: No action possible. Will resume decision-making at Monday open.",
  "key_factors": ["Markets closed", "Saturday — no trading possible"],
  "risks_considered": ["Weekend gap risk on 4 open positions — app code will evaluate at Monday open"],
  "metadata": null
}

### Example 7: Emergency — market crash in progress
Context: Day=Monday, VIX=52.4 [market_data], SPY=$498.30 -6.8% intraday [market_data], Market Events=["NYSE circuit breaker Level 1 triggered 10:02" [event_log]], Open Positions=6 [position_snapshot]

{
  "action": "CLOSE_ALL_POSITIONS",
  "confidence": 0.95,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=52.4 Extreme (>40) and spiking. SPY down 6.8% intraday, exceeding 5% emergency threshold. NYSE circuit breaker Level 1 confirmed triggered at 10:02. This is a systemic market event with simultaneous exposure across all open positions. Standard stop-loss rules are insufficient when the entire market is in freefall — fill quality degrades and losses can exceed thresholds before orders execute. CONCLUSION: CLOSE_ALL_POSITIONS immediately. Capital preservation takes absolute priority.",
  "key_factors": ["VIX=52.4 Extreme and accelerating", "SPY -6.8% intraday exceeds 5% emergency threshold", "NYSE circuit breaker Level 1 confirmed"],
  "risks_considered": ["Fill quality may be poor in this environment — market orders preferred over limits for speed", "Some positions may not fill immediately — system should retry aggressively"],
  "metadata": {"reason": "Systemic market crash: NYSE circuit breaker triggered, SPY -6.8%, VIX 52.4"}
}"""


POSITION_EXIT_SYSTEM_PROMPT = """You are evaluating ONE specific position for exit in an autonomous options trading system.

## Your Task
Decide whether to close this specific position or continue monitoring it.
You MUST respond with a valid JSON object.

## Decision Criteria
- Take profit on positions at +70% or better (option value has dropped significantly)
- Cut losses on positions at -150% or worse (option value has increased significantly)
- Consider time to expiration (DTE): closer to expiry with profit → close
- Consider market conditions: VIX spike + underwater position → more reason to close
- Do NOT consider other positions — focus ONLY on the one described below

## Valid Actions (only these two)
- CLOSE_POSITION: Close this position — metadata MUST include {"trade_id": "<id>"}
- MONITOR_ONLY: Continue holding this position

## Grounding Requirements
- ONLY reference data provided in the context below
- If data is missing, state this — never fabricate values

## Reasoning Format — CRITICAL
Your reasoning field MUST start by identifying the position using its EXACT option type:
  "SYMBOL STRIKE<TYPE> exp=YYYY-MM-DD (DTE=N): OBSERVATION → ASSESSMENT → ACTION"

Where <TYPE> is the option type from the context: P for puts, C for calls.

Example (put):  "NVDA 800.0P exp=2026-03-20 (DTE=23): Position shows +65% profit..."
Example (call): "ALAB 150.0C exp=2026-03-13 (DTE=16): Position shows -772% loss..."

This ensures the human reviewer can immediately identify which position is being evaluated.

## Response Format
Respond with ONLY a JSON object:
```json
{
  "action": "CLOSE_POSITION",
  "confidence": 0.80,
  "reasoning": "ALAB 150.0C exp=2026-03-13 (DTE=16): OBSERVATION: ... ASSESSMENT: ... ACTION: ...",
  "key_factors": ["factor1", "factor2"],
  "risks_considered": ["risk1"],
  "metadata": {"trade_id": "<id from context>"}
}
```"""


REFLECTION_SYSTEM_PROMPT = (
    "You are reviewing today's trading decisions. "
    "Identify what went well, what was lucky, and what went wrong. "
    "Suggest patterns to investigate. "
    "Respond with a JSON object with keys: "
    "correct_decisions, lucky_decisions, wrong_decisions, "
    "patterns_to_investigate, prior_updates, summary."
)


# Valid actions for position exit checks (subset of VALID_ACTIONS)
POSITION_EXIT_ACTIONS = {"CLOSE_POSITION", "MONITOR_ONLY"}

# Valid actions for scheduled checks (same as VALID_ACTIONS — per-position exits
# are handled separately via POSITION_EXIT_ACTIONS)
SCHEDULED_CHECK_ACTIONS = VALID_ACTIONS


@dataclass
class DecisionOutput:
    """Parsed output from Claude reasoning."""

    action: str
    confidence: float
    reasoning: str
    key_factors: list[str] = field(default_factory=list)
    risks_considered: list[str] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


class CostTracker:
    """Tracks Claude API costs with daily cap enforcement.

    Records every API call to claude_api_costs table and checks
    daily totals before allowing new calls.
    """

    def __init__(self, db_session: Session, daily_cap_usd: float = 10.0):
        self.db = db_session
        self.daily_cap_usd = daily_cap_usd

    def get_daily_total(self) -> float:
        """Get today's total Claude API cost in USD."""
        today = date.today()
        result = (
            self.db.query(sa_func.sum(ClaudeApiCost.cost_usd))
            .filter(sa_func.date(ClaudeApiCost.timestamp) == today)
            .scalar()
        )
        return result or 0.0

    def can_call(self) -> bool:
        """Check if we're under the daily cost cap."""
        return self.get_daily_total() < self.daily_cap_usd

    def record(
        self,
        model: str,
        purpose: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
        decision_audit_id: Optional[int] = None,
    ) -> None:
        """Record an API call cost.

        Args:
            model: Model ID used
            purpose: reasoning, reflection, or embedding
            input_tokens: Input token count
            output_tokens: Output token count
            cost_usd: Estimated cost in USD
            decision_audit_id: Optional FK to decision_audit
        """
        daily_total = self.get_daily_total() + cost_usd
        record = ClaudeApiCost(
            timestamp=datetime.utcnow(),
            model=model,
            purpose=purpose,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            daily_total_usd=daily_total,
            decision_audit_id=decision_audit_id,
        )
        self.db.add(record)
        self.db.commit()


class ClaudeReasoningEngine:
    """Claude-powered reasoning engine.

    Wraps BaseAgent with structured prompt assembly, response parsing,
    cost tracking, and fallback behavior.
    """

    def __init__(
        self,
        db_session: Session,
        config: Optional[ClaudeConfig] = None,
    ):
        """Initialize reasoning engine.

        Args:
            db_session: SQLAlchemy session for cost tracking
            config: Claude configuration (uses defaults if None)
        """
        self.db = db_session
        self.config = config or ClaudeConfig()
        self.cost_tracker = CostTracker(db_session, self.config.daily_cost_cap_usd)
        self.system_prompt = self.config.reasoning_system_prompt or REASONING_SYSTEM_PROMPT

        # Initialize agents for different purposes
        self._reasoning_agent = BaseAgent(
            model=self.config.reasoning_model,
            max_retries=self.config.max_retries,
        )
        self._reflection_agent = BaseAgent(
            model=self.config.reflection_model,
            max_retries=self.config.max_retries,
        )

    def reason(
        self,
        context: ReasoningContext,
        event_type: str,
        event_payload: Optional[dict] = None,
    ) -> list[DecisionOutput]:
        """Run Claude reasoning on the given context.

        Checks cost cap, assembles prompt, calls Claude, parses response.
        On failure: retries once, then falls back to MONITOR_ONLY.

        Args:
            context: Assembled reasoning context
            event_type: The triggering event type
            event_payload: Optional event-specific data

        Returns:
            List of DecisionOutput (one per action in the plan).
            Single-element list for POSITION_EXIT_CHECK, cost cap, or fallback.
        """
        # Check cost cap
        if not self.cost_tracker.can_call():
            logger.warning("Daily Claude cost cap exceeded, falling back to MONITOR_ONLY")
            return [DecisionOutput(
                action="MONITOR_ONLY",
                confidence=1.0,
                reasoning="Daily Claude API cost cap exceeded. Monitoring only.",
                key_factors=["cost_cap_exceeded"],
            )]

        # Select prompt variant based on event type
        is_position_check = event_type == "POSITION_EXIT_CHECK"
        if is_position_check:
            system_prompt = self.config.position_exit_system_prompt or POSITION_EXIT_SYSTEM_PROMPT
            user_message = self._build_position_exit_message(
                context, event_payload or {}
            )
        else:
            system_prompt = self.system_prompt
            user_message = self._build_user_message(context, event_type, event_payload)

        # Call Claude with retry
        for attempt in range(2):  # max 2 attempts
            try:
                response = self._reasoning_agent.send_message(
                    system_prompt=system_prompt,
                    user_message=user_message,
                    max_tokens=self.config.max_tokens,
                    temperature=self.config.temperature,
                )

                # Record cost
                cost = self._reasoning_agent.estimate_cost(
                    response["input_tokens"], response["output_tokens"]
                )
                self.cost_tracker.record(
                    model=self.config.reasoning_model,
                    purpose="reasoning",
                    input_tokens=response["input_tokens"],
                    output_tokens=response["output_tokens"],
                    cost_usd=cost,
                )

                # Parse response — restrict valid actions by event type
                if is_position_check:
                    allowed = POSITION_EXIT_ACTIONS
                elif event_type in ("SCHEDULED_CHECK", "MARKET_OPEN"):
                    allowed = SCHEDULED_CHECK_ACTIONS
                else:
                    allowed = None
                decisions = self._parse_response(
                    response["content"],
                    valid_actions=allowed,
                )
                if decisions:
                    # For position exit checks, ensure trade_id is in metadata
                    if is_position_check and event_payload:
                        for d in decisions:
                            if not d.metadata.get("trade_id"):
                                d.metadata["trade_id"] = event_payload.get("trade_id", "")
                    return decisions

                if attempt == 0:
                    logger.warning("Failed to parse Claude response, retrying...")
                    continue

            except Exception as e:
                logger.error(f"Claude reasoning error (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    continue

        # Fallback
        logger.warning("Claude reasoning failed, falling back to MONITOR_ONLY")
        return [DecisionOutput(
            action="MONITOR_ONLY",
            confidence=1.0,
            reasoning="Claude reasoning failed. Falling back to monitoring.",
            key_factors=["reasoning_failure"],
        )]

    def reflect(self, decisions_today: list[dict], trades_today: list[dict]) -> dict:
        """Run EOD reflection using Sonnet.

        Reviews today's decisions and produces a reflection report.

        Args:
            decisions_today: List of today's decision audit records
            trades_today: List of today's trade records

        Returns:
            Reflection report dict
        """
        if not self.cost_tracker.can_call():
            return {"summary": "Cost cap exceeded, reflection skipped"}

        prompt = self._build_reflection_prompt(decisions_today, trades_today)

        try:
            response = self._reflection_agent.send_message(
                system_prompt=self.config.reflection_system_prompt or REFLECTION_SYSTEM_PROMPT,
                user_message=prompt,
                max_tokens=2048,
                temperature=0.3,
            )

            cost = self._reflection_agent.estimate_cost(
                response["input_tokens"], response["output_tokens"]
            )
            self.cost_tracker.record(
                model=self.config.reflection_model,
                purpose="reflection",
                input_tokens=response["input_tokens"],
                output_tokens=response["output_tokens"],
                cost_usd=cost,
            )

            return self._parse_reflection(response["content"])

        except Exception as e:
            logger.error(f"Reflection failed: {e}")
            return {"summary": f"Reflection error: {e}"}

    def _build_user_message(
        self,
        context: ReasoningContext,
        event_type: str,
        event_payload: Optional[dict] = None,
    ) -> str:
        """Build the user message for Claude from context and event."""
        parts = [
            f"## Event: {event_type}",
        ]

        if event_payload:
            parts.append(f"\n## Event Data\n{json.dumps(event_payload, indent=2, default=str)}")

        parts.append(f"\n{context.to_prompt_string()}")

        parts.append(
            "\n## Instructions\n"
            "Analyze the context and event, then respond with a JSON DecisionOutput. "
            "Choose the most appropriate action given the current situation.\n\n"
            "Important: Only reference data provided above. "
            "If a value is not in the context, do not assume or estimate it."
        )

        return "\n".join(parts)

    def _build_position_exit_message(
        self,
        context: ReasoningContext,
        event_payload: dict,
    ) -> str:
        """Build a focused user message for a single position exit check.

        Only includes the target position's details and market context
        (VIX, SPY). No other positions or staged candidates.

        Args:
            context: Full reasoning context (we extract selectively)
            event_payload: Must contain trade_id, symbol, strike, pnl_pct

        Returns:
            User message string for Claude
        """
        trade_id = event_payload.get("trade_id", "UNKNOWN")
        symbol = event_payload.get("symbol", "UNKNOWN")
        strike = event_payload.get("strike", 0)
        pnl_pct = event_payload.get("pnl_pct", 0)
        option_type = event_payload.get("option_type", "PUT")
        # Short code: P for put, C for call
        option_code = "C" if option_type.upper() in ("CALL", "C") else "P"

        # Find the matching position from context
        target_pos = None
        for pos in context.open_positions:
            if pos.get("trade_id") == trade_id:
                target_pos = pos
                break

        parts = [
            f"## Event: POSITION_EXIT_CHECK",
            f"\n## Target Position",
            f"- trade_id: {trade_id}",
            f"- symbol: {symbol}",
            f"- strike: ${strike}",
            f"- option_type: {option_type} ({option_code})",
            f"- P&L: {pnl_pct:+.1f}%",
        ]

        if target_pos:
            parts.append(f"- entry_premium: ${target_pos.get('entry_premium', 'UNKNOWN')}")
            parts.append(f"- expiration: {target_pos.get('expiration', 'UNKNOWN')}")
            parts.append(f"- contracts: {target_pos.get('contracts', 'UNKNOWN')}")
            parts.append(f"- DTE: {target_pos.get('dte', 'UNKNOWN')}")
            if target_pos.get("current_mid"):
                parts.append(f"- current_mid: ${target_pos['current_mid']}")

        # Market context (minimal)
        mc = context.market_context
        parts.append(f"\n## Market Context")
        parts.append(f"- VIX: {mc.get('vix', 'UNKNOWN')}")
        parts.append(f"- SPY: ${mc.get('spy_price', 'UNKNOWN')}")
        if mc.get("conditions_favorable") is not None:
            parts.append(f"- Conditions favorable: {mc['conditions_favorable']}")

        parts.append(
            f"\n## Instructions\n"
            f"Evaluate this single position and decide: CLOSE_POSITION or MONITOR_ONLY.\n"
            f"If closing, metadata MUST include {{\"trade_id\": \"{trade_id}\"}}."
        )

        return "\n".join(parts)

    def _build_reflection_prompt(
        self, decisions: list[dict], trades: list[dict]
    ) -> str:
        """Build the EOD reflection prompt."""
        parts = [
            "## Today's Decisions",
            json.dumps(decisions, indent=2, default=str),
            "\n## Today's Trades",
            json.dumps(trades, indent=2, default=str),
            "\n## Instructions",
            "Review the decisions and trades. Categorize each decision as "
            "correct, lucky (right outcome but wrong reasoning), or wrong. "
            "Suggest patterns to investigate and any prior/strategy updates.",
        ]
        return "\n".join(parts)

    def _parse_response(
        self,
        content: str,
        valid_actions: Optional[set[str]] = None,
    ) -> list[DecisionOutput]:
        """Parse Claude's JSON response into list of DecisionOutput.

        Handles both multi-action format:  {"assessment": "...", "actions": [...]}
        and legacy single-action format:   {"action": "...", "confidence": ...}

        Args:
            content: Raw response text from Claude
            valid_actions: Override allowed action set (defaults to VALID_ACTIONS)

        Returns:
            List of DecisionOutput, or empty list if parsing fails entirely
        """
        allowed = valid_actions or VALID_ACTIONS
        try:
            # Try to extract JSON from response
            text = content.strip()

            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            data = json.loads(text)

            # Multi-action format
            if "actions" in data and isinstance(data["actions"], list):
                assessment = data.get("assessment", "")
                results: list[DecisionOutput] = []
                for item in data["actions"]:
                    parsed = self._parse_single_action(item, allowed)
                    if parsed:
                        results.append(parsed)
                if not results:
                    return [self._fallback_monitor("No valid actions in plan")]
                # Attach assessment to first action's metadata for downstream access
                results[0].metadata["_plan_assessment"] = assessment
                return results

            # Legacy single-action format (backward compat)
            parsed = self._parse_single_action(data, allowed)
            return [parsed] if parsed else []

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse Claude response: {e}")
            return []

    def _parse_single_action(
        self, data: dict, allowed: set[str]
    ) -> Optional[DecisionOutput]:
        """Parse a single action dict into DecisionOutput.

        Args:
            data: JSON dict with action, confidence, reasoning, etc.
            allowed: Set of valid action strings

        Returns:
            DecisionOutput if valid, None if parsing fails
        """
        action = data.get("action", "MONITOR_ONLY")
        if action not in allowed:
            logger.warning(
                f"Invalid action '{action}' (allowed: {allowed}), defaulting to MONITOR_ONLY"
            )
            action = "MONITOR_ONLY"

        confidence = max(0.0, min(1.0, float(data.get("confidence", 0.5))))

        return DecisionOutput(
            action=action,
            confidence=confidence,
            reasoning=data.get("reasoning", ""),
            key_factors=data.get("key_factors", []),
            risks_considered=data.get("risks_considered", []),
            metadata=data.get("metadata") or {},
        )

    @staticmethod
    def _fallback_monitor(reason: str) -> DecisionOutput:
        """Create a fallback MONITOR_ONLY decision."""
        return DecisionOutput(
            action="MONITOR_ONLY",
            confidence=1.0,
            reasoning=reason,
            key_factors=["parse_fallback"],
        )

    def _parse_reflection(self, content: str) -> dict:
        """Parse reflection response."""
        try:
            text = content.strip()
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            return json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return {"summary": content[:500]}
