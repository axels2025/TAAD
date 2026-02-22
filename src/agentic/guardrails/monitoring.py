"""Post-decision monitoring: confidence calibration and reasoning entropy.

Pure analytics — zero runtime cost. Tracks whether Claude's confidence
predictions match actual outcomes, and detects reasoning stagnation.

Confidence calibration: buckets decisions by confidence, compares predicted
vs actual accuracy per bucket. Calibration error > threshold -> warning.

Reasoning entropy: tracks Jaccard similarity between consecutive reasoning
strings. High similarity for N+ consecutive -> flag stagnation.
"""

from collections import deque
from datetime import datetime, date

from loguru import logger
from sqlalchemy.orm import Session
from sqlalchemy import func as sa_func

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.registry import GuardrailResult


class ConfidenceCalibrator:
    """Tracks confidence calibration: predicted vs actual accuracy.

    On trade close, records whether the originating decision's confidence
    predicted the outcome correctly. Periodically computes calibration
    error across confidence buckets.
    """

    # Confidence bucket boundaries: [0, 0.3), [0.3, 0.5), [0.5, 0.7), [0.7, 0.85), [0.85, 1.0]
    BUCKET_EDGES = [0.0, 0.3, 0.5, 0.7, 0.85, 1.01]

    def __init__(self):
        """Initialize calibrator with empty outcome tracking."""
        # List of (confidence, was_correct) tuples
        self._outcomes: list[tuple[float, bool]] = []

    def record_outcome(self, confidence: float, was_correct: bool) -> None:
        """Record whether a decision at a given confidence was correct.

        Args:
            confidence: The confidence from the originating decision
            was_correct: Whether the trade was profitable / decision was right
        """
        self._outcomes.append((confidence, was_correct))

    def compute_calibration(self) -> dict:
        """Compute calibration error across confidence buckets.

        Returns:
            Dict with buckets, per-bucket stats, and overall calibration error
        """
        if not self._outcomes:
            return {
                "buckets": [],
                "calibration_error": 0.0,
                "sample_size": 0,
            }

        buckets = []
        total_error = 0.0
        total_weight = 0

        for i in range(len(self.BUCKET_EDGES) - 1):
            low = self.BUCKET_EDGES[i]
            high = self.BUCKET_EDGES[i + 1]

            bucket_outcomes = [
                (conf, correct)
                for conf, correct in self._outcomes
                if low <= conf < high
            ]

            if not bucket_outcomes:
                continue

            predicted_accuracy = sum(conf for conf, _ in bucket_outcomes) / len(bucket_outcomes)
            actual_accuracy = sum(1 for _, correct in bucket_outcomes if correct) / len(bucket_outcomes)
            bucket_error = abs(predicted_accuracy - actual_accuracy)

            bucket_data = {
                "range": f"{low:.2f}-{high:.2f}",
                "sample_size": len(bucket_outcomes),
                "predicted_accuracy": round(predicted_accuracy, 3),
                "actual_accuracy": round(actual_accuracy, 3),
                "calibration_error": round(bucket_error, 3),
            }
            buckets.append(bucket_data)

            total_error += bucket_error * len(bucket_outcomes)
            total_weight += len(bucket_outcomes)

        overall_error = total_error / total_weight if total_weight > 0 else 0.0

        return {
            "buckets": buckets,
            "calibration_error": round(overall_error, 3),
            "sample_size": len(self._outcomes),
        }

    def check_calibration(self, config: GuardrailConfig) -> GuardrailResult:
        """Check if calibration error exceeds threshold.

        Args:
            config: Guardrail configuration

        Returns:
            GuardrailResult
        """
        cal = self.compute_calibration()

        if cal["sample_size"] < 10:
            return GuardrailResult(
                passed=True,
                guard_name="confidence_calibration",
                severity="info",
                reason=f"Insufficient samples ({cal['sample_size']}) for calibration check",
            )

        if cal["calibration_error"] > config.calibration_error_threshold:
            return GuardrailResult(
                passed=False,
                guard_name="confidence_calibration",
                severity="warning",
                reason=(
                    f"Calibration error {cal['calibration_error']:.3f} exceeds threshold "
                    f"{config.calibration_error_threshold:.3f} "
                    f"(sample_size={cal['sample_size']})"
                ),
                details=cal,
            )

        return GuardrailResult(
            passed=True,
            guard_name="confidence_calibration",
            severity="info",
            reason=f"Calibration OK: error={cal['calibration_error']:.3f}",
            details=cal,
        )


class ReasoningEntropyMonitor:
    """Tracks reasoning diversity and detects stagnation.

    Monitors last N reasoning strings. Computes Jaccard similarity
    between consecutive reasonings. High similarity for M+ consecutive
    -> flag stagnation.

    Also tracks unique key_factors ratio.
    """

    def __init__(self, max_history: int = 20):
        """Initialize entropy monitor.

        Args:
            max_history: Maximum reasoning strings to track
        """
        self._reasoning_history: deque[str] = deque(maxlen=max_history)
        self._key_factors_history: deque[list[str]] = deque(maxlen=max_history)

    def record_reasoning(self, reasoning: str, key_factors: list[str]) -> None:
        """Record a new reasoning string and its key factors.

        Args:
            reasoning: Claude's reasoning text
            key_factors: List of key factor strings
        """
        self._reasoning_history.append(reasoning)
        self._key_factors_history.append(key_factors or [])

    def compute_similarity_scores(self) -> list[float]:
        """Compute Jaccard similarity between consecutive reasoning strings.

        Returns:
            List of similarity scores (0-1)
        """
        if len(self._reasoning_history) < 2:
            return []

        scores = []
        history = list(self._reasoning_history)
        for i in range(1, len(history)):
            sim = self._jaccard_similarity(history[i - 1], history[i])
            scores.append(sim)

        return scores

    def compute_unique_factors_ratio(self) -> float:
        """Compute ratio of unique key factors across all recorded decisions.

        Returns:
            Ratio of unique factors to total factors (0-1)
        """
        all_factors = []
        for factors in self._key_factors_history:
            all_factors.extend(factors)

        if not all_factors:
            return 1.0  # No factors = no stagnation

        unique_count = len(set(all_factors))
        total_count = len(all_factors)

        return unique_count / total_count

    def check_stagnation(self, config: GuardrailConfig) -> list[GuardrailResult]:
        """Check for reasoning stagnation.

        Two checks:
        1. High consecutive similarity (>threshold for N+ consecutive)
        2. Low unique key_factors ratio (<0.30)

        Args:
            config: Guardrail configuration

        Returns:
            List of GuardrailResult
        """
        results = []

        # Check reasoning similarity stagnation
        if config.reasoning_entropy_enabled:
            scores = self.compute_similarity_scores()
            if scores:
                # Count consecutive high-similarity scores
                consecutive = 0
                max_consecutive = 0
                for score in scores:
                    if score > config.reasoning_similarity_threshold:
                        consecutive += 1
                        max_consecutive = max(max_consecutive, consecutive)
                    else:
                        consecutive = 0

                if max_consecutive >= config.reasoning_stagnation_count:
                    results.append(GuardrailResult(
                        passed=False,
                        guard_name="reasoning_entropy",
                        severity="warning",
                        reason=(
                            f"Reasoning stagnation detected: {max_consecutive} consecutive "
                            f"decisions with >{config.reasoning_similarity_threshold:.0%} similarity"
                        ),
                        details={
                            "consecutive_similar": max_consecutive,
                            "threshold": config.reasoning_similarity_threshold,
                            "recent_scores": [round(s, 3) for s in scores[-10:]],
                        },
                    ))

            # Check unique key_factors ratio
            ratio = self.compute_unique_factors_ratio()
            if len(self._key_factors_history) >= 5 and ratio < 0.30:
                results.append(GuardrailResult(
                    passed=False,
                    guard_name="reasoning_entropy",
                    severity="warning",
                    reason=(
                        f"Low key factor diversity: unique ratio {ratio:.2f} (threshold: 0.30). "
                        f"Claude may be reusing the same factors."
                    ),
                    details={
                        "unique_factors_ratio": round(ratio, 3),
                        "history_size": len(self._key_factors_history),
                    },
                ))

        if not results:
            results.append(GuardrailResult(
                passed=True,
                guard_name="reasoning_entropy",
                severity="info",
                reason="Reasoning diversity is adequate",
            ))

        return results

    @staticmethod
    def _jaccard_similarity(text_a: str, text_b: str) -> float:
        """Compute Jaccard similarity between two texts (word-level).

        Args:
            text_a: First text
            text_b: Second text

        Returns:
            Jaccard similarity coefficient (0-1)
        """
        words_a = set(text_a.lower().split())
        words_b = set(text_b.lower().split())

        if not words_a and not words_b:
            return 1.0
        if not words_a or not words_b:
            return 0.0

        intersection = words_a & words_b
        union = words_a | words_b

        return len(intersection) / len(union)
