"""Learning Engine for self-improving trading strategy.

This module provides components for analyzing trade outcomes, detecting
profitable patterns, running A/B experiments, and optimizing strategy
parameters based on statistical evidence.

Main Components:
- PatternDetector: Identifies profitable patterns across multiple dimensions
- StatisticalValidator: Validates patterns using rigorous statistical tests
- ExperimentEngine: Manages A/B experiments for testing improvements
- ParameterOptimizer: Proposes and tracks parameter changes
- LearningOrchestrator: Coordinates the weekly learning cycle

Usage:
    from src.learning import LearningOrchestrator
    from src.data.database import get_db_session

    with get_db_session() as db:
        orchestrator = LearningOrchestrator(db)
        report = orchestrator.run_weekly_analysis()
        print(f"Patterns detected: {report.patterns_detected}")
"""

from src.learning.experiment_engine import ExperimentEngine
from src.learning.learning_orchestrator import LearningOrchestrator
from src.learning.models import (
    ConfigChange,
    DetectedPattern,
    ExperimentResult,
    LearningReport,
    ParameterProposal,
    ValidationResult,
)
from src.learning.parameter_optimizer import ParameterOptimizer
from src.learning.pattern_detector import PatternDetector
from src.learning.statistical_validator import StatisticalValidator

__all__ = [
    # Main orchestrator
    "LearningOrchestrator",
    # Core components
    "PatternDetector",
    "StatisticalValidator",
    "ExperimentEngine",
    "ParameterOptimizer",
    # Data models
    "DetectedPattern",
    "ValidationResult",
    "ExperimentResult",
    "ParameterProposal",
    "ConfigChange",
    "LearningReport",
]
