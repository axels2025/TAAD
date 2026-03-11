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
from src.utils.timezone import utc_now


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
- Sudden extreme market event (flash crash, circuit breakers triggered)
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
- VIX spiking rapidly above 40? → CLOSE_ALL_POSITIONS
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
- ONLY cite numbers (VIX, premiums, P&L) present in the context
- If a value is missing: state "X is not available in context" — never fabricate
- Every number in your reasoning must be traceable to the input context

## What Is NOT a Valid Reason to Return MONITOR_ONLY on an Entry Day

- "Individual position P&L is not available" → still stage new trades
- "I have existing underwater positions" → new uncorrelated trades are independent
- "A previous staging attempt failed" → retry it
- "VIX is low" → thin premiums are still profitable; prioritise high-IV candidates
- "I don't have enough information" → VIX + day-of-week is sufficient to stage
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
Context: Day=Monday, VIX=18.5 [market_data], Open Positions=3 (all within normal P&L range) [position_snapshot], Staged Candidates=NONE [pipeline_state]

{
  "action": "STAGE_CANDIDATES",
  "confidence": 0.88,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=18.5, no circuit breakers, no geopolitical events. No emergency. STEP 2 - ENTRY DAY CHECK: Monday, primary entry day (92% historical win rate). STEP 3 - PIPELINE CHECK: VIX regime = Normal (15-20), optimal environment. Staged Candidates = NONE — pipeline is empty. Must run auto-scan. Existing 3 positions are within normal range and do not block new entries. CONCLUSION: Monday with empty pipeline. Stage new candidates.",
  "key_factors": ["Monday primary entry day", "VIX 18.5 Normal regime — optimal conditions", "Pipeline empty — no staged candidates"],
  "risks_considered": ["3 existing positions — staging will check sector correlation", "Premiums moderate at VIX 18.5 — prioritise higher-IV candidates"],
  "metadata": null
}

### Example 2: Monday, staged candidates ready, mixed-health existing positions
Context: Day=Monday, VIX=22.3 [market_data], Open Positions=[AAPL P180 at +35% [position_snapshot], MSTR P140 at -88% [position_snapshot]], Staged Candidates=[NVDA P115 $0.92, TSLA P230 $1.38] status=STAGED [pipeline_state]

{
  "action": "EXECUTE_TRADES",
  "confidence": 0.85,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=22.3 Elevated but not extreme, no circuit breakers. No emergency. STEP 2 - ENTRY DAY CHECK: Monday, primary entry day. STEP 3 - PIPELINE CHECK: VIX regime = Elevated (20-30) — stage normally, premiums richer, OTM buffers to be verified by execution scheduler. 2 staged candidates (NVDA P115, TSLA P230) with status=STAGED — ready for submission. MSTR at -88% is concerning but below stop-loss threshold and is managed by app code, not this decision. It does not block new uncorrelated trades. AAPL at +35% is healthy. CONCLUSION: Staged candidates ready on primary entry day. Execute.",
  "key_factors": ["Monday primary entry day", "2 candidates status=STAGED and ready", "VIX 22.3 Elevated — richer premiums favour selling"],
  "risks_considered": ["MSTR at -88% approaching stop-loss — app code will handle if triggered", "Elevated VIX — execution scheduler will validate premiums and OTM buffers"],
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
Context: Day=Wednesday, VIX=16.2 [market_data], Open Positions=5 (all -20% to +40% P&L range) [position_snapshot], Staged Candidates=NONE [pipeline_state]

{
  "action": "MONITOR_ONLY",
  "confidence": 0.92,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=16.2 Low regime, no circuit breakers. No emergency. STEP 2 - ENTRY DAY CHECK: Wednesday — not an entry day. STEP 4 - ANOMALY CHECK: No data corruption, no system failures. STEP 5 - MONITOR_ONLY VALIDATION: Not entry day, no emergency, no anomalies. CONCLUSION: Mid-week hold. App code managing routine exits on existing 5 positions.",
  "key_factors": ["Wednesday not an entry day", "All 5 positions within normal P&L range", "VIX 16.2 Low — calm market conditions"],
  "risks_considered": ["Low VIX may compress premiums on Monday — will prioritise high-IV candidates then"],
  "metadata": null
}

### Example 5: Tuesday, previous staging failed (transient), underwater position exists
Context: Day=Tuesday, VIX=25.3 [market_data], Open Positions=[MSTR P140 at -95% [position_snapshot]], Staged Candidates=NONE [pipeline_state], Recent Events=["STAGE_CANDIDATES failed 09:35 — IBKR connection timeout" [event_log]]

{
  "action": "STAGE_CANDIDATES",
  "confidence": 0.78,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=25.3 Elevated but not extreme, no circuit breakers. No emergency. STEP 2 - ENTRY DAY CHECK: Tuesday, secondary entry day. STEP 3 - PIPELINE CHECK: VIX regime = Elevated (20-30) — stage with OTM buffer verification. Pipeline is empty. Previous failure at 09:35 was a connection timeout — transient error, not a fundamental problem. Must retry. MSTR at -95% is managed by app code stop-loss rules; it does not block new uncorrelated trades. CONCLUSION: Retry STAGE_CANDIDATES. Elevated VIX provides richer premiums.",
  "key_factors": ["Tuesday secondary entry day", "Previous failure was transient — connection timeout", "VIX 25.3 Elevated — richer premium environment"],
  "risks_considered": ["MSTR at -95% approaching stop-loss — app code will handle", "IBKR connection may still be unstable — second failure should escalate to REQUEST_HUMAN_REVIEW"],
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
Context: Day=Monday, VIX=52.4 [market_data], Market Events=["NYSE circuit breaker Level 1 triggered 10:02" [event_log]], Open Positions=6 [position_snapshot]

{
  "action": "CLOSE_ALL_POSITIONS",
  "confidence": 0.95,
  "reasoning": "STEP 1 - EMERGENCY CHECK: VIX=52.4 Extreme (>40) and spiking rapidly. NYSE circuit breaker Level 1 confirmed triggered at 10:02. This is a systemic market event with simultaneous exposure across all open positions. Standard stop-loss rules are insufficient when the entire market is in freefall — fill quality degrades and losses can exceed thresholds before orders execute. CONCLUSION: CLOSE_ALL_POSITIONS immediately. Capital preservation takes absolute priority.",
  "key_factors": ["VIX=52.4 Extreme and accelerating", "NYSE circuit breaker Level 1 confirmed", "All 6 positions exposed to systemic selloff"],
  "risks_considered": ["Fill quality may be poor in this environment — market orders preferred over limits for speed", "Some positions may not fill immediately — system should retry aggressively"],
  "metadata": {"reason": "Systemic market crash: NYSE circuit breaker triggered, VIX 52.4"}
}"""


POSITION_EXIT_SYSTEM_PROMPT = """You are the exit judgment layer in an autonomous naked put options trading system.

You are ONLY called when the rule engine cannot make a clear decision.
Hard rules (>=+75% profit, <=-300% loss, DTE=0) are handled in code before you are called.
Your job is to resolve GENUINE AMBIGUITY — situations where multiple valid considerations conflict.

## Your Decision
Evaluate whether to CLOSE_POSITION or MONITOR_ONLY based on the full context provided.

## What Constitutes Genuine Reasons to Close Early
- Delta has deteriorated significantly (abs delta trending toward 0.40+) with no recovery signal
- Stock is in confirmed downtrend AND approaching the strike (distance_to_strike_pct < 8%)
- IV has expanded significantly AFTER entry (not mean-reverting) — premium you sold is now worth much more
- VIX has spiked AND this position is underwater — compounding risk
- Earnings discovered within DTE window that were not present at entry
- Profit is solid (+50%+) with very little DTE remaining — theta is nearly exhausted, risk/reward has inverted

## What Does NOT Justify Early Close
- VIX elevated but position is comfortably OTM and profitable
- Stock moved down modestly but delta is still well below 0.30
- Profit exists but DTE still sufficient for further theta decay
- Market is volatile but position has strong OTM buffer

## Confidence Calibration
- 0.9+: Strong conviction with multiple confirming signals
- 0.7-0.9: Clear lean with one counterargument acknowledged
- 0.5-0.7: Genuine coin-flip — your TENSION section should be substantial
- Below 0.5: Default to MONITOR_ONLY (if you are not confident, do not act)

## Grounding Rules
- ONLY reference data in the context provided
- If a field is null or missing, state this — do not infer or fabricate
- Do NOT speculate about news, earnings, or events not mentioned in the context

## Reasoning Format — CRITICAL
Your reasoning field MUST start by identifying the position using its EXACT option type:
  "SYMBOL STRIKE<TYPE> exp=YYYY-MM-DD (DTE=N):"

Then structure as: OBSERVATION (what the data shows) -> TENSION (what makes this ambiguous) -> RESOLUTION (your judgment call)

The TENSION field is mandatory — if there is no genuine tension, this position should have been handled by the rule engine, not you.

Example: "NVDA 800.0P exp=2026-03-20 (DTE=3): OBSERVATION: Position at +58% profit, delta stable at 0.15, stock sideways. TENSION: Profit is solid but 17 points below the 75% hard target. DTE=3 means minimal theta left to capture vs. reversal risk over 3 trading days. RESOLUTION: Close — risk/reward has inverted with so little time remaining."

## Response Format
Respond with ONLY a JSON object:
```json
{
  "action": "CLOSE_POSITION",
  "confidence": 0.80,
  "reasoning": "SYMBOL STRIKEtype exp=DATE (DTE=N): OBSERVATION: ... TENSION: ... RESOLUTION: ...",
  "key_factors": ["the 2-3 factors that drove your decision"],
  "risks_considered": ["risks on the other side of your decision"],
  "learning_signal": "one sentence on what pattern this decision represents for the learning engine",
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
            timestamp=utc_now(),
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
        entry_days: Optional[list[str]] = None,
    ):
        """Initialize reasoning engine.

        Args:
            db_session: SQLAlchemy session for cost tracking
            config: Claude configuration (uses defaults if None)
            entry_days: Configurable entry days (default Monday, Tuesday)
        """
        self.db = db_session
        self.config = config or ClaudeConfig()
        self.cost_tracker = CostTracker(db_session, self.config.daily_cost_cap_usd)
        base_prompt = self.config.reasoning_system_prompt or REASONING_SYSTEM_PROMPT
        self.system_prompt = self._inject_entry_days(
            base_prompt, entry_days or ["Monday", "Tuesday"]
        )

        # Initialize agents for different purposes
        self._reasoning_agent = BaseAgent(
            model=self.config.reasoning_model,
            max_retries=self.config.max_retries,
        )
        self._reflection_agent = BaseAgent(
            model=self.config.reflection_model,
            max_retries=self.config.max_retries,
        )

    @staticmethod
    def _inject_entry_days(prompt: str, entry_days: list[str]) -> str:
        """Replace hardcoded entry-day logic in the system prompt.

        Rewrites Step 2 (ENTRY DAY CHECK) and the Role & Bias section
        to reflect the configured entry days instead of hardcoded Mon/Tue.
        """
        all_days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        non_entry = [d for d in all_days if d not in entry_days]

        # Build Step 2 replacement lines
        step2_lines = []
        for day in entry_days:
            label = "PRIMARY" if day == entry_days[0] else "SECONDARY"
            step2_lines.append(f"- {day} = {label} entry day → proceed to Step 3")
        if non_entry:
            step2_lines.append(
                f"- {', '.join(non_entry)} = NOT entry days → skip to Step 4"
            )
        step2_lines.append(
            "- Weekend / market closed = NOT entry days → skip to Step 4"
        )
        step2_block = "\n".join(step2_lines)

        # Replace Step 2 block
        old_step2 = (
            "- Monday = PRIMARY entry day → proceed to Step 3\n"
            "- Tuesday = SECONDARY entry day → proceed to Step 3\n"
            "- Wednesday–Friday = NOT entry days → skip to Step 4\n"
            "- Weekend / market closed = NOT entry days → skip to Step 4"
        )
        prompt = prompt.replace(old_step2, step2_block)

        # Replace Role & Bias entry-day references
        entry_str = " and ".join(entry_days) if len(entry_days) <= 2 else ", ".join(entry_days)
        prompt = prompt.replace(
            "Monday is the PRIMARY entry day (92% historical win rate, Friday expiry).\n"
            "Tuesday is the SECONDARY entry day (still profitable, slightly lower edge).",
            f"Configured entry days: {entry_str}.\n"
            f"On entry days: find reasons TO trade. Only hold back on specific, concrete, articulable risk.",
        )

        # Remove duplicate "On entry days" line if it now appears twice
        prompt = prompt.replace(
            "On entry days: find reasons TO trade. Only hold back on specific, concrete, articulable risk.\n"
            "On entry days: find reasons TO trade. Only hold back on specific, concrete, articulable risk.",
            "On entry days: find reasons TO trade. Only hold back on specific, concrete, articulable risk.",
        )

        return prompt

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
        """Build an enriched user message for a single position exit check.

        Includes position identity, P&L status, risk indicators (delta, IV,
        stock trends from PositionSnapshot), market context, and the
        escalation reason explaining why the rule engine couldn't decide.

        Args:
            context: Full reasoning context (we extract selectively)
            event_payload: Enriched payload with trade_id, symbol, strike,
                pnl_pct, snapshot data, escalation_reason, sector_context

        Returns:
            User message string for Claude
        """
        trade_id = event_payload.get("trade_id", "UNKNOWN")
        symbol = event_payload.get("symbol", "UNKNOWN")
        strike = event_payload.get("strike", 0)
        pnl_pct = event_payload.get("pnl_pct", 0)
        option_type = event_payload.get("option_type", "PUT")
        dte = event_payload.get("dte", "Unknown")
        option_code = "C" if option_type.upper() in ("CALL", "C") else "P"
        snapshot = event_payload.get("snapshot", {})
        escalation_reason = event_payload.get("escalation_reason", "No specific reason provided")
        sector_context = event_payload.get("sector_context", "Unknown")

        # Find the matching position from context for entry data
        target_pos = None
        for pos in context.open_positions:
            if pos.get("trade_id") == trade_id:
                target_pos = pos
                break

        entry_premium = target_pos.get("entry_premium", "Unknown") if target_pos else "Unknown"
        expiration = target_pos.get("expiration", "Unknown") if target_pos else "Unknown"
        contracts = target_pos.get("contracts", "Unknown") if target_pos else "Unknown"
        entry_date = target_pos.get("entry_date", "Unknown") if target_pos else "Unknown"
        current_premium = snapshot.get("current_premium", "Unknown")

        # Compute derived fields
        profit_target_pct = 75.0
        stop_loss_pct = -300.0
        if isinstance(pnl_pct, (int, float)):
            pt_status = f"{profit_target_pct - pnl_pct:.1f}pp away" if pnl_pct < profit_target_pct else "TRIGGERED"
            sl_status = f"{abs(stop_loss_pct - pnl_pct):.1f}pp away" if pnl_pct > stop_loss_pct else "TRIGGERED"
        else:
            pt_status = "Unknown"
            sl_status = "Unknown"

        # Market context
        mc = context.market_context
        vix_current = mc.get("vix", "Unknown")

        # Format snapshot fields (use "Unknown" for missing data)
        def fmt(val, fmt_str=".3f", prefix="", suffix=""):
            if val is None:
                return "Unknown"
            try:
                return f"{prefix}{val:{fmt_str}}{suffix}"
            except (ValueError, TypeError):
                return str(val)

        parts = [
            "## Position Under Evaluation",
            "",
            "**Identity**",
            f"- Trade ID: {trade_id}",
            f"- Symbol: {symbol}",
            f"- Option: {strike}{option_code} exp={expiration} (DTE={dte})",
            f"- Entry date: {entry_date}",
            f"- Contracts: {contracts}",
            "",
            "**P&L Status**",
            f"- Entry premium: ${entry_premium}",
            f"- Current premium: ${current_premium}" + (f" ({pnl_pct:+.1f}%)" if isinstance(pnl_pct, (int, float)) else ""),
            f"- Profit target (+{profit_target_pct:.0f}%): {pt_status}",
            f"- Stop loss ({stop_loss_pct:.0f}%): {sl_status}",
            "",
            "**Risk Indicators**",
            f"- Current delta: {fmt(snapshot.get('delta'))}",
            f"- Entry delta: {fmt(snapshot.get('entry_delta'))}",
            f"- Delta trend: {snapshot.get('delta_trend', 'Unknown')}",
            f"- Distance to strike: {fmt(snapshot.get('distance_to_strike_pct'), '.1f', suffix='%')}",
            f"- Stock price: {fmt(snapshot.get('stock_price'), '.2f', prefix='$')} (entry: {fmt(snapshot.get('entry_stock_price'), '.2f', prefix='$')})",
            f"- Stock trend: {snapshot.get('stock_trend', 'Unknown')}",
            f"- Theta per day: {fmt(snapshot.get('theta'))}",
            "",
            "**Volatility Context**",
            f"- Current IV: {fmt(snapshot.get('iv'), '.1f', suffix='%')}",
            f"- IV trend: {snapshot.get('iv_trend', 'Unknown')}",
            f"- Entry IV: {fmt(snapshot.get('entry_iv'), '.1f', suffix='%')}",
            f"- VIX current: {vix_current}",
            "",
            "**Context**",
            f"- {sector_context}",
            f"- Portfolio delta: {fmt(event_payload.get('portfolio_delta'), '.2f')}",
            f"- Margin utilisation: {fmt(event_payload.get('margin_utilisation_pct'), '.1f', suffix='%')}",
            f"- Earnings within DTE: {self._format_earnings(event_payload)}",
            f"- Is OpEx week: {'Yes' if event_payload.get('is_opex_week') else 'No'}",
            "",
            "## Why the Rule Engine Escalated This",
            escalation_reason,
            "",
            "## Response Format",
            "Respond with ONLY valid JSON:",
            "```json",
            "{",
            '  "action": "CLOSE_POSITION" or "MONITOR_ONLY",',
            "  \"confidence\": 0.0-1.0,",
            f'  "reasoning": "{symbol} {strike}{option_code} exp={expiration} (DTE={dte}): OBSERVATION: ... TENSION: ... RESOLUTION: ...",',
            '  "key_factors": ["the 2-3 factors that drove your decision"],',
            '  "risks_considered": ["risks on the other side of your decision"],',
            '  "learning_signal": "one sentence on what pattern this decision represents for the learning engine",',
            f'  "metadata": {{"trade_id": "{trade_id}"}}',
            "}",
            "```",
        ]

        return "\n".join(parts)

    def _format_earnings(self, payload: dict) -> str:
        """Format earnings proximity for the position exit message.

        Returns a human-readable string indicating whether earnings
        fall within the option's DTE window.
        """
        earnings_in_dte = payload.get("earnings_in_dte")
        if earnings_in_dte is None:
            return "Unknown"
        days = payload.get("days_to_earnings")
        date_str = payload.get("earnings_date")
        if earnings_in_dte:
            return f"YES — {days}d away ({date_str})"
        elif days is not None and days <= 30:
            return f"No (next: {days}d away, {date_str})"
        else:
            return "No"

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

        metadata = data.get("metadata") or {}
        # Capture learning_signal if present (position exit ambiguity resolver)
        if data.get("learning_signal"):
            metadata["learning_signal"] = data["learning_signal"]

        return DecisionOutput(
            action=action,
            confidence=confidence,
            reasoning=data.get("reasoning", ""),
            key_factors=data.get("key_factors", []),
            risks_considered=data.get("risks_considered", []),
            metadata=metadata,
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
