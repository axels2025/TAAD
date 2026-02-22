"""AI-powered performance analyzer using Claude.

Sends aggregated trading data to Claude and parses back structured
insights, recommendations, risk warnings, and new hypotheses.
"""

import json
from typing import Optional

from loguru import logger

from src.agents.base_agent import BaseAgent
from src.agents.models import (
    AnalysisContext,
    AnalysisDepth,
    AnalysisInsight,
    AnalysisReport,
    DEPTH_MODELS,
)


SYSTEM_PROMPT = """\
You are an expert quantitative trading analyst specializing in options strategies, \
specifically naked put selling. You analyze trading performance data and provide \
actionable insights.

Your role:
1. SYNTHESIZE patterns across multiple dimensions (don't just restate statistics)
2. DETECT contradictions between patterns or proposals
3. REASON about causation vs correlation
4. GENERATE new hypotheses the statistical engine hasn't tested
5. IDENTIFY risks including concentration, correlation, and regime change
6. PRIORITIZE recommendations by impact and confidence

Guidelines:
- Be specific and quantitative. Reference actual numbers from the data.
- Distinguish correlation from causation. Question mechanisms.
- Flag conflicts between recommendations.
- Consider what could go wrong, not just what's working.
- Keep insights actionable — "consider X" is better than "X is interesting."

Respond with valid JSON matching this schema exactly:
{
  "narrative": "2-3 paragraph plain-English performance overview",
  "insights": [
    {
      "category": "recommendation|risk|hypothesis|observation",
      "title": "Short title (under 80 chars)",
      "body": "Detailed explanation with numbers",
      "confidence": "high|medium|low",
      "priority": 1,
      "related_patterns": ["pattern_name_1"],
      "actionable": true
    }
  ]
}

Return ONLY the JSON object. No markdown fences, no commentary outside the JSON.\
"""


class PerformanceAnalyzer:
    """Sends aggregated trading data to Claude for analysis.

    Builds a structured prompt from AnalysisContext, sends it to the
    appropriate Claude model based on depth, and parses the structured
    JSON response into an AnalysisReport.
    """

    def __init__(
        self,
        depth: AnalysisDepth = AnalysisDepth.STANDARD,
        api_key: Optional[str] = None,
    ):
        """Initialize the analyzer with a given depth.

        Args:
            depth: Analysis depth controlling model choice and detail level
            api_key: Optional API key override (uses Config if not provided)
        """
        self.depth = depth
        model = DEPTH_MODELS[depth]
        self.agent = BaseAgent(model=model, api_key=api_key)

    def analyze(self, context: AnalysisContext) -> AnalysisReport:
        """Run AI analysis on the provided context.

        Args:
            context: Aggregated trading data from DataAggregator

        Returns:
            AnalysisReport with narrative and structured insights
        """
        if context.performance.total_trades == 0:
            return AnalysisReport(
                narrative="No closed trades found in the analysis period. "
                "Start trading to generate performance data for analysis.",
                depth=self.depth,
                model_used=self.agent.model,
            )

        prompt = self._build_prompt(context)

        logger.info(
            f"Sending analysis request: depth={self.depth.value}, "
            f"model={self.agent.model}"
        )

        response = self.agent.send_message(
            system_prompt=SYSTEM_PROMPT,
            user_message=prompt,
            max_tokens=4096,
            temperature=0.3,
        )

        report = self._parse_response(response)
        report.depth = self.depth
        report.model_used = response["model"]
        report.input_tokens = response["input_tokens"]
        report.output_tokens = response["output_tokens"]
        report.cost_estimate = self.agent.estimate_cost(
            response["input_tokens"], response["output_tokens"]
        )

        logger.info(
            f"Analysis complete: {len(report.insights)} insights, "
            f"cost=${report.cost_estimate:.4f}"
        )

        return report

    def _build_prompt(self, context: AnalysisContext) -> str:
        """Build the user message from AnalysisContext.

        Compresses all data into a structured text prompt that stays
        within token budgets.

        Args:
            context: The analysis context

        Returns:
            Formatted prompt string
        """
        sections = []

        # User question (if any)
        if context.user_question:
            sections.append(
                f"## USER QUESTION\n{context.user_question}\n"
                "Please address this question specifically in your analysis."
            )

        # Performance summary
        perf = context.performance
        sections.append(
            f"## PERFORMANCE SUMMARY ({context.analysis_period_days} days)\n"
            f"Total closed trades: {perf.total_trades} | "
            f"Win rate: {perf.win_rate:.1%} | "
            f"Avg ROI: {perf.avg_roi:.2%}\n"
            f"Total P&L: ${perf.total_pnl:,.2f} | "
            f"Max drawdown: ${perf.max_drawdown:,.2f}\n"
            f"Last 30d: {perf.recent_trades} trades, "
            f"{perf.recent_win_rate:.1%} win, {perf.recent_avg_roi:.2%} ROI"
        )

        # Patterns
        if context.patterns:
            outperforming = [p for p in context.patterns if p.direction == "outperforming"]
            underperforming = [p for p in context.patterns if p.direction == "underperforming"]

            lines = ["## VALIDATED PATTERNS"]
            if outperforming:
                lines.append("Outperforming:")
                for p in outperforming:
                    lines.append(
                        f"  {p.pattern_name}: {p.sample_size} trades, "
                        f"{p.win_rate:.0%} win, {p.avg_roi:.2%} ROI, "
                        f"p={p.p_value:.3f}, conf={p.confidence:.2f}"
                    )
            if underperforming:
                lines.append("Underperforming:")
                for p in underperforming:
                    lines.append(
                        f"  {p.pattern_name}: {p.sample_size} trades, "
                        f"{p.win_rate:.0%} win, {p.avg_roi:.2%} ROI, "
                        f"p={p.p_value:.3f}"
                    )
            sections.append("\n".join(lines))

        # Dimensional breakdowns
        if context.breakdowns:
            lines = ["## DIMENSIONAL BREAKDOWNS"]
            for bd in context.breakdowns:
                lines.append(f"\n### {bd.dimension.replace('_', ' ').title()}")
                for bucket in bd.buckets:
                    lines.append(
                        f"  {bucket['label']}: {bucket['trades']} trades, "
                        f"{bucket['win_rate']:.0%} win, {bucket['avg_roi']:.2%} ROI"
                    )
            sections.append("\n".join(lines))

        # Experiments
        if context.experiments:
            lines = ["## EXPERIMENTS"]
            for exp in context.experiments:
                status_str = exp.status
                if exp.p_value is not None:
                    status_str += f" (p={exp.p_value:.3f})"
                if exp.decision:
                    status_str += f" -> {exp.decision}"
                lines.append(
                    f"  {exp.name}: {exp.parameter} "
                    f"[{exp.control_value} vs {exp.test_value}] "
                    f"control={exp.control_trades}, test={exp.test_trades} "
                    f"| {status_str}"
                )
            sections.append("\n".join(lines))

        # Proposals
        if context.proposals:
            lines = ["## OPTIMIZER PROPOSALS"]
            for prop in context.proposals:
                lines.append(
                    f"  {prop.parameter}: {prop.current_value} -> {prop.proposed_value} "
                    f"(expected +{prop.expected_improvement:.2%}, "
                    f"conf={prop.confidence:.2f})\n"
                    f"    Reason: {prop.reasoning}"
                )
            sections.append("\n".join(lines))

        # Recent learning events
        if context.recent_learning_events:
            lines = ["## RECENT LEARNING EVENTS"]
            for event in context.recent_learning_events[:5]:
                lines.append(
                    f"  [{event['date'][:10]}] {event['type']}: "
                    f"{event['pattern'] or event['parameter']} "
                    f"{event['change']}"
                )
            sections.append("\n".join(lines))

        # Config snapshot
        if context.config.parameters:
            lines = ["## CURRENT STRATEGY CONFIG"]
            for key, val in context.config.parameters.items():
                lines.append(f"  {key}: {val}")
            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    def _parse_response(self, response: dict) -> AnalysisReport:
        """Parse Claude's JSON response into an AnalysisReport.

        Handles malformed responses gracefully by falling back to
        raw text as the narrative.

        Args:
            response: Raw response dict from BaseAgent.send_message

        Returns:
            AnalysisReport with parsed insights
        """
        content = response["content"].strip()

        # Strip markdown fences if Claude added them despite instructions
        if content.startswith("```"):
            # Remove opening fence (```json or ```)
            first_newline = content.index("\n")
            content = content[first_newline + 1:]
            # Remove closing fence
            if content.endswith("```"):
                content = content[:-3].strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse Claude response as JSON: {e}")
            return AnalysisReport(
                narrative=content,
                insights=[],
            )

        narrative = data.get("narrative", "")
        raw_insights = data.get("insights", [])

        insights = []
        for raw in raw_insights:
            try:
                insights.append(AnalysisInsight(
                    category=raw.get("category", "observation"),
                    title=raw.get("title", ""),
                    body=raw.get("body", ""),
                    confidence=raw.get("confidence", "medium"),
                    priority=raw.get("priority", 5),
                    related_patterns=raw.get("related_patterns", []),
                    actionable=raw.get("actionable", False),
                ))
            except (KeyError, TypeError) as e:
                logger.warning(f"Skipping malformed insight: {e}")
                continue

        # Sort by priority
        insights.sort(key=lambda i: i.priority)

        return AnalysisReport(
            narrative=narrative,
            insights=insights,
        )
