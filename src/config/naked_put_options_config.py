"""Configuration for naked put options screening using Barchart API.

This module defines all parameters for the Barchart options screener workflow.
All parameters are configurable via environment variables (.env file).
"""

import os
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class BarchartScreenerSettings(BaseSettings):
    """Settings for Barchart options screener loaded from environment.

    These settings map to the Barchart getOptionsScreener API parameters.
    All parameters can be configured via the .env file with BARCHART_ prefix.

    Example .env configuration:
        BARCHART_API_KEY=your_api_key_here
        BARCHART_DTE_MIN=0
        BARCHART_DTE_MAX=30
        BARCHART_DELTA_MIN=-0.50
        BARCHART_DELTA_MAX=-0.10
    """

    # API Configuration
    api_key: str = Field(default="", description="Barchart OnDemand API key (required)")
    api_url: str = Field(
        default="https://ondemand.websol.barchart.com/getOptionsScreener.json",
        description="Barchart API endpoint URL",
    )

    # Days to Expiration (DTE) Range
    # Default minimum of 7 days prevents 0-DTE naked puts which carry extreme
    # gamma risk, near-zero recovery time, and after-hours assignment danger.
    # Override via BARCHART_DTE_MIN=0 in .env for testing if needed.
    dte_min: int = Field(default=7, ge=0, description="Minimum days to expiration (7 = safe minimum for naked puts)")
    dte_max: int = Field(default=30, ge=0, description="Maximum days to expiration")

    # Security Type Selection
    security_types: list[str] = Field(
        default=["stocks", "etfs"],
        description="List of security types to scan: stocks, etfs",
    )

    # Option Volume Range
    volume_min: int = Field(
        default=250, ge=0, description="Minimum option volume (daily)"
    )
    volume_max: Optional[int] = Field(
        default=None, description="Maximum option volume (None = unlimited)"
    )

    # Option Open Interest Range
    open_interest_min: int = Field(
        default=250, ge=0, description="Minimum open interest"
    )
    open_interest_max: Optional[int] = Field(
        default=None, description="Maximum open interest (None = unlimited)"
    )

    # OTM% Range (client-side filter applied after Barchart results)
    # Delta serves as a proxy at the API stage, but a -0.20 delta on a $500 stock
    # vs a $30 stock produces very different OTM percentages. This explicit OTM%
    # filter catches candidates that slip through the delta filter.
    otm_pct_min: float = Field(
        default_factory=lambda: float(os.getenv("MIN_OTM_PCT", "0.10")),
        ge=0.0,
        le=1.0,
        description="Minimum OTM percentage (0.10 = 10%). Candidates closer to the money are rejected. "
                    "Falls back to MIN_OTM_PCT env var for consistency with execution validator.",
    )
    otm_pct_max: float = Field(
        default=0.25,
        ge=0.0,
        le=1.0,
        description="Maximum OTM percentage (0.25 = 25%). Candidates too far OTM are rejected.",
    )

    # Moneyness (expressed as delta for Barchart API)
    # For puts: negative delta
    # -0.50 delta ≈ 50% OTM, -0.10 delta ≈ 10% OTM
    delta_min: float = Field(
        default=-0.50,
        ge=-1.0,
        le=0.0,
        description="Minimum delta (e.g., -0.50 for deeper OTM boundary)",
    )
    delta_max: float = Field(
        default=-0.10,
        ge=-1.0,
        le=0.0,
        description="Maximum delta (e.g., -0.10 for closer to money boundary)",
    )

    # Bid Price (minimum premium)
    bid_price_min: float = Field(
        default=0.20, ge=0.0, description="Minimum bid price for the option ($/share)"
    )
    bid_price_max: Optional[float] = Field(
        default=None, description="Maximum bid price (None = unlimited)"
    )

    # Underlying Stock Price Range
    # NOTE: Barchart API may not support this filter directly,
    # so we apply it client-side after receiving results
    stock_price_min: float = Field(
        default=30.0, ge=0.0, description="Minimum stock price for underlying"
    )
    stock_price_max: float = Field(
        default=250.0, ge=0.0, description="Maximum stock price for underlying"
    )

    # Raw Implied Volatility Range (Barchart API filter)
    # NOTE: This is raw/current IV, NOT IV Rank. Barchart returns raw IV values.
    # A stock with 30% IV might have a low IV Rank if its historical range is 30-80%.
    # IV Rank filtering is enforced separately during IBKR validation (see IBKRValidationSettings).
    raw_iv_min: float = Field(
        default=0.30,
        ge=0.0,
        le=5.0,
        description="Minimum raw implied volatility (0.30 = 30%). This is raw IV, not IV Rank.",
    )
    raw_iv_max: float = Field(
        default=0.80,
        ge=0.0,
        le=5.0,
        description="Maximum raw implied volatility (0.80 = 80%). This is raw IV, not IV Rank.",
    )

    # Results Configuration
    max_results: int = Field(
        default=100,
        ge=1,
        le=500,
        description="Maximum results to request from Barchart API",
    )

    # Output Configuration
    output_dir: str = Field(
        default="data/scans", description="Directory for saving scan output files"
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate that API key is provided."""
        if not v or v == "":
            raise ValueError(
                "BARCHART_API_KEY is required. "
                "Get your API key from https://www.barchart.com/ondemand "
                "and add it to your .env file"
            )
        return v

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        env_prefix = "BARCHART_"
        case_sensitive = False
        extra = "ignore"


class IBKRValidationSettings(BaseSettings):
    """Settings for IBKR validation of Barchart results.

    These settings control how we validate Barchart candidates
    using real-time IBKR data.

    Environment variables (in .env):
        MAX_SPREAD_PCT: Maximum bid-ask spread percentage (0.01 = 1%)
        MIN_MARGIN_EFFICIENCY: Minimum premium/margin ratio (0.02 = 2%)
        REQUIRE_UPTREND: Require stock to be in uptrend (true/false)
    """

    max_spread_pct: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Maximum bid-ask spread as percentage of mid price (0.20 = 20%)",
    )

    min_margin_efficiency: float = Field(
        default=0.02,
        ge=0.0,
        le=1.0,
        description="Minimum premium/margin ratio (0.02 = 2%)",
    )

    require_uptrend: bool = Field(
        default=True, description="Require stock to be in uptrend (price > 20-day SMA)"
    )

    max_candidates_to_validate: int = Field(
        default=50,
        ge=1,
        le=200,
        description="Maximum number of Barchart candidates to validate with IBKR",
    )

    # IV Rank minimum threshold (computed from IBKR historical IV data)
    # IV Rank = (current_iv - 52wk_low_iv) / (52wk_high_iv - 52wk_low_iv)
    # A low IV Rank means premiums are cheap relative to the stock's own history.
    # We want elevated IV Rank so premiums are rich.
    iv_rank_min: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Minimum IV Rank (0.30 = 30%). Candidates below this are rejected during IBKR validation.",
    )

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False
        extra = "ignore"


class NakedPutScreenerConfig(BaseModel):
    """Complete configuration for naked put options screening.

    Combines Barchart screener settings with IBKR validation settings
    to provide a two-step screening workflow:

    1. Barchart: Fast server-side scan of entire market
    2. IBKR: Real-time validation of top candidates

    Example:
        >>> config = NakedPutScreenerConfig.from_env()
        >>> params = config.to_barchart_params()
        >>> print(params)
        {'optionType': 'put', 'minDTE': 0, 'maxDTE': 30, ...}
    """

    screener: BarchartScreenerSettings = Field(default_factory=BarchartScreenerSettings)

    validation: IBKRValidationSettings = Field(default_factory=IBKRValidationSettings)

    @classmethod
    def from_env(cls) -> "NakedPutScreenerConfig":
        """Load configuration from environment variables.

        Returns:
            NakedPutScreenerConfig: Configuration loaded from .env file

        Raises:
            ValueError: If required settings (e.g., API key) are missing
        """
        return cls(
            screener=BarchartScreenerSettings(), validation=IBKRValidationSettings()
        )

    def to_barchart_params(self) -> dict:
        """Convert config to Barchart API parameters.

        Returns:
            dict: Parameters ready for Barchart getOptionsScreener API call

        Example:
            >>> config = NakedPutScreenerConfig.from_env()
            >>> params = config.to_barchart_params()
            >>> # Use params in API request
            >>> response = requests.get(api_url, params=params)
        """
        params = {
            "apikey": self.screener.api_key,
            "optionType": "put",
            "minDTE": self.screener.dte_min,
            "maxDTE": self.screener.dte_max,
            "minVolume": self.screener.volume_min,
            "minOpenInterest": self.screener.open_interest_min,
            "minDelta": self.screener.delta_min,
            "maxDelta": self.screener.delta_max,
            "minPrice": self.screener.bid_price_min,
            "fields": "delta,gamma,theta,vega,bid,ask,volume,openInterest,volatility",
            "limit": self.screener.max_results,
        }

        # Add optional max filters if specified
        if self.screener.volume_max is not None:
            params["maxVolume"] = self.screener.volume_max

        if self.screener.open_interest_max is not None:
            params["maxOpenInterest"] = self.screener.open_interest_max

        if self.screener.bid_price_max is not None:
            params["maxPrice"] = self.screener.bid_price_max

        return params

    def display_summary(self) -> dict:
        """Get a summary of current configuration for display.

        Returns:
            dict: Human-readable configuration summary
        """
        return {
            "DTE Range": f"{self.screener.dte_min}-{self.screener.dte_max} days",
            "Delta Range": f"{self.screener.delta_min} to {self.screener.delta_max}",
            "OTM% Range": f"{self.screener.otm_pct_min:.0%}-{self.screener.otm_pct_max:.0%}",
            "Min Bid Price": f"${self.screener.bid_price_min:.2f}",
            "Stock Price Range": f"${self.screener.stock_price_min:.0f}-${self.screener.stock_price_max:.0f}",
            "Min Volume": str(self.screener.volume_min),
            "Min Open Interest": str(self.screener.open_interest_min),
            "Raw IV Range": f"{self.screener.raw_iv_min:.0%}-{self.screener.raw_iv_max:.0%}",
            "Security Types": ", ".join(self.screener.security_types),
            "Max Spread": f"{self.validation.max_spread_pct:.0%}",
            "Min Margin Efficiency": f"{self.validation.min_margin_efficiency:.1%}",
            "Min IV Rank": f"{self.validation.iv_rank_min:.0%}",
            "Require Uptrend": str(self.validation.require_uptrend),
        }


# Global config instance (singleton pattern)
_config: Optional[NakedPutScreenerConfig] = None


def get_naked_put_config() -> NakedPutScreenerConfig:
    """Get the global naked put screener configuration.

    Uses singleton pattern to load configuration once and reuse it.
    Configuration is loaded from environment variables (.env file).

    Returns:
        NakedPutScreenerConfig: The global configuration instance

    Raises:
        ValueError: If required configuration (e.g., API key) is missing

    Example:
        >>> config = get_naked_put_config()
        >>> print(config.screener.dte_min)
        7
        >>> print(config.validation.require_uptrend)
        True
    """
    global _config
    if _config is None:
        _config = NakedPutScreenerConfig.from_env()
    return _config


def get_validation_only_config() -> NakedPutScreenerConfig:
    """Get config for validation only (CSV import + IBKR validation).

    Creates a minimal config with only validation settings, bypassing
    Barchart API key validation. Used when importing from CSV file
    and only need IBKR validation settings.

    Returns:
        NakedPutScreenerConfig: Config with only validation settings

    Example:
        >>> config = get_validation_only_config()
        >>> print(config.validation.max_spread_pct)
        0.20
    """
    # Create minimal screener settings without API key validation
    minimal_screener = BarchartScreenerSettings.model_construct(
        api_key="not_required_for_csv",  # Bypass validation
        api_url="",
        otm_pct_min=0.10,
        otm_pct_max=0.25,
        dte_min=0,
        dte_max=30,
        security_types=["stocks"],
        volume_min=0,
        volume_max=None,
        open_interest_min=0,
        open_interest_max=None,
        delta_min=-0.50,
        delta_max=-0.10,
        bid_price_min=0.0,
        bid_price_max=None,
        stock_price_min=0.0,
        stock_price_max=10000.0,
        raw_iv_min=0.0,
        raw_iv_max=5.0,
        max_results=100,
        output_dir="data/scans",
    )

    return NakedPutScreenerConfig(
        screener=minimal_screener, validation=IBKRValidationSettings()
    )
