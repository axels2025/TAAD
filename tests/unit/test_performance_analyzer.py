"""Tests for the performance analyzer.

Tests prompt construction and response parsing with mocked Claude API
to avoid external dependencies in unit tests.
"""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agents.models import (
    AnalysisContext,
    AnalysisDepth,
    AnalysisInsight,
    AnalysisReport,
    ConfigSnapshot,
    DimensionalBreakdown,
    ExperimentSummary,
    PatternSummary,
    PerformanceSummary,
    ProposalSummary,
)
from src.agents.performance_analyzer import PerformanceAnalyzer, SYSTEM_PROMPT


def _make_context(**overrides) -> AnalysisContext:
    """Create a test AnalysisContext with reasonable defaults."""
    defaults = dict(
        performance=PerformanceSummary(
            total_trades=127,
            win_rate=0.74,
            avg_roi=0.028,
            total_pnl=8420.0,
            max_drawdown=-1240.0,
            recent_trades=30,
            recent_win_rate=0.78,
            recent_avg_roi=0.031,
        ),
        patterns=[
            PatternSummary(
                pattern_type="delta_bucket",
                pattern_name="delta_15_20_outperforms",
                pattern_value="15-20%",
                sample_size=38,
                win_rate=0.82,
                avg_roi=0.038,
                p_value=0.012,
                confidence=0.91,
                direction="outperforming",
            ),
            PatternSummary(
                pattern_type="sector",
                pattern_name="sector_energy_underperforms",
                pattern_value="Energy",
                sample_size=12,
                win_rate=0.58,
                avg_roi=0.012,
                p_value=0.031,
                confidence=0.78,
                direction="underperforming",
            ),
        ],
        breakdowns=[
            DimensionalBreakdown(
                dimension="sector",
                buckets=[
                    {"label": "Technology", "trades": 42, "win_rate": 0.79, "avg_roi": 0.032},
                    {"label": "Consumer", "trades": 25, "win_rate": 0.72, "avg_roi": 0.025},
                    {"label": "Energy", "trades": 12, "win_rate": 0.58, "avg_roi": 0.012},
                ],
            ),
        ],
        experiments=[
            ExperimentSummary(
                experiment_id="EXP-001",
                name="Test wider delta",
                parameter="delta_range",
                control_value="(0.10, 0.20)",
                test_value="(0.10, 0.25)",
                status="active",
                control_trades=18,
                test_trades=14,
            ),
        ],
        proposals=[
            ProposalSummary(
                parameter="delta_range",
                current_value="(0.10, 0.25)",
                proposed_value="(0.15, 0.20)",
                expected_improvement=0.01,
                confidence=0.91,
                reasoning="15-20% delta shows 3.8% ROI vs 2.8% baseline",
            ),
        ],
        recent_learning_events=[
            {
                "type": "pattern_detected",
                "date": "2026-02-10",
                "pattern": "delta_15_20_outperforms",
                "parameter": "",
                "change": "",
                "reasoning": "38 trades, 82% win rate",
            },
        ],
        config=ConfigSnapshot(parameters={
            "paper_trading": True,
            "max_positions": 10,
            "premium_min": 0.30,
        }),
        analysis_period_days=90,
        depth=AnalysisDepth.STANDARD,
    )
    defaults.update(overrides)
    return AnalysisContext(**defaults)


def _make_claude_response(narrative: str = "", insights: list = None) -> dict:
    """Create a mock Claude API response dict."""
    if insights is None:
        insights = [
            {
                "category": "recommendation",
                "title": "Narrow delta range to 15-20%",
                "body": "Your 15-20% delta bucket shows 82% win rate vs 74% baseline.",
                "confidence": "high",
                "priority": 1,
                "related_patterns": ["delta_15_20_outperforms"],
                "actionable": True,
            },
            {
                "category": "risk",
                "title": "Technology concentration risk",
                "body": "42 of 127 trades (33%) are in Technology.",
                "confidence": "medium",
                "priority": 2,
                "related_patterns": ["sector_technology"],
                "actionable": True,
            },
        ]

    content = json.dumps({
        "narrative": narrative or "Your trading performance has been solid.",
        "insights": insights,
    })

    return {
        "content": content,
        "input_tokens": 1500,
        "output_tokens": 500,
        "model": "claude-sonnet-4-5-20250929",
    }


class TestPromptBuilding:
    """Tests for prompt construction."""

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_prompt_contains_performance_section(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer(depth=AnalysisDepth.STANDARD)
        context = _make_context()
        analyzer.analyze(context)

        call_args = mock_agent.send_message.call_args
        prompt = call_args.kwargs.get("user_message") or call_args[1].get("user_message") or call_args[0][1]

        assert "PERFORMANCE SUMMARY" in prompt
        assert "127" in prompt  # total trades
        assert "74" in prompt   # win rate ~74%
        assert "8,420" in prompt  # total P&L

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_prompt_contains_patterns(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        context = _make_context()
        analyzer.analyze(context)

        call_args = mock_agent.send_message.call_args
        prompt = call_args.kwargs.get("user_message") or call_args[1].get("user_message") or call_args[0][1]

        assert "VALIDATED PATTERNS" in prompt
        assert "delta_15_20_outperforms" in prompt
        assert "Outperforming" in prompt
        assert "Underperforming" in prompt

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_prompt_contains_user_question(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        context = _make_context(user_question="Why are Energy trades bad?")
        analyzer.analyze(context)

        call_args = mock_agent.send_message.call_args
        prompt = call_args.kwargs.get("user_message") or call_args[1].get("user_message") or call_args[0][1]

        assert "USER QUESTION" in prompt
        assert "Why are Energy trades bad?" in prompt

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_prompt_contains_breakdowns(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        context = _make_context()
        analyzer.analyze(context)

        call_args = mock_agent.send_message.call_args
        prompt = call_args.kwargs.get("user_message") or call_args[1].get("user_message") or call_args[0][1]

        assert "DIMENSIONAL BREAKDOWNS" in prompt
        assert "Technology" in prompt
        assert "42 trades" in prompt

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_prompt_contains_experiments(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        context = _make_context()
        analyzer.analyze(context)

        call_args = mock_agent.send_message.call_args
        prompt = call_args.kwargs.get("user_message") or call_args[1].get("user_message") or call_args[0][1]

        assert "EXPERIMENTS" in prompt
        assert "EXP-001" not in prompt  # experiment_id isn't in the prompt, but name is
        assert "Test wider delta" in prompt


class TestResponseParsing:
    """Tests for parsing Claude's JSON response."""

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_valid_json_parsed(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response(
            narrative="Performance is strong."
        )
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(_make_context())

        assert report.narrative == "Performance is strong."
        assert len(report.insights) == 2
        assert report.insights[0].category == "recommendation"
        assert report.insights[0].priority == 1
        assert report.insights[1].category == "risk"

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_insights_sorted_by_priority(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response(
            insights=[
                {"category": "risk", "title": "Low priority", "body": "...", "priority": 5},
                {"category": "recommendation", "title": "High priority", "body": "...", "priority": 1},
            ]
        )
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(_make_context())

        assert report.insights[0].priority == 1
        assert report.insights[1].priority == 5

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_markdown_fences_stripped(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"

        fenced_content = '```json\n{"narrative": "test", "insights": []}\n```'
        mock_agent.send_message.return_value = {
            "content": fenced_content,
            "input_tokens": 100,
            "output_tokens": 50,
            "model": "claude-sonnet-4-5-20250929",
        }
        mock_agent.estimate_cost.return_value = 0.001
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(_make_context())

        assert report.narrative == "test"
        assert report.insights == []

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_malformed_json_fallback(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = {
            "content": "This is not JSON at all",
            "input_tokens": 100,
            "output_tokens": 50,
            "model": "claude-sonnet-4-5-20250929",
        }
        mock_agent.estimate_cost.return_value = 0.001
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(_make_context())

        # Falls back to raw content as narrative
        assert report.narrative == "This is not JSON at all"
        assert report.insights == []

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_cost_tracking(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(_make_context())

        assert report.input_tokens == 1500
        assert report.output_tokens == 500
        assert report.cost_estimate == 0.03
        assert report.model_used == "claude-sonnet-4-5-20250929"


class TestReportProperties:
    """Tests for AnalysisReport convenience properties."""

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_category_filters(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response(
            insights=[
                {"category": "recommendation", "title": "R1", "body": "..."},
                {"category": "risk", "title": "K1", "body": "..."},
                {"category": "hypothesis", "title": "H1", "body": "..."},
                {"category": "recommendation", "title": "R2", "body": "..."},
            ]
        )
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        report = analyzer.analyze(_make_context())

        assert len(report.recommendations) == 2
        assert len(report.risks) == 1
        assert len(report.hypotheses) == 1


class TestEmptyData:
    """Tests for edge cases with no data."""

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_no_trades_returns_empty_report(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer()
        context = _make_context(
            performance=PerformanceSummary(
                total_trades=0,
                win_rate=0.0,
                avg_roi=0.0,
                total_pnl=0.0,
                max_drawdown=0.0,
            ),
        )
        report = analyzer.analyze(context)

        assert "No closed trades" in report.narrative
        # Should NOT have called Claude API
        mock_agent.send_message.assert_not_called()


class TestDepthSelection:
    """Tests for depth-based model selection."""

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_quick_uses_haiku(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-haiku-4-5-20251001"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.001
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer(depth=AnalysisDepth.QUICK)

        MockAgent.assert_called_once_with(model="claude-haiku-4-5-20251001", api_key=None)

    @patch("src.agents.performance_analyzer.BaseAgent")
    def test_standard_uses_sonnet(self, MockAgent):
        mock_agent = MagicMock()
        mock_agent.model = "claude-sonnet-4-5-20250929"
        mock_agent.send_message.return_value = _make_claude_response()
        mock_agent.estimate_cost.return_value = 0.03
        MockAgent.return_value = mock_agent

        analyzer = PerformanceAnalyzer(depth=AnalysisDepth.STANDARD)

        MockAgent.assert_called_once_with(model="claude-sonnet-4-5-20250929", api_key=None)
