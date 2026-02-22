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
    "CLOSE_POSITION",
    "ADJUST_PARAMETERS",
    "RUN_EXPERIMENT",
    "REQUEST_HUMAN_REVIEW",
    "EMERGENCY_STOP",
}

REASONING_SYSTEM_PROMPT = """You are the reasoning engine for an autonomous naked put options trading system.

Your role is to analyze the current context and decide the best action to take.
You must respond with a valid JSON object matching the DecisionOutput schema.

## Rules
1. Safety first: when in doubt, choose MONITOR_ONLY
2. Never exceed risk limits or margin constraints
3. Consider recent patterns and learning outcomes
4. Factor in current market conditions (VIX, trends, regime)
5. If confidence is below 0.6, recommend REQUEST_HUMAN_REVIEW
6. If any anomaly is detected, recommend REQUEST_HUMAN_REVIEW

## Grounding Requirements
7. ONLY reference symbols that appear in the context below
8. ONLY cite numbers (VIX, premiums, strikes, P&L) present in the context
9. If data is missing or marked UNKNOWN, state this — never fabricate values
10. If you cannot determine a confident action, choose MONITOR_ONLY

## Reasoning Structure
Your reasoning MUST follow: OBSERVATION (cite specific numbers) → ASSESSMENT → ACTION rationale

## Uncertainty Calibration
- confidence < 0.6 = insufficient data or unclear situation
- confidence 0.6–0.8 = reasonable certainty with some unknowns
- confidence > 0.8 = strong evidence in context supports this action
- NEVER set confidence > 0.9 unless multiple confirming data points are present
- MONITOR_ONLY when uncertain is always correct

## Valid Actions
- MONITOR_ONLY: No action needed, continue observing
- STAGE_CANDIDATES: Run Sunday session to find trade candidates
- EXECUTE_TRADES: Execute staged/confirmed trades
- CLOSE_POSITION: Close a specific open position (requires position_id in metadata)
- ADJUST_PARAMETERS: Propose strategy parameter change (requires experiment design)
- RUN_EXPERIMENT: Start a new A/B experiment
- REQUEST_HUMAN_REVIEW: Escalate to human for review
- EMERGENCY_STOP: Halt all trading immediately

## Response Format
Respond with ONLY a JSON object:
```json
{
  "action": "MONITOR_ONLY",
  "confidence": 0.85,
  "reasoning": "Brief explanation of why this action was chosen",
  "key_factors": ["factor1", "factor2"],
  "risks_considered": ["risk1", "risk2"],
  "metadata": {}
}
```"""


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
    ) -> DecisionOutput:
        """Run Claude reasoning on the given context.

        Checks cost cap, assembles prompt, calls Claude, parses response.
        On failure: retries once, then falls back to MONITOR_ONLY.

        Args:
            context: Assembled reasoning context
            event_type: The triggering event type
            event_payload: Optional event-specific data

        Returns:
            DecisionOutput with action, confidence, and reasoning
        """
        # Check cost cap
        if not self.cost_tracker.can_call():
            logger.warning("Daily Claude cost cap exceeded, falling back to MONITOR_ONLY")
            return DecisionOutput(
                action="MONITOR_ONLY",
                confidence=1.0,
                reasoning="Daily Claude API cost cap exceeded. Monitoring only.",
                key_factors=["cost_cap_exceeded"],
            )

        # Assemble user message
        user_message = self._build_user_message(context, event_type, event_payload)

        # Call Claude with retry
        for attempt in range(2):  # max 2 attempts
            try:
                response = self._reasoning_agent.send_message(
                    system_prompt=REASONING_SYSTEM_PROMPT,
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

                # Parse response
                decision = self._parse_response(response["content"])
                if decision:
                    return decision

                if attempt == 0:
                    logger.warning("Failed to parse Claude response, retrying...")
                    continue

            except Exception as e:
                logger.error(f"Claude reasoning error (attempt {attempt + 1}): {e}")
                if attempt == 0:
                    continue

        # Fallback
        logger.warning("Claude reasoning failed, falling back to MONITOR_ONLY")
        return DecisionOutput(
            action="MONITOR_ONLY",
            confidence=1.0,
            reasoning="Claude reasoning failed. Falling back to monitoring.",
            key_factors=["reasoning_failure"],
        )

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
                system_prompt=(
                    "You are reviewing today's trading decisions. "
                    "Identify what went well, what was lucky, and what went wrong. "
                    "Suggest patterns to investigate. "
                    "Respond with a JSON object with keys: "
                    "correct_decisions, lucky_decisions, wrong_decisions, "
                    "patterns_to_investigate, prior_updates, summary."
                ),
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

    def _parse_response(self, content: str) -> Optional[DecisionOutput]:
        """Parse Claude's JSON response into DecisionOutput.

        Args:
            content: Raw response text from Claude

        Returns:
            DecisionOutput if valid, None if parsing fails
        """
        try:
            # Try to extract JSON from response
            text = content.strip()

            # Handle markdown code blocks
            if "```json" in text:
                text = text.split("```json")[1].split("```")[0].strip()
            elif "```" in text:
                text = text.split("```")[1].split("```")[0].strip()

            data = json.loads(text)

            action = data.get("action", "MONITOR_ONLY")
            if action not in VALID_ACTIONS:
                logger.warning(f"Invalid action '{action}', defaulting to MONITOR_ONLY")
                action = "MONITOR_ONLY"

            confidence = float(data.get("confidence", 0.5))
            confidence = max(0.0, min(1.0, confidence))

            return DecisionOutput(
                action=action,
                confidence=confidence,
                reasoning=data.get("reasoning", ""),
                key_factors=data.get("key_factors", []),
                risks_considered=data.get("risks_considered", []),
                metadata=data.get("metadata", {}),
            )

        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            logger.warning(f"Failed to parse Claude response: {e}")
            return None

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
