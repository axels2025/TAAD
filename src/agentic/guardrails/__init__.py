"""Hallucination guardrails for the agentic trading daemon.

Individually toggleable guards coordinated by a GuardrailRegistry.
Guards return GuardrailResult(passed, guard_name, severity, reason, details).
On severity="block", the registry overrides the decision to MONITOR_ONLY.

Zero additional Claude API calls. All guards are pure Python logic.
"""

from src.agentic.guardrails.config import GuardrailConfig
from src.agentic.guardrails.registry import GuardrailRegistry, GuardrailResult

__all__ = ["GuardrailConfig", "GuardrailRegistry", "GuardrailResult"]
