"""GuardrailRegistry orchestrator and GuardrailResult dataclass.

Coordinates all registered guards and collects results.
On any severity="block" result, overrides the decision to MONITOR_ONLY.
"""

from dataclasses import dataclass, field
from typing import Optional

from loguru import logger

from src.agentic.guardrails.config import GuardrailConfig


@dataclass
class GuardrailResult:
    """Result from a single guardrail check."""

    passed: bool
    guard_name: str
    severity: str  # "block", "warning", "info"
    reason: str = ""
    details: dict = field(default_factory=dict)


class GuardrailRegistry:
    """Orchestrates all registered guardrail checks.

    Runs context validation (pre-Claude) and output validation (post-Claude)
    pipelines. Collects results and determines whether to block or warn.
    """

    def __init__(self, config: Optional[GuardrailConfig] = None):
        """Initialize registry with configuration.

        Args:
            config: Guardrail configuration (uses defaults if None)
        """
        self.config = config or GuardrailConfig()
        self._context_validators = []
        self._output_validators = []
        self._execution_gates = []

    def register_context_validator(self, validator) -> None:
        """Register a pre-Claude context validator."""
        self._context_validators.append(validator)

    def register_output_validator(self, validator) -> None:
        """Register a post-Claude output validator."""
        self._output_validators.append(validator)

    def register_execution_gate(self, gate) -> None:
        """Register a pre-execution gate."""
        self._execution_gates.append(gate)

    def validate_context(self, context) -> list[GuardrailResult]:
        """Run all context validators (pre-Claude).

        Args:
            context: ReasoningContext to validate

        Returns:
            List of GuardrailResult from all validators
        """
        if not self.config.enabled:
            return []

        results = []
        for validator in self._context_validators:
            try:
                validator_results = validator.validate(context, self.config)
                results.extend(validator_results)
            except Exception as e:
                logger.error(f"Context validator {type(validator).__name__} failed: {e}")
                results.append(GuardrailResult(
                    passed=True,  # Don't block on validator errors
                    guard_name=type(validator).__name__,
                    severity="warning",
                    reason=f"Validator error: {e}",
                ))

        self._log_results("context", results)
        return results

    def validate_output(self, decision, context) -> list[GuardrailResult]:
        """Run all output validators (post-Claude).

        Args:
            decision: DecisionOutput from Claude
            context: ReasoningContext that was sent to Claude

        Returns:
            List of GuardrailResult from all validators
        """
        if not self.config.enabled:
            return []

        results = []
        for validator in self._output_validators:
            try:
                validator_results = validator.validate(decision, context, self.config)
                results.extend(validator_results)
            except Exception as e:
                logger.error(f"Output validator {type(validator).__name__} failed: {e}")
                results.append(GuardrailResult(
                    passed=True,
                    guard_name=type(validator).__name__,
                    severity="warning",
                    reason=f"Validator error: {e}",
                ))

        self._log_results("output", results)
        return results

    def validate_execution(self, decision, context, **kwargs) -> list[GuardrailResult]:
        """Run all execution gates (pre-execution).

        Args:
            decision: DecisionOutput about to be executed
            context: Execution context
            **kwargs: Additional arguments (e.g., ibkr_client)

        Returns:
            List of GuardrailResult from all gates
        """
        if not self.config.enabled:
            return []

        results = []
        for gate in self._execution_gates:
            try:
                gate_results = gate.validate(decision, context, self.config, **kwargs)
                results.extend(gate_results)
            except Exception as e:
                logger.error(f"Execution gate {type(gate).__name__} failed: {e}")
                results.append(GuardrailResult(
                    passed=True,
                    guard_name=type(gate).__name__,
                    severity="warning",
                    reason=f"Gate error: {e}",
                ))

        self._log_results("execution", results)
        return results

    def has_block(self, results: list[GuardrailResult]) -> bool:
        """Check if any result has severity='block'.

        Args:
            results: List of guardrail results

        Returns:
            True if any result is a blocking failure
        """
        return any(not r.passed and r.severity == "block" for r in results)

    def get_block_reasons(self, results: list[GuardrailResult]) -> list[str]:
        """Get all blocking reasons from results.

        Args:
            results: List of guardrail results

        Returns:
            List of reason strings from blocking results
        """
        return [
            f"[{r.guard_name}] {r.reason}"
            for r in results
            if not r.passed and r.severity == "block"
        ]

    def results_to_dict(self, results: list[GuardrailResult]) -> list[dict]:
        """Serialize results to dict for JSON storage in DecisionAudit.

        Args:
            results: List of guardrail results

        Returns:
            List of serializable dicts
        """
        return [
            {
                "passed": r.passed,
                "guard_name": r.guard_name,
                "severity": r.severity,
                "reason": r.reason,
                "details": r.details,
            }
            for r in results
        ]

    def _log_results(self, phase: str, results: list[GuardrailResult]) -> None:
        """Log guardrail results."""
        blocks = [r for r in results if not r.passed and r.severity == "block"]
        warnings = [r for r in results if not r.passed and r.severity == "warning"]

        if blocks:
            logger.warning(
                f"Guardrail {phase}: {len(blocks)} BLOCK(s) - "
                + "; ".join(f"[{r.guard_name}] {r.reason}" for r in blocks)
            )
        if warnings:
            logger.info(
                f"Guardrail {phase}: {len(warnings)} warning(s) - "
                + "; ".join(f"[{r.guard_name}] {r.reason}" for r in warnings)
            )
