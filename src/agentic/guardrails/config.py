"""Guardrail configuration with per-guard toggles.

All guards default to enabled. Loaded from the guardrails section
of config/phase5.yaml.
"""

from pydantic import BaseModel, Field


class GuardrailConfig(BaseModel):
    """Per-guard toggles and thresholds for hallucination guardrails."""

    # Global toggle
    enabled: bool = True

    # Phase 6.1: Output validation
    action_plausibility_enabled: bool = True
    symbol_crossref_enabled: bool = True
    reasoning_coherence_enabled: bool = True

    # Phase 6.2: Input validation
    data_freshness_enabled: bool = True
    consistency_check_enabled: bool = True
    null_sanitization_enabled: bool = True

    # Phase 6.4: Numerical grounding
    numerical_grounding_enabled: bool = True
    numerical_tolerance_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    numerical_max_mismatches_before_block: int = Field(default=2, ge=1)

    # Phase 6.5: Execution gate
    execution_gate_enabled: bool = True
    vix_movement_block_pct: float = Field(default=15.0, ge=1.0)
    spy_movement_block_pct: float = Field(default=2.0, ge=0.5)
    max_orders_per_minute: int = Field(default=5, ge=1)

    # Phase 6.6: Monitoring
    confidence_calibration_enabled: bool = True
    reasoning_entropy_enabled: bool = True
    calibration_error_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    reasoning_similarity_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    reasoning_stagnation_count: int = Field(default=5, ge=2)
