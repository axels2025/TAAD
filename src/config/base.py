"""Base configuration with Pydantic validation.

This module provides the core configuration system for the trading agent,
with comprehensive validation and type checking using Pydantic.
"""

from pathlib import Path

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class IBKRConfig(BaseModel):
    """Interactive Brokers connection configuration."""

    host: str = Field(default="127.0.0.1", description="IBKR Gateway/TWS host")
    port: int = Field(default=7497, ge=1, le=65535, description="IBKR API port")
    client_id: int = Field(default=1, ge=0, description="Unique client identifier")
    account: str | None = Field(default=None, description="IBKR account number")
    timeout: int = Field(
        default=60, ge=5, le=300, description="Connection timeout in seconds"
    )


class RiskLimits(BaseModel):
    """Risk management limits and circuit breakers."""

    max_daily_loss: float = Field(
        default=-0.02,
        ge=-1.0,
        le=0.0,
        description="Maximum daily loss before circuit breaker (as percentage)",
    )
    max_position_loss: float = Field(
        default=-500.0,
        le=0.0,
        description="Maximum loss per position before stop loss ($)",
    )
    max_portfolio_var_95: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Maximum portfolio Value at Risk (95% confidence)",
    )
    max_sector_concentration: float = Field(
        default=0.30,
        ge=0.0,
        le=1.0,
        description="Maximum exposure to any single sector",
    )
    max_correlation: float = Field(
        default=0.70,
        ge=-1.0,
        le=1.0,
        description="Maximum correlation between positions",
    )
    max_margin_utilization: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Maximum margin utilization allowed",
    )
    max_positions_per_day: int = Field(
        default=10, ge=1, le=100, description="Maximum new positions per day"
    )


class LearningConfig(BaseModel):
    """Self-learning engine configuration."""

    enabled: bool = Field(default=True, description="Enable learning engine")
    min_trades_for_learning: int = Field(
        default=30,
        ge=10,
        description="Minimum trades before pattern detection starts",
    )
    min_samples_for_pattern: int = Field(
        default=30, ge=5, description="Minimum samples to validate a pattern"
    )
    experiment_allocation: float = Field(
        default=0.20,
        ge=0.0,
        le=1.0,
        description="Percentage of trades allocated to experiments",
    )
    confidence_threshold: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Minimum confidence for pattern validity",
    )
    p_value_threshold: float = Field(
        default=0.05,
        ge=0.0,
        le=1.0,
        description="Maximum p-value for statistical significance",
    )
    min_effect_size: float = Field(
        default=0.005,
        ge=0.0,
        description="Minimum effect size (0.5%) to adopt changes",
    )


class Config(BaseSettings):
    """Main application configuration with validation.

    This class loads configuration from environment variables and provides
    validated settings for all system components.

    Example:
        >>> config = Config()
        >>> print(config.ibkr.host)
        '127.0.0.1'
        >>> print(config.paper_trading)
        True
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # IBKR Configuration
    ibkr_host: str = Field(default="127.0.0.1")
    ibkr_port: int = Field(default=7497)
    ibkr_client_id: int = Field(default=1)
    ibkr_account: str | None = Field(default=None)

    # Anthropic API
    anthropic_api_key: str = Field(description="Anthropic API key for Claude")

    # Database
    database_url: str = Field(
        default="postgresql://localhost/trading_agent",
        description="Database connection URL (PostgreSQL for production, SQLite for testing)",
    )

    # Trading Settings
    paper_trading: bool = Field(default=True, description="Paper trading mode")

    # Risk Limits
    max_daily_loss: float = Field(default=-0.02)
    max_position_loss: float = Field(default=-500.0)
    max_position_size: float = Field(
        default=5000.0, description="Maximum $ per position"
    )

    # --- Margin & Portfolio Budget ---
    max_total_margin: float = Field(
        default=100000.0,
        description="Absolute ceiling on total margin across all staged trades",
    )
    margin_budget_pct: float = Field(
        default=0.50,
        ge=0.0,
        le=1.0,
        description="Fraction of NLV to use as margin budget",
    )
    margin_budget_default: float = Field(
        default=50000.0,
        description="Fallback margin budget when IBKR is offline",
    )

    # --- Position Limits ---
    max_positions: int = Field(
        default=10, ge=1, description="Maximum number of open positions"
    )
    max_positions_per_day: int = Field(
        default=10, ge=1, description="Maximum new positions per day"
    )
    max_sector_count: int = Field(
        default=3, ge=1, description="Maximum positions in any single sector"
    )

    # --- Premium & Pricing ---
    premium_min: float = Field(
        default=0.30, description="Minimum acceptable premium per share"
    )
    premium_max: float = Field(
        default=2.00, description="Maximum preferred premium per share"
    )
    premium_target: float = Field(
        default=0.40, description="Ideal premium target per share"
    )
    premium_floor: float = Field(
        default=0.20,
        description="Absolute floor after limit price adjustments",
    )
    price_adjustment_increment: float = Field(
        default=0.01,
        description="Amount to decrease limit per adjustment",
    )
    max_price_adjustments: int = Field(
        default=2, ge=0, description="Maximum number of price adjustments"
    )

    # --- Risk Governor (previously hardcoded) ---
    max_margin_per_trade_pct: float = Field(
        default=0.10,
        ge=0.0,
        le=1.0,
        description="Max margin per single trade as % of net liquidation",
    )
    max_margin_utilization: float = Field(
        default=0.80,
        ge=0.0,
        le=1.0,
        description="Maximum margin utilization allowed",
    )
    max_weekly_loss_pct: float = Field(
        default=-0.05,
        ge=-1.0,
        le=0.0,
        description="Maximum weekly loss before circuit breaker",
    )
    max_drawdown_pct: float = Field(
        default=-0.10,
        ge=-1.0,
        le=0.0,
        description="Maximum peak-to-trough drawdown before circuit breaker",
    )

    # Learning Settings
    learning_enabled: bool = Field(default=True)
    min_trades_for_learning: int = Field(default=30)
    experiment_allocation: float = Field(default=0.20)

    # --- TAAD Flex Query (per-account, up to 3 accounts) ---
    ibkr_flex_token_1: str | None = Field(default=None, description="Flex Web Service token for account 1")
    ibkr_flex_query_id_1: str | None = Field(default=None, description="Flex Query ID for account 1")
    ibkr_flex_account_1: str | None = Field(default=None, description="Account ID for Flex account 1")
    ibkr_flex_token_2: str | None = Field(default=None, description="Flex Web Service token for account 2")
    ibkr_flex_query_id_2: str | None = Field(default=None, description="Flex Query ID for account 2")
    ibkr_flex_account_2: str | None = Field(default=None, description="Account ID for Flex account 2")
    ibkr_flex_token_3: str | None = Field(default=None, description="Flex Web Service token for account 3")
    ibkr_flex_query_id_3: str | None = Field(default=None, description="Flex Query ID for account 3")
    ibkr_flex_account_3: str | None = Field(default=None, description="Account ID for Flex account 3")

    # --- TAAD Activity Flex Query IDs (per account, for historical imports) ---
    ibkr_flex_activity_query_last_month_1: str | None = Field(default=None, description="Activity Flex Query ID (Last Month) for account 1")
    ibkr_flex_activity_query_last_quarter_1: str | None = Field(default=None, description="Activity Flex Query ID (Last Quarter) for account 1")
    ibkr_flex_activity_query_last_year_1: str | None = Field(default=None, description="Activity Flex Query ID (Last Year) for account 1")
    ibkr_flex_activity_query_last_month_2: str | None = Field(default=None, description="Activity Flex Query ID (Last Month) for account 2")
    ibkr_flex_activity_query_last_quarter_2: str | None = Field(default=None, description="Activity Flex Query ID (Last Quarter) for account 2")
    ibkr_flex_activity_query_last_year_2: str | None = Field(default=None, description="Activity Flex Query ID (Last Year) for account 2")
    ibkr_flex_activity_query_last_month_3: str | None = Field(default=None, description="Activity Flex Query ID (Last Month) for account 3")
    ibkr_flex_activity_query_last_quarter_3: str | None = Field(default=None, description="Activity Flex Query ID (Last Quarter) for account 3")
    ibkr_flex_activity_query_last_year_3: str | None = Field(default=None, description="Activity Flex Query ID (Last Year) for account 3")

    # Logging
    log_level: str = Field(default="INFO", description="Logging level")
    log_file: str = Field(default="logs/app.log", description="Log file path")

    @field_validator("anthropic_api_key")
    @classmethod
    def validate_api_key(cls, v: str) -> str:
        """Validate Anthropic API key format."""
        if not v or v == "your_key_here":
            raise ValueError("Valid Anthropic API key required")
        if not v.startswith("sk-ant-"):
            raise ValueError("Invalid Anthropic API key format")
        return v

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        """Validate log level is one of the accepted values."""
        valid_levels = ["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
        v_upper = v.upper()
        if v_upper not in valid_levels:
            raise ValueError(f"Log level must be one of: {', '.join(valid_levels)}")
        return v_upper

    @property
    def ibkr(self) -> IBKRConfig:
        """Get IBKR configuration."""
        return IBKRConfig(
            host=self.ibkr_host,
            port=self.ibkr_port,
            client_id=self.ibkr_client_id,
            account=self.ibkr_account,
        )

    @property
    def risk_limits(self) -> RiskLimits:
        """Get risk limit configuration."""
        return RiskLimits(
            max_daily_loss=self.max_daily_loss,
            max_position_loss=self.max_position_loss,
            max_margin_utilization=self.max_margin_utilization,
            max_positions_per_day=self.max_positions_per_day,
        )

    @property
    def learning(self) -> LearningConfig:
        """Get learning configuration."""
        return LearningConfig(
            enabled=self.learning_enabled,
            min_trades_for_learning=self.min_trades_for_learning,
            experiment_allocation=self.experiment_allocation,
        )

    def get_flex_credentials(self, account_id: str | None = None) -> dict[str, str] | None:
        """Get Flex Query credentials for a given account.

        Args:
            account_id: IBKR account ID (e.g., 'YOUR_ACCOUNT'). If None, returns first configured account.

        Returns:
            Dict with 'token', 'query_id', 'account_id' or None if not found.
        """
        slots = [
            (self.ibkr_flex_token_1, self.ibkr_flex_query_id_1, self.ibkr_flex_account_1),
            (self.ibkr_flex_token_2, self.ibkr_flex_query_id_2, self.ibkr_flex_account_2),
            (self.ibkr_flex_token_3, self.ibkr_flex_query_id_3, self.ibkr_flex_account_3),
        ]
        for token, query_id, acct in slots:
            if token and query_id and acct:
                if account_id is None or acct == account_id:
                    return {"token": token, "query_id": query_id, "account_id": acct}
        return None

    def get_flex_credentials_for_query(
        self, query: str, account_id: str | None = None
    ) -> dict[str, str] | None:
        """Get Flex Query credentials for a specific query type.

        For 'daily' queries, returns the Trade Confirmation query ID.
        For 'last_month', 'last_quarter', 'last_year', returns the
        corresponding Activity Flex Query ID.

        Args:
            query: Query type - 'daily' (trade confirmation), 'last_month',
                   'last_quarter', 'last_year' (activity queries).
            account_id: Filter to specific account. If None, returns first match.

        Returns:
            Dict with 'token', 'query_id', 'account_id' or None if not found.
        """
        if query == "daily":
            return self.get_flex_credentials(account_id)

        # Activity query lookup — reuses the same token per account slot
        activity_slots = [
            {
                "token": self.ibkr_flex_token_1,
                "account": self.ibkr_flex_account_1,
                "last_month": self.ibkr_flex_activity_query_last_month_1,
                "last_quarter": self.ibkr_flex_activity_query_last_quarter_1,
                "last_year": self.ibkr_flex_activity_query_last_year_1,
            },
            {
                "token": self.ibkr_flex_token_2,
                "account": self.ibkr_flex_account_2,
                "last_month": self.ibkr_flex_activity_query_last_month_2,
                "last_quarter": self.ibkr_flex_activity_query_last_quarter_2,
                "last_year": self.ibkr_flex_activity_query_last_year_2,
            },
            {
                "token": self.ibkr_flex_token_3,
                "account": self.ibkr_flex_account_3,
                "last_month": self.ibkr_flex_activity_query_last_month_3,
                "last_quarter": self.ibkr_flex_activity_query_last_quarter_3,
                "last_year": self.ibkr_flex_activity_query_last_year_3,
            },
        ]

        for slot in activity_slots:
            if slot["token"] and slot["account"]:
                if account_id is None or slot["account"] == account_id:
                    query_id = slot.get(query)
                    if query_id:
                        return {
                            "token": slot["token"],
                            "query_id": query_id,
                            "account_id": slot["account"],
                        }
        return None

    def list_flex_accounts(self) -> list[str]:
        """List all configured Flex Query account IDs."""
        accounts = []
        slots = [
            (self.ibkr_flex_token_1, self.ibkr_flex_query_id_1, self.ibkr_flex_account_1),
            (self.ibkr_flex_token_2, self.ibkr_flex_query_id_2, self.ibkr_flex_account_2),
            (self.ibkr_flex_token_3, self.ibkr_flex_query_id_3, self.ibkr_flex_account_3),
        ]
        for token, query_id, acct in slots:
            if token and query_id and acct:
                accounts.append(acct)
        return accounts

    def ensure_directories(self) -> None:
        """Ensure all required directories exist."""
        directories = [
            "data/databases",
            "data/cache",
            "data/exports",
            "logs",
        ]

        for directory in directories:
            Path(directory).mkdir(parents=True, exist_ok=True)


# Global config instance
_config: Config | None = None


def get_config() -> Config:
    """Get or create the global configuration instance.

    Returns:
        Config: The global configuration object

    Example:
        >>> config = get_config()
        >>> print(config.paper_trading)
        True
    """
    global _config
    if _config is None:
        _config = Config()
        _config.ensure_directories()
    return _config


def reset_config() -> None:
    """Reset the global configuration instance.

    Useful for testing when you need to reload configuration.
    """
    global _config
    _config = None
