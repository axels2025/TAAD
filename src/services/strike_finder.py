"""Strike selection data models and preferences.

Provides StrikeCandidate and StrikePreferences dataclasses used by
the portfolio builder and IBKR scanner pipeline.
"""

import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol

from loguru import logger


@dataclass
class StrikePreferences:
    """User-configurable strike selection preferences.

    All values loaded from environment variables with sensible defaults.
    These preferences guide the strike finder in selecting optimal strikes.

    Attributes:
        min_premium: Minimum acceptable premium (default $0.30)
        max_premium: Maximum preferred premium (default $0.60)
        target_premium: Ideal premium target (default $0.40)
        min_otm_pct: Minimum OTM percentage (default 15%)
        target_otm_pct: Ideal OTM percentage (default 20%)
        rare_min_otm_pct: Absolute minimum for rare cases (default 10%)
        max_dte: Maximum days to expiration (default 14)
        target_dte: Ideal DTE - same-week Friday (default 7)
        contract_price_threshold: Price above which max contracts reduced (default $90)
        contract_max_expensive: Max contracts for expensive stocks (default 3)
        contract_max_cheap: Max contracts for cheaper stocks (default 5)
        allow_higher_premium_if_further_otm: Allow $0.60+ if OTM > 25% (default True)
    """

    min_premium: float = 0.30
    max_premium: float = 0.60
    target_premium: float = 0.40
    min_otm_pct: float = 0.15
    target_otm_pct: float = 0.20
    rare_min_otm_pct: float = 0.10
    max_dte: int = 14
    target_dte: int = 7
    contract_price_threshold: float = 90.0
    contract_max_expensive: int = 3
    contract_max_cheap: int = 5
    allow_higher_premium_if_further_otm: bool = True

    @classmethod
    def from_env(cls) -> "StrikePreferences":
        """Load preferences from the central Config singleton.

        Premium values come from ``get_config()`` so there is one source
        of truth.  OTM/DTE/contract-sizing values stay as ``os.getenv``
        since they are only read here.

        Returns:
            StrikePreferences instance
        """
        from src.config.base import get_config

        cfg = get_config()
        return cls(
            min_premium=cfg.premium_min,
            max_premium=cfg.premium_max,
            target_premium=cfg.premium_target,
            min_otm_pct=float(os.getenv("OTM_MIN_PCT", "0.15")),
            target_otm_pct=float(os.getenv("OTM_TARGET_PCT", "0.20")),
            rare_min_otm_pct=float(os.getenv("OTM_RARE_MIN_PCT", "0.10")),
            max_dte=int(os.getenv("DTE_MAX", "14")),
            target_dte=int(os.getenv("DTE_TARGET", "7")),
            contract_price_threshold=float(os.getenv("CONTRACT_PRICE_THRESHOLD", "90.0")),
            contract_max_expensive=int(os.getenv("CONTRACT_MAX_EXPENSIVE", "3")),
            contract_max_cheap=int(os.getenv("CONTRACT_MAX_CHEAP", "5")),
            allow_higher_premium_if_further_otm=os.getenv(
                "ALLOW_HIGHER_PREMIUM_IF_FURTHER_OTM", "true"
            ).lower() == "true",
        )


@dataclass
class StrikeCandidate:
    """A potential strike price with full details.

    Represents a candidate strike price for a naked put trade,
    including all relevant metrics for ranking and selection.

    Attributes:
        symbol: Stock ticker symbol
        stock_price: Current stock price
        strike: Strike price of the option
        expiration: Option expiration date
        dte: Days to expiration
        bid: Current bid price
        ask: Current ask price
        mid: Mid price ((bid + ask) / 2)
        suggested_limit: Calculated limit price (between bid and mid)
        otm_pct: Out-of-the-money percentage (e.g., 0.20 = 20%)
        delta: Option delta (negative for puts)
        iv: Implied volatility
        iv_rank: IV rank (percentile)
        volume: Daily option volume
        open_interest: Open interest
        margin_estimate: Estimated margin from formula
        margin_actual: Actual margin from IBKR (None if not checked)
        contracts: Recommended number of contracts
        total_margin: margin * contracts
        premium_income: suggested_limit * 100 * contracts
        margin_efficiency: premium_income / total_margin
        sector: Stock sector
        score: Composite ranking score (0-100)
        source: Data source ('ibkr' or 'barchart')
    """

    symbol: str
    stock_price: float
    strike: float
    expiration: date
    dte: int
    bid: float
    ask: float
    mid: float
    suggested_limit: float
    otm_pct: float
    delta: float
    iv: float
    iv_rank: float
    volume: int
    open_interest: int
    margin_estimate: float
    margin_actual: float | None
    contracts: int
    total_margin: float
    premium_income: float
    margin_efficiency: float
    sector: str = "Unknown"
    score: float = 0.0
    source: str = "ibkr"

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "symbol": self.symbol,
            "stock_price": self.stock_price,
            "strike": self.strike,
            "expiration": self.expiration.isoformat(),
            "dte": self.dte,
            "bid": self.bid,
            "ask": self.ask,
            "mid": self.mid,
            "suggested_limit": self.suggested_limit,
            "otm_pct": self.otm_pct,
            "delta": self.delta,
            "iv": self.iv,
            "iv_rank": self.iv_rank,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "margin_estimate": self.margin_estimate,
            "margin_actual": self.margin_actual,
            "contracts": self.contracts,
            "total_margin": self.total_margin,
            "premium_income": self.premium_income,
            "margin_efficiency": self.margin_efficiency,
            "sector": self.sector,
            "score": self.score,
            "source": self.source,
        }

    @property
    def _margin_sanity_floor(self) -> float:
        """Minimum believable margin per contract (5% of notional).

        IBKR's whatIfOrder sometimes returns near-zero values after hours.
        Any margin below this floor is treated as unreliable.
        """
        return 0.05 * self.strike * 100

    @property
    def effective_margin(self) -> float:
        """Get the effective margin (actual if available, else estimate).

        Never returns 0 — falls back to Reg-T minimum (10% of notional).
        Rejects IBKR margin values below the sanity floor.
        """
        floor = self._margin_sanity_floor
        if self.margin_actual and self.margin_actual >= floor:
            return self.margin_actual
        if self.margin_estimate and self.margin_estimate > 0:
            return self.margin_estimate
        return 0.10 * self.stock_price * 100

    @property
    def margin_source(self) -> str:
        """Get the margin source ('ibkr_whatif' or 'estimated')."""
        floor = self._margin_sanity_floor
        if self.margin_actual and self.margin_actual >= floor:
            return "ibkr_whatif"
        return "estimated"


class IBKRClientProtocol(Protocol):
    """Protocol for IBKR client dependency injection."""

    def get_stock_price(self, symbol: str) -> float | None:
        """Get current stock price."""
        ...

    def get_option_chain(
        self, symbol: str, expiration: date
    ) -> list[dict] | None:
        """Get option chain for symbol and expiration."""
        ...

    def get_actual_margin(
        self, symbol: str, strike: float, expiration: date
    ) -> float | None:
        """Get actual margin requirement via whatIfOrder."""
        ...
