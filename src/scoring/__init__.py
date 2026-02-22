"""Scoring engine for naked put candidates.

This module provides research-backed scoring and ranking of naked put
options candidates based on 6 key dimensions.
"""

from src.scoring.score_config import (
    DEFAULT_DIVERSIFICATION,
    DEFAULT_THRESHOLDS,
    DEFAULT_WEIGHTS,
    DiversificationRules,
    ScoreThresholds,
    ScoreWeights,
)
from src.scoring.scorer import NakedPutScorer, ScoredCandidate

__all__ = [
    "NakedPutScorer",
    "ScoredCandidate",
    "ScoreWeights",
    "ScoreThresholds",
    "DiversificationRules",
    "DEFAULT_WEIGHTS",
    "DEFAULT_THRESHOLDS",
    "DEFAULT_DIVERSIFICATION",
]
