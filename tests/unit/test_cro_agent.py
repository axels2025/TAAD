"""Tests for the CRO (Chief Risk Officer) adversarial agent."""

import json
from dataclasses import field
from unittest.mock import MagicMock, patch

import pytest

from src.agents.cro_agent import (
    CROAgent,
    CROAssessment,
    CROObjection,
    CRO_SYSTEM_PROMPT,
)
from src.agentic.working_memory import ReasoningContext


@pytest.fixture
def cro_agent():
    """Create a CRO agent with mocked API."""
    with patch("src.agents.cro_agent.BaseAgent") as mock_cls:
        agent = CROAgent(model="claude-sonnet-4-5-20250929")
        yield agent, mock_cls.return_value


@pytest.fixture
def sample_context():
    """Create a ReasoningContext with staged candidates."""
    ctx = ReasoningContext()
    ctx.market_context = {
        "vix": 22.5,
        "vix_change_pct": 0.03,
        "spy_price": 545.0,
        "spy_change_pct": -0.005,
        "day_of_week": "Monday",
    }
    ctx.staged_candidates = [
        {
            "symbol": "NVDA",
            "strike": 115.0,
            "expiration": "2026-03-20",
            "stock_price": 132.5,
            "otm_pct": 13.2,
            "staged_limit_price": 0.85,
            "staged_contracts": 2,
            "staged_margin": 4500.0,
            "delta": -0.08,
            "iv": 0.45,
            "sector": "Technology",
            "state": "STAGED",
        },
        {
            "symbol": "TSLA",
            "strike": 230.0,
            "expiration": "2026-03-20",
            "stock_price": 265.0,
            "otm_pct": 13.2,
            "staged_limit_price": 1.20,
            "staged_contracts": 1,
            "staged_margin": 5000.0,
            "delta": -0.10,
            "iv": 0.62,
            "sector": "Consumer Discretionary",
            "state": "STAGED",
        },
    ]
    ctx.open_positions = [
        {
            "symbol": "AAPL",
            "strike": 180.0,
            "dte": 5,
            "delta": -0.06,
            "pnl_pct": 0.35,
        },
    ]
    ctx.strategy_state = {
        "account_balance": 50000.0,
        "margin_utilization_pct": 0.35,
        "positions_count": 1,
        "max_positions": 10,
    }
    return ctx


class TestCROAssessment:
    """Tests for CROAssessment dataclass."""

    def test_has_critical_with_critical_objection(self):
        assessment = CROAssessment(
            overall_risk_level="CRITICAL",
            should_escalate=True,
            objections=[
                CROObjection("CRITICAL", "single_stock", "NVDA", "earnings", "data"),
            ],
        )
        assert assessment.has_critical is True
        assert assessment.has_high is True

    def test_has_critical_without_critical(self):
        assessment = CROAssessment(
            overall_risk_level="MODERATE",
            should_escalate=False,
            objections=[
                CROObjection("MODERATE", "market_regime", "portfolio", "concern", ""),
            ],
        )
        assert assessment.has_critical is False
        assert assessment.has_high is False

    def test_has_high_with_high_objection(self):
        assessment = CROAssessment(
            overall_risk_level="HIGH",
            should_escalate=True,
            objections=[
                CROObjection("HIGH", "concentration", "NVDA", "issue", ""),
                CROObjection("LOW", "position_level", "TSLA", "minor", ""),
            ],
        )
        assert assessment.has_critical is False
        assert assessment.has_high is True

    def test_to_dict_serialization(self):
        assessment = CROAssessment(
            overall_risk_level="MODERATE",
            should_escalate=False,
            objections=[
                CROObjection("LOW", "market_regime", "portfolio", "concern", "VIX=22"),
            ],
            strongest_objection="VIX elevated",
            assessment_summary="Minor concerns only.",
            input_tokens=500,
            output_tokens=200,
            cost_usd=0.0025,
        )
        d = assessment.to_dict()
        assert d["overall_risk_level"] == "MODERATE"
        assert d["should_escalate"] is False
        assert len(d["objections"]) == 1
        assert d["objections"][0]["severity"] == "LOW"
        assert d["cost_usd"] == 0.0025

    def test_empty_assessment(self):
        assessment = CROAssessment(
            overall_risk_level="LOW",
            should_escalate=False,
        )
        assert assessment.has_critical is False
        assert assessment.has_high is False
        assert len(assessment.objections) == 0


class TestCROAgentParsing:
    """Tests for CRO response parsing."""

    def test_parse_valid_json_response(self, cro_agent):
        agent, mock_base = cro_agent
        response_json = {
            "overall_risk_level": "HIGH",
            "should_escalate": True,
            "objections": [
                {
                    "severity": "HIGH",
                    "category": "single_stock",
                    "target": "NVDA",
                    "objection": "Earnings in 3 days within DTE window",
                    "evidence": "Earnings date 2026-03-20 matches expiration",
                },
                {
                    "severity": "LOW",
                    "category": "market_regime",
                    "target": "portfolio",
                    "objection": "VIX slightly elevated but manageable",
                    "evidence": "VIX=22.5, within normal range",
                },
            ],
            "strongest_objection": "NVDA earnings within DTE window creates binary event risk",
            "assessment_summary": "NVDA trade carries significant earnings risk. TSLA trade acceptable.",
        }

        assessment = agent._parse_response(json.dumps(response_json))
        assert assessment.overall_risk_level == "HIGH"
        assert assessment.should_escalate is True
        assert len(assessment.objections) == 2
        assert assessment.objections[0].severity == "HIGH"
        assert assessment.objections[0].target == "NVDA"
        assert "NVDA" in assessment.strongest_objection

    def test_parse_json_with_code_fences(self, cro_agent):
        agent, _ = cro_agent
        response = '```json\n{"overall_risk_level": "LOW", "should_escalate": false, "objections": [], "strongest_objection": "", "assessment_summary": "No concerns."}\n```'
        assessment = agent._parse_response(response)
        assert assessment.overall_risk_level == "LOW"
        assert assessment.should_escalate is False

    def test_parse_invalid_json(self, cro_agent):
        agent, _ = cro_agent
        assessment = agent._parse_response("This is not JSON at all")
        assert assessment.overall_risk_level == "UNKNOWN"
        assert assessment.error is not None

    def test_parse_missing_fields_uses_defaults(self, cro_agent):
        agent, _ = cro_agent
        assessment = agent._parse_response('{"overall_risk_level": "MODERATE"}')
        assert assessment.overall_risk_level == "MODERATE"
        assert assessment.should_escalate is False
        assert len(assessment.objections) == 0


class TestCROAgentReview:
    """Tests for the full review workflow."""

    def test_review_returns_assessment(self, cro_agent, sample_context):
        agent, mock_base = cro_agent
        mock_base.send_message.return_value = {
            "content": json.dumps({
                "overall_risk_level": "MODERATE",
                "should_escalate": False,
                "objections": [
                    {
                        "severity": "MODERATE",
                        "category": "single_stock",
                        "target": "TSLA",
                        "objection": "High IV suggests event pricing",
                        "evidence": "IV=0.62 is elevated vs historical",
                    },
                ],
                "strongest_objection": "TSLA IV elevated suggesting event risk",
                "assessment_summary": "TSLA has elevated IV. NVDA looks clean.",
            }),
            "input_tokens": 1200,
            "output_tokens": 300,
            "model": "claude-sonnet-4-5-20250929",
        }
        mock_base.estimate_cost.return_value = 0.008

        assessment = agent.review(sample_context, primary_decision_reasoning="Stage normally")
        assert assessment.overall_risk_level == "MODERATE"
        assert assessment.should_escalate is False
        assert len(assessment.objections) == 1
        assert assessment.input_tokens == 1200
        assert assessment.output_tokens == 300

    def test_review_handles_api_failure(self, cro_agent, sample_context):
        agent, mock_base = cro_agent
        mock_base.send_message.side_effect = Exception("API timeout")

        assessment = agent.review(sample_context)
        assert assessment.overall_risk_level == "UNKNOWN"
        assert assessment.error is not None
        assert "API timeout" in assessment.error

    def test_review_prompt_includes_candidates(self, cro_agent, sample_context):
        agent, mock_base = cro_agent
        mock_base.send_message.return_value = {
            "content": '{"overall_risk_level": "LOW", "should_escalate": false, "objections": [], "strongest_objection": "", "assessment_summary": "Clean."}',
            "input_tokens": 800,
            "output_tokens": 100,
            "model": "test",
        }
        mock_base.estimate_cost.return_value = 0.003

        agent.review(sample_context, primary_decision_reasoning="Execute trades")

        # Verify the prompt was built with candidate data
        call_args = mock_base.send_message.call_args
        user_msg = call_args.kwargs.get("user_message") or call_args[1].get("user_message") or call_args[0][1]
        assert "NVDA" in user_msg
        assert "TSLA" in user_msg
        assert "115.0" in user_msg  # NVDA strike
        assert "Execute trades" in user_msg  # primary reasoning included

    def test_review_prompt_includes_open_positions(self, cro_agent, sample_context):
        agent, mock_base = cro_agent
        mock_base.send_message.return_value = {
            "content": '{"overall_risk_level": "LOW", "should_escalate": false, "objections": [], "strongest_objection": "", "assessment_summary": "Clean."}',
            "input_tokens": 800,
            "output_tokens": 100,
            "model": "test",
        }
        mock_base.estimate_cost.return_value = 0.003

        agent.review(sample_context)

        call_args = mock_base.send_message.call_args
        user_msg = call_args.kwargs.get("user_message") or call_args[1].get("user_message") or call_args[0][1]
        assert "AAPL" in user_msg
        assert "Current Open Positions" in user_msg

    def test_system_prompt_is_adversarial(self):
        """Verify the system prompt establishes adversarial tone."""
        assert "devil's advocate" in CRO_SYSTEM_PROMPT.lower()
        assert "NOT trying to be balanced" in CRO_SYSTEM_PROMPT
        assert "reasons NOT to execute" in CRO_SYSTEM_PROMPT


class TestCROConfig:
    """Tests for CRO configuration."""

    def test_default_config_values(self):
        from src.agentic.config import CROConfig

        config = CROConfig()
        assert config.enabled is True
        assert config.model == "claude-sonnet-4-5-20250929"
        assert config.escalate_on_high is True
        assert config.timeout == 45.0
        assert config.max_retries == 2

    def test_config_loads_from_phase5(self):
        from src.agentic.config import Phase5Config
        from src.agentic.guardrails.config import GuardrailConfig

        Phase5Config.model_rebuild(_types_namespace={"GuardrailConfig": GuardrailConfig})
        config = Phase5Config(cro={"enabled": False, "model": "claude-haiku-4-5-20251001"})
        assert config.cro.enabled is False
        assert config.cro.model == "claude-haiku-4-5-20251001"
