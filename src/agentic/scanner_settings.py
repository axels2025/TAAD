"""Scanner settings model with YAML persistence.

Provides Pydantic models for the auto-select scanner configuration
(filter criteria, ranking weights, budget limits) and functions to
load/save them from config/scanner_settings.yaml.

Follows the same pattern as src/nakedtrader/config.py — Pydantic for
validation, YAML for human-editable persistence, defaults if no file.
"""

from pathlib import Path

import yaml
from loguru import logger
from pydantic import BaseModel, Field, model_validator

DEFAULT_PATH = Path("config/scanner_settings.yaml")


class FilterSettings(BaseModel):
    """Option filter criteria for strike selection."""

    delta_min: float = Field(default=0.05, ge=0.0, le=1.0)
    delta_max: float = Field(default=0.30, ge=0.0, le=1.0)
    delta_target: float = Field(default=0.065, ge=0.0, le=1.0)
    min_premium: float = Field(default=0.30, ge=0.0)
    min_otm_pct: float = Field(default=0.10, ge=0.0, le=1.0)
    max_dte: int = Field(default=7, ge=1)
    dte_prefer_shortest: bool = True


class RankingWeights(BaseModel):
    """Weights for scoring and ranking option candidates (must sum to 100)."""

    safety: int = Field(default=40, ge=0, le=100)
    liquidity: int = Field(default=30, ge=0, le=100)
    ai_score: int = Field(default=20, ge=0, le=100)
    efficiency: int = Field(default=10, ge=0, le=100)

    @model_validator(mode="after")
    def weights_must_sum_to_100(self) -> "RankingWeights":
        total = self.safety + self.liquidity + self.ai_score + self.efficiency
        if total != 100:
            raise ValueError(
                f"Ranking weights must sum to 100, got {total} "
                f"(safety={self.safety}, liquidity={self.liquidity}, "
                f"ai_score={self.ai_score}, efficiency={self.efficiency})"
            )
        return self


class BudgetSettings(BaseModel):
    """Budget and position limits for the scanner portfolio."""

    margin_budget_pct: float = Field(default=0.20, ge=0.01, le=1.0)
    margin_budget_default: float = Field(
        default=50000.0, ge=1000.0,
        description="Fallback margin budget when IBKR is offline ($)",
    )
    max_positions: int = Field(default=10, ge=1)
    max_positions_per_day: int = Field(
        default=10, ge=1,
        description="Maximum new positions opened per day",
    )
    max_per_sector: int = Field(default=5, ge=1)
    price_threshold: float = Field(default=90.0, ge=0.0)
    max_contracts_expensive: int = Field(default=3, ge=1)
    max_contracts_cheap: int = Field(default=5, ge=1)

    # Position sizing risk parameters
    risk_per_trade_pct: float = Field(
        default=0.02, ge=0.005, le=0.20,
        description="Max risk per trade as fraction of NLV (0.02 = 2%)",
    )
    loss_assumption_pct: float = Field(
        default=0.25, ge=0.05, le=0.50,
        description="Assumed max stock drop for risk calc (0.25 = 25%)",
    )

    # VIX-based position scaling
    vix_scale_normal: float = Field(
        default=0.80, ge=0.1, le=1.0,
        description="Sizing multiplier when VIX 15-25",
    )
    vix_scale_elevated: float = Field(
        default=0.50, ge=0.1, le=1.0,
        description="Sizing multiplier when VIX 25-35",
    )
    vix_scale_extreme: float = Field(
        default=0.25, ge=0.0, le=1.0,
        description="Sizing multiplier when VIX > 35",
    )


class RiskGovernorSettings(BaseModel):
    """Risk governor circuit breaker settings.

    These limits are enforced at execution time by the RiskGovernor.
    They act as hard safety ceilings independent of the scanner pipeline.
    """

    max_margin_utilization: float = Field(
        default=0.80, ge=0.10, le=1.0,
        description="Maximum margin utilization allowed (0.80 = 80% of NLV)",
    )
    max_margin_per_trade_pct: float = Field(
        default=0.10, ge=0.01, le=0.50,
        description="Max margin for a single trade as fraction of NLV (0.10 = 10%)",
    )
    max_daily_loss_pct: float = Field(
        default=-0.02, ge=-1.0, le=0.0,
        description="Max daily loss before circuit breaker (-0.02 = -2%)",
    )
    max_weekly_loss_pct: float = Field(
        default=-0.05, ge=-1.0, le=0.0,
        description="Max weekly loss before circuit breaker (-0.05 = -5%)",
    )
    max_drawdown_pct: float = Field(
        default=-0.10, ge=-1.0, le=0.0,
        description="Max peak-to-trough drawdown before halt (-0.10 = -10%)",
    )
    max_position_loss: float = Field(
        default=-500.0, le=0.0,
        description="Max loss per position before stop loss ($)",
    )
    max_spread_pct: float = Field(
        default=0.10, ge=0.0, le=1.0,
        description="Max bid-ask spread as fraction of mid price (0.10 = 10%)",
    )


class ScannerScanSettings(BaseModel):
    """IBKR scanner parameters (num_rows, etc.)."""

    num_rows: int = Field(default=50, ge=10, le=500)


class EarningsFilterSettings(BaseModel):
    """Earnings detection and optional filter adjustment.

    Earnings are always detected for every symbol.  When ``enabled`` is
    True the scanner adds ``additional_otm_pct`` to the base min OTM %
    for symbols with earnings within the option's DTE.  When ``enabled``
    is False, earnings are still detected but only a warning is logged
    (no filter adjustment).
    """

    enabled: bool = False
    additional_otm_pct: float = Field(default=0.15, ge=0.0, le=1.0)
    lookahead_days: int = Field(default=0, ge=0, le=14)


class ScannerSettings(BaseModel):
    """Complete scanner settings: filters, ranking, budget, earnings, and risk."""

    filters: FilterSettings = FilterSettings()
    ranking: RankingWeights = RankingWeights()
    budget: BudgetSettings = BudgetSettings()
    risk_governor: RiskGovernorSettings = Field(default_factory=RiskGovernorSettings)
    earnings: EarningsFilterSettings = Field(default_factory=EarningsFilterSettings)
    scanner: ScannerScanSettings = Field(default_factory=ScannerScanSettings)


def load_scanner_settings(path: str | Path = DEFAULT_PATH) -> ScannerSettings:
    """Load scanner settings from YAML, returning defaults if file is missing.

    Args:
        path: Path to the YAML config file.

    Returns:
        Validated ScannerSettings instance.
    """
    path = Path(path)
    if not path.exists():
        logger.info(f"Scanner settings file not found at {path}, using defaults")
        return ScannerSettings()

    with open(path) as f:
        data = yaml.safe_load(f) or {}

    settings = ScannerSettings(**data)
    logger.debug(f"Loaded scanner settings from {path}")
    return settings


def save_scanner_settings(
    settings: ScannerSettings, path: str | Path = DEFAULT_PATH
) -> None:
    """Save scanner settings to YAML.

    Args:
        settings: Validated ScannerSettings to persist.
        path: Path to write the YAML config file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with open(path, "w") as f:
        yaml.dump(
            settings.model_dump(),
            f,
            default_flow_style=False,
            sort_keys=False,
        )

    logger.info(f"Saved scanner settings to {path}")
