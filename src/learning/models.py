"""Data models for the learning engine.

Defines dataclasses used across learning components for pattern detection,
validation, experiments, and parameter optimization.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional


@dataclass
class DetectedPattern:
    """A pattern detected from trade analysis.

    Represents a statistically significant finding about what works
    or doesn't work in the trading strategy.
    """

    # Pattern identification
    pattern_type: str  # 'delta_bucket', 'vix_regime', 'sector', etc.
    pattern_name: str  # 'delta_15_20_outperforms'
    pattern_value: str  # '15-20%'

    # Statistics
    sample_size: int
    win_rate: float  # 0.0-1.0
    avg_roi: float  # Average return on investment
    baseline_win_rate: float  # Overall win rate for comparison
    baseline_roi: float  # Overall ROI for comparison

    # Statistical significance
    p_value: float  # From t-test or similar
    effect_size: float  # Cohen's d or similar measure
    confidence: float  # 0.0-1.0

    # Metadata
    date_detected: datetime
    market_regime: Optional[str] = None

    def is_significant(self, min_samples: int = 30, max_p: float = 0.05, min_effect: float = 0.5) -> bool:
        """Check if pattern meets significance criteria."""
        return (
            self.sample_size >= min_samples
            and self.p_value < max_p
            and abs(self.effect_size) > min_effect
        )

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "pattern_type": self.pattern_type,
            "pattern_name": self.pattern_name,
            "pattern_value": self.pattern_value,
            "sample_size": self.sample_size,
            "win_rate": self.win_rate,
            "avg_roi": self.avg_roi,
            "p_value": self.p_value,
            "confidence": self.confidence,
            "date_detected": self.date_detected,
            "market_regime": self.market_regime,
        }


@dataclass
class ValidationResult:
    """Result of statistical validation."""

    valid: bool
    reason: str = ""
    p_value: Optional[float] = None
    effect_size: Optional[float] = None
    cv_score: Optional[float] = None  # Cross-validation score
    confidence: Optional[float] = None

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "valid": self.valid,
            "reason": self.reason,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "cv_score": self.cv_score,
            "confidence": self.confidence,
        }


@dataclass
class ExperimentResult:
    """Result of experiment evaluation."""

    decision: str  # 'ADOPT', 'REJECT', 'INSUFFICIENT_DATA', 'CONTINUE'
    p_value: Optional[float] = None
    effect_size: Optional[float] = None
    control_roi: Optional[float] = None
    test_roi: Optional[float] = None
    recommendation: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "decision": self.decision,
            "p_value": self.p_value,
            "effect_size": self.effect_size,
            "control_roi": self.control_roi,
            "test_roi": self.test_roi,
            "recommendation": self.recommendation,
            "reason": self.reason,
        }


@dataclass
class ParameterProposal:
    """Proposed parameter change based on patterns."""

    parameter: str
    current_value: Any
    proposed_value: Any
    expected_improvement: float
    confidence: float
    source_pattern: DetectedPattern
    reasoning: str = ""

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "parameter": self.parameter,
            "current_value": str(self.current_value),
            "proposed_value": str(self.proposed_value),
            "expected_improvement": self.expected_improvement,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "source_pattern": self.source_pattern.pattern_name,
        }


@dataclass
class ConfigChange:
    """Record of a configuration parameter change."""

    timestamp: datetime
    parameter: str
    old_value: Any
    new_value: Any
    reason: str
    approval_type: str  # 'auto', 'manual', 'experiment'

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "parameter": self.parameter,
            "old_value": str(self.old_value),
            "new_value": str(self.new_value),
            "reason": self.reason,
            "approval_type": self.approval_type,
        }


@dataclass
class LearningReport:
    """Weekly learning cycle report."""

    timestamp: datetime
    patterns_detected: int = 0
    patterns_validated: int = 0
    experiments_adopted: list = field(default_factory=list)
    experiments_rejected: list = field(default_factory=list)
    proposals: list[ParameterProposal] = field(default_factory=list)
    changes_applied: list[ParameterProposal] = field(default_factory=list)
    total_trades_analyzed: int = 0
    baseline_win_rate: float = 0.0
    baseline_avg_roi: float = 0.0

    def to_dict(self) -> dict:
        """Convert to dictionary."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "patterns_detected": self.patterns_detected,
            "patterns_validated": self.patterns_validated,
            "experiments_adopted": len(self.experiments_adopted),
            "experiments_rejected": len(self.experiments_rejected),
            "proposals": len(self.proposals),
            "changes_applied": len(self.changes_applied),
            "total_trades_analyzed": self.total_trades_analyzed,
            "baseline_win_rate": self.baseline_win_rate,
            "baseline_avg_roi": self.baseline_avg_roi,
        }
