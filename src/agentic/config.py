"""Phase 5 daemon configuration.

Loads from config/phase5.yaml with environment variable overrides.
All parameters have safe defaults for paper trading.
"""

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class AutonomyConfig(BaseModel):
    """Autonomy level configuration."""

    initial_level: int = Field(default=1, ge=1, le=4)
    promotion_clean_days: int = Field(default=5, ge=1)
    promotion_min_trades: int = Field(default=10, ge=1)
    promotion_min_win_rate: float = Field(default=0.60, ge=0.0, le=1.0)
    demotion_loss_streak: int = Field(default=3, ge=1)
    max_level: int = Field(default=2, ge=1, le=4)  # Safety: cap at L2 for paper trading


class ClaudeConfig(BaseModel):
    """Claude API configuration."""

    reasoning_model: str = "claude-sonnet-4-5-20250929"
    reflection_model: str = "claude-sonnet-4-5-20250929"
    embedding_model: str = "text-embedding-3-small"
    max_tokens: int = Field(default=4096, ge=256)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    daily_cost_cap_usd: float = Field(default=10.0, ge=0.0)
    max_retries: int = Field(default=3, ge=1)


class DaemonConfig(BaseModel):
    """Daemon process configuration."""

    client_id: int = Field(default=10, ge=1)  # Avoids conflicts with CLI (1), watch (2/4), sell (5)
    heartbeat_interval_seconds: int = Field(default=60, ge=10)
    event_poll_interval_seconds: int = Field(default=5, ge=1)
    max_events_per_cycle: int = Field(default=10, ge=1)
    pid_file: str = "run/taad.pid"
    graceful_shutdown_timeout_seconds: int = Field(default=30, ge=5)


class AlertConfig(BaseModel):
    """Alert routing configuration."""

    log_all: bool = True
    email_medium_and_above: bool = True
    webhook_high_and_above: bool = True


class DashboardConfig(BaseModel):
    """Web dashboard configuration."""

    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = Field(default=8080, ge=1024, le=65535)
    auth_token: str = ""  # Must be set in production


class LearningLoopConfig(BaseModel):
    """Learning loop configuration."""

    eod_reflection_time: str = "16:30"  # 4:30 PM ET
    min_trades_for_experiment: int = Field(default=10, ge=3)
    max_concurrent_experiments: int = Field(default=3, ge=1)


class Phase5Config(BaseModel):
    """Top-level Phase 5 configuration.

    Loads from config/phase5.yaml with safe defaults.
    """

    autonomy: AutonomyConfig = Field(default_factory=AutonomyConfig)
    claude: ClaudeConfig = Field(default_factory=ClaudeConfig)
    daemon: DaemonConfig = Field(default_factory=DaemonConfig)
    alerts: AlertConfig = Field(default_factory=AlertConfig)
    dashboard: DashboardConfig = Field(default_factory=DashboardConfig)
    learning: LearningLoopConfig = Field(default_factory=LearningLoopConfig)
    guardrails: "GuardrailConfig" = Field(default_factory=lambda: _default_guardrail_config())


def _default_guardrail_config():
    """Lazy import to avoid circular dependency."""
    from src.agentic.guardrails.config import GuardrailConfig
    return GuardrailConfig()


def load_phase5_config(config_path: Optional[str] = None) -> Phase5Config:
    """Load Phase 5 configuration from YAML file.

    Falls back to defaults if file not found.

    Args:
        config_path: Path to phase5.yaml. Defaults to config/phase5.yaml.

    Returns:
        Phase5Config instance
    """
    # Resolve the forward reference to GuardrailConfig before instantiation.
    # Pydantic v2 requires all string-annotated types to be resolvable.
    from src.agentic.guardrails.config import GuardrailConfig  # noqa: F811

    Phase5Config.model_rebuild(_types_namespace={"GuardrailConfig": GuardrailConfig})

    if config_path is None:
        config_path = str(Path("config/phase5.yaml"))

    path = Path(config_path)
    if path.exists():
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        return Phase5Config(**data)

    return Phase5Config()
