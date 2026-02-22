"""NakedTrader configuration loaded from YAML with CLI overrides.

Provides a Pydantic model for the daily SPX options trading configuration,
loaded from config/daily_spx_options.yaml with optional CLI overrides.
"""

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator


class InstrumentConfig(BaseModel):
    """Instrument settings."""

    default_symbol: str = "XSP"
    contracts: int = Field(default=1, ge=1)
    max_contracts: int = Field(default=10, ge=1)

    @field_validator("default_symbol")
    @classmethod
    def validate_symbol(cls, v: str) -> str:
        allowed = {"SPX", "XSP", "SPY"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"Symbol must be one of {allowed}, got '{v}'")
        return v


class StrikeConfig(BaseModel):
    """Delta-based strike selection parameters."""

    delta_min: float = Field(default=0.05, ge=0.0, le=1.0)
    delta_max: float = Field(default=0.12, ge=0.0, le=1.0)
    delta_target: float = Field(default=0.065, ge=0.0, le=1.0)


class DTEConfig(BaseModel):
    """Days-to-expiration parameters."""

    min: int = Field(default=1, ge=0)
    max: int = Field(default=4, ge=0)
    prefer_shortest: bool = True


class PremiumConfig(BaseModel):
    """Premium thresholds."""

    min: float = Field(default=0.30, ge=0.0)


class ExitConfig(BaseModel):
    """Exit order parameters."""

    profit_target_pct: float = Field(default=0.70, ge=0.0, le=1.0)
    profit_target_floor: float = Field(default=0.10, ge=0.0)
    stop_loss_enabled: bool = False
    stop_loss_multiplier: float = Field(default=3.0, ge=1.0)


class ExecutionConfig(BaseModel):
    """Execution timing parameters."""

    wait_for_open: bool = True
    open_delay_seconds: int = Field(default=60, ge=0)
    order_type: str = "LMT"
    fill_timeout_seconds: int = Field(default=300, ge=0)
    latest_entry_time: str = "15:00"

    @field_validator("order_type")
    @classmethod
    def validate_order_type(cls, v: str) -> str:
        allowed = {"LMT", "MKT"}
        v = v.upper()
        if v not in allowed:
            raise ValueError(f"order_type must be one of {allowed}, got '{v}'")
        return v


class WatchConfig(BaseModel):
    """Watch command parameters."""

    interval_seconds: int = Field(default=120, ge=5)
    show_greeks: bool = True


class NakedTraderConfig(BaseModel):
    """Complete NakedTrader configuration.

    Loaded from YAML with optional CLI overrides for per-run adjustments.
    """

    instrument: InstrumentConfig = InstrumentConfig()
    strike: StrikeConfig = StrikeConfig()
    dte: DTEConfig = DTEConfig()
    premium: PremiumConfig = PremiumConfig()
    exit: ExitConfig = ExitConfig()
    execution: ExecutionConfig = ExecutionConfig()
    watch: WatchConfig = WatchConfig()

    @classmethod
    def from_yaml(cls, path: str | Path) -> "NakedTraderConfig":
        """Load configuration from a YAML file.

        Args:
            path: Path to the YAML config file.

        Returns:
            Validated NakedTraderConfig instance.

        Raises:
            FileNotFoundError: If the config file doesn't exist.
            ValidationError: If the config values are invalid.
        """
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"Config file not found: {path}")

        with open(path) as f:
            data = yaml.safe_load(f) or {}

        return cls(**data)

    def with_overrides(
        self,
        symbol: str | None = None,
        contracts: int | None = None,
        delta: float | None = None,
        dte: int | None = None,
        stop_loss: bool | None = None,
    ) -> "NakedTraderConfig":
        """Return a new config with CLI overrides applied.

        Args:
            symbol: Override instrument symbol.
            contracts: Override number of contracts.
            delta: Override target delta.
            dte: Override max DTE.
            stop_loss: Override stop-loss enabled/disabled.

        Returns:
            New NakedTraderConfig with overrides applied.
        """
        data = self.model_dump()

        if symbol is not None:
            data["instrument"]["default_symbol"] = symbol.upper()
        if contracts is not None:
            data["instrument"]["contracts"] = contracts
        if delta is not None:
            data["strike"]["delta_target"] = delta
        if dte is not None:
            data["dte"]["max"] = dte
        if stop_loss is not None:
            data["exit"]["stop_loss_enabled"] = stop_loss

        return NakedTraderConfig(**data)
