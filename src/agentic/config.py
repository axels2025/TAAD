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
    disabled_triggers: list[str] = Field(default_factory=list)
    auto_execute_actions: list[str] = Field(default_factory=list)
    execute_confidence_threshold: float = Field(default=0.80, ge=0.5, le=1.0)


class ClaudeConfig(BaseModel):
    """Claude API configuration."""

    reasoning_model: str = "claude-sonnet-4-5-20250929"
    reflection_model: str = "claude-sonnet-4-5-20250929"
    embedding_model: str = "text-embedding-3-small"
    max_tokens: int = Field(default=4096, ge=256)
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    daily_cost_cap_usd: float = Field(default=10.0, ge=0.0)
    max_retries: int = Field(default=3, ge=1)
    reasoning_system_prompt: str = ""
    position_exit_system_prompt: str = ""
    reflection_system_prompt: str = ""
    performance_analysis_system_prompt: str = ""


class DaemonConfig(BaseModel):
    """Daemon process configuration."""

    client_id: int = Field(default=10, ge=1)  # Avoids conflicts with CLI (1), watch (2/4), sell (5)
    heartbeat_interval_seconds: int = Field(default=60, ge=10)
    event_poll_interval_seconds: int = Field(default=5, ge=1)
    max_events_per_cycle: int = Field(default=10, ge=1)
    pid_file: str = "run/taad.pid"
    graceful_shutdown_timeout_seconds: int = Field(default=30, ge=5)
    reconnect_interval_seconds: int = Field(default=30, ge=10, le=300)
    reconnect_alert_audio_path: str = ""  # Startup/pre-market TWS reminder
    reconnect_disconnect_audio_path: str = ""  # Played on connection loss
    reconnect_success_audio_path: str = ""  # Played on successful reconnection
    reconnect_alert_cooldown_seconds: int = Field(default=300, ge=60)
    premarket_alert_minutes: int = Field(default=15, ge=5, le=60)


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
    weekly_learning_day: str = Field(default="Friday", description="Day of week for weekly learning cycle")
    weekly_learning_hour: int = Field(default=17, ge=0, le=23, description="Hour (ET) for weekly learning trigger")
    min_trades_for_experiment: int = Field(default=10, ge=3)
    max_concurrent_experiments: int = Field(default=3, ge=1)
    auto_apply_threshold: float = Field(default=0.80, ge=0.50, le=1.0, description="Confidence threshold for auto-applying parameter changes")
    eod_reflection_enabled: bool = Field(default=True, description="Enable daily EOD reflection via Claude")
    eod_reflection_frequency: str = Field(default="daily", description="Reflection frequency: 'daily' or 'weekly'")
    hypothesis_generation_enabled: bool = Field(default=True, description="Enable Claude-generated hypotheses during weekly learning")
    hypothesis_model: str = Field(default="claude-sonnet-4-5-20250929", description="Model for hypothesis generation")
    learning_accounts: list[str] = Field(
        default_factory=list,
        description="Account IDs included in learning analysis. Empty = all non-paper accounts.",
    )


class StrategyConfig(BaseModel):
    """Strategy-level configuration."""

    entry_days: list[str] = Field(
        default_factory=lambda: ["Monday", "Tuesday"],
        description="Days of the week when new trades can be staged/executed",
    )


class AutoScanConfig(BaseModel):
    """Market-open auto-scan configuration."""

    enabled: bool = False  # Opt-in (default off)
    delay_minutes: int = Field(default=5, ge=0, le=30)  # Wait for spreads to settle
    scanner_preset: str = "naked-put"  # IBKR scanner preset
    auto_stage: bool = True  # Stage selected trades automatically
    require_ibkr: bool = True  # Hard requirement for IBKR connection


class CROConfig(BaseModel):
    """Chief Risk Officer adversarial agent configuration."""

    enabled: bool = Field(default=True, description="Enable CRO adversarial review before trade execution")
    model: str = Field(default="claude-sonnet-4-5-20250929", description="Model for CRO review (Sonnet for cost efficiency)")
    escalate_on_high: bool = Field(default=True, description="Escalate to human review on HIGH/CRITICAL objections")
    timeout: float = Field(default=45.0, ge=10.0, le=120.0, description="CRO API call timeout in seconds")
    max_retries: int = Field(default=2, ge=1, le=5)


class ExitRulesConfig(BaseModel):
    """Dashboard-configurable exit rules for the daemon's ExitManager.

    profit_target: fraction of max profit to exit at (0.50 = 50%)
    stop_loss: negative multiple of premium received (-2.00 = 2x premium)
    time_exit_dte: close N days before expiry (-1 = let expire)
    let_expire_premium: if current premium ≤ this when time_exit triggers,
        let the option expire worthless instead of paying to close (0.0 = always close)
    """

    profit_target: float = Field(default=0.50, ge=0.0, le=1.0)
    stop_loss: float = Field(default=-2.00, le=0.0)
    time_exit_dte: int = Field(default=2, ge=-1, le=14)
    let_expire_premium: float = Field(default=0.05, ge=0.0, le=1.0)


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
    auto_scan: AutoScanConfig = Field(default_factory=AutoScanConfig)
    exit_rules: ExitRulesConfig = Field(default_factory=ExitRulesConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    cro: CROConfig = Field(default_factory=CROConfig)
    guardrails: "GuardrailConfig" = Field(default_factory=lambda: _default_guardrail_config())


def _default_guardrail_config():
    """Lazy import to avoid circular dependency."""
    from src.agentic.guardrails.config import GuardrailConfig
    return GuardrailConfig()


# Resolve forward reference so Phase5Config() works without load_phase5_config()
def _rebuild_phase5_config():
    try:
        from src.agentic.guardrails.config import GuardrailConfig
        Phase5Config.model_rebuild(_types_namespace={"GuardrailConfig": GuardrailConfig})
    except Exception:
        pass  # Circular import edge case — load_phase5_config() will rebuild later

_rebuild_phase5_config()


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
