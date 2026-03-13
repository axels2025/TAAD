"""Chief Risk Officer (CRO) adversarial agent.

Inspired by the ATLAS framework's CRO concept: before executing trades,
a separate AI agent actively looks for reasons NOT to trade. This provides
a devil's advocate counterweight to the reasoning engine's entry-day bias.

The CRO agent runs ONLY when:
- The reasoning engine decides EXECUTE_TRADES or STAGE_CANDIDATES
- There are concrete trade candidates to challenge

It does NOT:
- Have authority to block trades (that's the human's/autonomy governor's job)
- Replace the risk governor's mechanical checks
- Run on MONITOR_ONLY or other non-trade actions

Output is a structured risk assessment that gets:
1. Logged in the DecisionAudit for the trade
2. Displayed to the human via dashboard notification
3. Used to escalate to human review if objections are severe
"""

import json
from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from src.agents.base_agent import BaseAgent
from src.agentic.working_memory import ReasoningContext


CRO_SYSTEM_PROMPT = """You are the Chief Risk Officer (CRO) for an autonomous options selling system.
Your SOLE job is to find reasons NOT to execute the proposed trades. You are the devil's advocate.

## Your Mandate

The primary reasoning engine has an explicit entry-day bias — it looks for reasons TO trade.
You exist to counterbalance that bias. Challenge every assumption. Attack every trade.

You are NOT trying to be balanced. You are NOT trying to agree. You are the adversary.
If you cannot find strong objections, say so honestly — but try hard first.

## What You Evaluate

For each staged candidate, assess:

### 1. Single-Stock Risk (most important for individual stock options)
- Is this stock in a confirmed downtrend? (price below key moving averages)
- Are there upcoming earnings, FDA decisions, or catalysts within the DTE window?
- Has the stock had unusual volume or price action suggesting informed trading?
- Is implied volatility elevated due to a known event (earnings IV crush risk)?
- Has this stock been in the news for negative reasons recently?

### 2. Concentration Risk
- How correlated is this trade with existing open positions?
- Are we overweight in one sector?
- Would this trade increase our exposure to a single macro factor?

### 3. Market Regime Risk
- Does the current VIX level justify selling premium, or is it pricing in a known risk?
- Is the VIX term structure in backwardation (near-term fear > long-term)?
- Are credit spreads widening (risk-off signal)?
- Is there a macro event upcoming (FOMC, CPI, employment) within the DTE window?

### 4. Position-Level Risk
- Is the OTM buffer sufficient for the current volatility regime?
- Is the premium adequate compensation for the risk taken?
- Is the delta appropriate, or is this too close to ATM?

### 5. Portfolio-Level Risk
- What is the total portfolio exposure if all positions move against us simultaneously?
- Are we at/near position count limits?
- Is daily/weekly P&L already underwater?

## What You Must NOT Do
- Do NOT recommend specific trades or alternatives
- Do NOT evaluate whether the strategy overall is sound
- Do NOT comment on past trades or historical performance
- Do NOT fabricate data — only reference what is in the provided context
- Do NOT object to trades purely because of existing underwater positions (new trades are independent)

## Response Format

Respond with ONLY a JSON object:

{
  "overall_risk_level": "LOW | MODERATE | HIGH | CRITICAL",
  "should_escalate": true/false,
  "objections": [
    {
      "severity": "LOW | MODERATE | HIGH | CRITICAL",
      "category": "single_stock | concentration | market_regime | position_level | portfolio_level",
      "target": "symbol or 'portfolio'",
      "objection": "specific concern in 1-2 sentences",
      "evidence": "data from context supporting this concern"
    }
  ],
  "strongest_objection": "the single most compelling reason to NOT execute, in one sentence",
  "assessment_summary": "2-3 sentence overall risk assessment"
}

## Severity Guide
- LOW: Minor concern, trade is probably fine. Noting for completeness.
- MODERATE: Real concern that the trader should be aware of. Not a blocker.
- HIGH: Significant risk that warrants careful consideration. Should escalate to human.
- CRITICAL: Trade should NOT proceed without explicit human approval. Concrete, specific danger."""


@dataclass
class CROObjection:
    """A single risk objection from the CRO agent."""

    severity: str  # LOW, MODERATE, HIGH, CRITICAL
    category: str  # single_stock, concentration, market_regime, etc.
    target: str  # symbol or "portfolio"
    objection: str
    evidence: str


@dataclass
class CROAssessment:
    """Full CRO risk assessment for a set of trade candidates."""

    overall_risk_level: str  # LOW, MODERATE, HIGH, CRITICAL
    should_escalate: bool
    objections: list[CROObjection] = field(default_factory=list)
    strongest_objection: str = ""
    assessment_summary: str = ""
    raw_response: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    error: Optional[str] = None

    @property
    def has_critical(self) -> bool:
        """Whether any objection is CRITICAL severity."""
        return any(o.severity == "CRITICAL" for o in self.objections)

    @property
    def has_high(self) -> bool:
        """Whether any objection is HIGH or CRITICAL severity."""
        return any(o.severity in ("HIGH", "CRITICAL") for o in self.objections)

    def to_dict(self) -> dict:
        """Serialize for storage in DecisionAudit or notification."""
        return {
            "overall_risk_level": self.overall_risk_level,
            "should_escalate": self.should_escalate,
            "objections": [
                {
                    "severity": o.severity,
                    "category": o.category,
                    "target": o.target,
                    "objection": o.objection,
                    "evidence": o.evidence,
                }
                for o in self.objections
            ],
            "strongest_objection": self.strongest_objection,
            "assessment_summary": self.assessment_summary,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
        }


class CROAgent:
    """Adversarial risk review agent.

    Challenges proposed trades by actively seeking reasons NOT to execute.
    Uses a separate Claude call with a devil's advocate system prompt.
    """

    def __init__(
        self,
        model: str = "claude-sonnet-4-5-20250929",
        max_retries: int = 2,
        timeout: float = 45.0,
    ):
        """Initialize CRO agent.

        Args:
            model: Claude model ID (Sonnet recommended for cost efficiency)
            max_retries: Max retry attempts
            timeout: Request timeout in seconds
        """
        self._agent = BaseAgent(
            model=model,
            max_retries=max_retries,
            timeout=timeout,
        )

    def review(
        self,
        context: ReasoningContext,
        primary_decision_reasoning: str = "",
    ) -> CROAssessment:
        """Run adversarial risk review on staged candidates.

        Args:
            context: The same ReasoningContext given to the reasoning engine
            primary_decision_reasoning: The reasoning engine's rationale (so CRO can attack it)

        Returns:
            CROAssessment with objections and risk level
        """
        # Build the user message with all relevant data
        user_message = self._build_review_prompt(context, primary_decision_reasoning)

        try:
            response = self._agent.send_message(
                system_prompt=CRO_SYSTEM_PROMPT,
                user_message=user_message,
                max_tokens=2048,
                temperature=0.3,
            )

            assessment = self._parse_response(response["content"])
            assessment.raw_response = response["content"]
            assessment.input_tokens = response["input_tokens"]
            assessment.output_tokens = response["output_tokens"]
            assessment.cost_usd = self._agent.estimate_cost(
                response["input_tokens"], response["output_tokens"]
            )

            logger.info(
                f"CRO review complete: risk={assessment.overall_risk_level}, "
                f"objections={len(assessment.objections)}, "
                f"escalate={assessment.should_escalate}, "
                f"cost=${assessment.cost_usd:.4f}"
            )

            return assessment

        except Exception as e:
            logger.error(f"CRO agent failed: {e}", exc_info=True)
            return CROAssessment(
                overall_risk_level="UNKNOWN",
                should_escalate=False,
                error=str(e),
            )

    def _build_review_prompt(
        self,
        context: ReasoningContext,
        primary_reasoning: str,
    ) -> str:
        """Build the user message for the CRO review.

        Includes all context the CRO needs to challenge the trades.
        """
        sections = []

        # Market context
        mc = context.market_context or {}
        sections.append("## Market Context")
        sections.append(f"- VIX: {mc.get('vix', 'UNKNOWN')}")
        if mc.get("vix_change_pct"):
            sections.append(f"- VIX Session Change: {mc['vix_change_pct']:.1%}")
        if mc.get("spy_price"):
            sections.append(f"- SPY: ${mc['spy_price']}")
        if mc.get("spy_change_pct"):
            sections.append(f"- SPY Change: {mc['spy_change_pct']:.1%}")
        sections.append(f"- Day: {mc.get('day_of_week', 'UNKNOWN')}")
        sections.append("")

        # Staged candidates (the trades being challenged)
        if context.staged_candidates:
            sections.append("## Staged Candidates (trades to be executed)")
            for i, candidate in enumerate(context.staged_candidates, 1):
                sections.append(f"\n### Candidate {i}: {candidate.get('symbol', '?')}")
                for key in [
                    "symbol", "strike", "expiration", "stock_price", "otm_pct",
                    "staged_limit_price", "staged_contracts", "staged_margin",
                    "delta", "iv", "sector", "earnings_date", "state",
                ]:
                    val = candidate.get(key)
                    if val is not None:
                        sections.append(f"- {key}: {val}")
            sections.append("")

        # Open positions (for concentration/correlation analysis)
        if context.open_positions:
            sections.append("## Current Open Positions")
            for pos in context.open_positions:
                symbol = pos.get("symbol", "?")
                pnl = pos.get("pnl_pct", pos.get("unrealized_pnl_pct", "?"))
                strike = pos.get("strike", "?")
                dte = pos.get("dte", "?")
                delta = pos.get("delta", "?")
                sections.append(
                    f"- {symbol} strike={strike} DTE={dte} delta={delta} P&L={pnl}"
                )
            sections.append("")

        # Account state
        sections.append("## Account State")
        ss = context.strategy_state or {}
        for key in [
            "account_balance", "margin_used", "margin_utilization_pct",
            "buying_power", "daily_pnl", "weekly_pnl", "positions_count",
            "max_positions",
        ]:
            val = ss.get(key)
            if val is not None:
                sections.append(f"- {key}: {val}")
        sections.append("")

        # Primary decision reasoning (so CRO can attack it specifically)
        if primary_reasoning:
            sections.append("## Primary Reasoning Engine's Decision")
            sections.append(
                "The following is the reasoning engine's rationale for executing. "
                "Your job is to challenge this reasoning:"
            )
            sections.append(f"\n> {primary_reasoning}")
            sections.append("")

        # Active patterns (potential blind spots)
        if context.active_patterns:
            sections.append("## Active Patterns (from learning engine)")
            for pattern in context.active_patterns[:5]:
                sections.append(
                    f"- {pattern.get('name', '?')}: {pattern.get('description', '?')}"
                )
            sections.append("")

        return "\n".join(sections)

    def _parse_response(self, content: str) -> CROAssessment:
        """Parse Claude's JSON response into a CROAssessment.

        Args:
            content: Raw response text from Claude

        Returns:
            Parsed CROAssessment
        """
        # Strip markdown code fences if present
        text = content.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```json) and last line (```)
            lines = [l for l in lines if not l.strip().startswith("```")]
            text = "\n".join(lines)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"CRO response not valid JSON, treating as error")
            return CROAssessment(
                overall_risk_level="UNKNOWN",
                should_escalate=False,
                error=f"Invalid JSON response: {content[:200]}",
            )

        objections = []
        for obj_data in data.get("objections", []):
            objections.append(
                CROObjection(
                    severity=obj_data.get("severity", "LOW"),
                    category=obj_data.get("category", "unknown"),
                    target=obj_data.get("target", "unknown"),
                    objection=obj_data.get("objection", ""),
                    evidence=obj_data.get("evidence", ""),
                )
            )

        return CROAssessment(
            overall_risk_level=data.get("overall_risk_level", "UNKNOWN"),
            should_escalate=data.get("should_escalate", False),
            objections=objections,
            strongest_objection=data.get("strongest_objection", ""),
            assessment_summary=data.get("assessment_summary", ""),
        )
