"""Data models for AI-powered performance analysis.

Defines dataclasses for structuring data sent to and received from
the Claude API for trading performance analysis.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AnalysisDepth(str, Enum):
    """Analysis depth controls model choice, pattern count, and dimensions."""

    QUICK = "quick"        # Haiku, top 5 patterns, 2 dimensions
    STANDARD = "standard"  # Sonnet, top 20 patterns, 4 dimensions
    DEEP = "deep"          # Opus, top 30 patterns, 8 dimensions


# Model mapping per depth level
DEPTH_MODELS = {
    AnalysisDepth.QUICK: "claude-haiku-4-5-20251001",
    AnalysisDepth.STANDARD: "claude-sonnet-4-5-20250929",
    AnalysisDepth.DEEP: "claude-opus-4-6",
}

DEPTH_PATTERN_LIMITS = {
    AnalysisDepth.QUICK: 5,
    AnalysisDepth.STANDARD: 20,
    AnalysisDepth.DEEP: 30,
}

DEPTH_DIMENSIONS = {
    AnalysisDepth.QUICK: ["sector", "delta_bucket"],
    AnalysisDepth.STANDARD: ["sector", "delta_bucket", "dte_bucket", "vix_regime"],
    AnalysisDepth.DEEP: [
        "sector", "delta_bucket", "dte_bucket", "vix_regime",
        "rsi_bucket", "trend_direction", "entry_day", "vol_regime",
    ],
}


@dataclass
class PerformanceSummary:
    """High-level performance metrics for the analysis period."""

    total_trades: int
    win_rate: float
    avg_roi: float
    total_pnl: float
    max_drawdown: float
    # Recent window comparison
    recent_trades: int = 0
    recent_win_rate: float = 0.0
    recent_avg_roi: float = 0.0


@dataclass
class PatternSummary:
    """Compressed representation of a detected pattern for the prompt."""

    pattern_type: str
    pattern_name: str
    pattern_value: str
    sample_size: int
    win_rate: float
    avg_roi: float
    p_value: float
    confidence: float
    direction: str = "outperforming"  # "outperforming" or "underperforming"


@dataclass
class DimensionalBreakdown:
    """Win rate and ROI bucketed by a single dimension."""

    dimension: str  # e.g. "sector", "delta_bucket"
    buckets: list[dict] = field(default_factory=list)
    # Each bucket: {"label": str, "trades": int, "win_rate": float, "avg_roi": float}


@dataclass
class ExperimentSummary:
    """Compressed experiment status for the prompt."""

    experiment_id: str
    name: str
    parameter: str
    control_value: str
    test_value: str
    status: str
    control_trades: int = 0
    test_trades: int = 0
    p_value: Optional[float] = None
    decision: Optional[str] = None


@dataclass
class ProposalSummary:
    """Compressed optimizer proposal for the prompt."""

    parameter: str
    current_value: str
    proposed_value: str
    expected_improvement: float
    confidence: float
    reasoning: str


@dataclass
class ConfigSnapshot:
    """Current strategy configuration snapshot."""

    parameters: dict = field(default_factory=dict)


@dataclass
class AnalysisContext:
    """Everything sent to Claude for analysis.

    Aggregated from the database, compressed to stay within token budgets.
    """

    performance: PerformanceSummary
    patterns: list[PatternSummary] = field(default_factory=list)
    breakdowns: list[DimensionalBreakdown] = field(default_factory=list)
    experiments: list[ExperimentSummary] = field(default_factory=list)
    proposals: list[ProposalSummary] = field(default_factory=list)
    recent_learning_events: list[dict] = field(default_factory=list)
    config: ConfigSnapshot = field(default_factory=ConfigSnapshot)
    analysis_period_days: int = 90
    depth: AnalysisDepth = AnalysisDepth.STANDARD
    user_question: Optional[str] = None


@dataclass
class AnalysisInsight:
    """A single insight from Claude's analysis."""

    category: str  # "recommendation", "risk", "hypothesis", "observation"
    title: str
    body: str
    confidence: str = "medium"  # "high", "medium", "low"
    priority: int = 5
    related_patterns: list[str] = field(default_factory=list)
    actionable: bool = False


@dataclass
class AnalysisReport:
    """Complete analysis response from Claude."""

    narrative: str
    insights: list[AnalysisInsight] = field(default_factory=list)
    timestamp: datetime = field(default_factory=datetime.now)
    depth: AnalysisDepth = AnalysisDepth.STANDARD
    model_used: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_estimate: float = 0.0

    @property
    def recommendations(self) -> list[AnalysisInsight]:
        return [i for i in self.insights if i.category == "recommendation"]

    @property
    def risks(self) -> list[AnalysisInsight]:
        return [i for i in self.insights if i.category == "risk"]

    @property
    def hypotheses(self) -> list[AnalysisInsight]:
        return [i for i in self.insights if i.category == "hypothesis"]
