"""Candidate data models for trade opportunities from various sources.

This module defines dataclasses for representing trade candidates from
different sources (Barchart, IBKR, manual entry) before validation.
"""

from dataclasses import dataclass, field
from datetime import date


@dataclass
class BarchartCandidate:
    """Parsed candidate from Barchart CSV export.

    Represents a naked put option opportunity exported from Barchart.com's
    options screener. All percentage fields are stored as decimals
    (e.g., 0.1153 for 11.53%).

    Attributes:
        symbol: Stock ticker symbol
        expiration: Option expiration date
        strike: Strike price
        option_type: Option type (always "PUT" for naked put screener)
        underlying_price: Current underlying stock price
        bid: Option bid price (premium receivable)
        dte: Days to expiration
        moneyness_pct: Out-of-the-money percentage (negative = OTM)
        breakeven: Breakeven price based on bid
        breakeven_pct: Breakeven as % from current price
        volume: Daily option volume
        open_interest: Open interest
        iv_rank: IV Rank as decimal (e.g., 0.4481 for 44.81%)
        delta: Option delta (negative for puts)
        premium_return_pct: Premium return percentage
        annualized_return_pct: Annualized return percentage
        profit_probability: Probability of profit (e.g., 0.8554 for 85.54%)
        source: Data source identifier (default: "barchart_csv")
        raw_row: Original CSV row for debugging
    """

    # Contract identification
    symbol: str
    expiration: date
    strike: float
    option_type: str  # Always "PUT"

    # Underlying data
    underlying_price: float

    # Option metrics from Barchart
    bid: float
    dte: int
    moneyness_pct: float  # e.g., -0.1153 for -11.53%
    breakeven: float
    breakeven_pct: float  # e.g., -0.1242 for -12.42%

    # Liquidity
    volume: int
    open_interest: int

    # Volatility & Greeks
    iv_rank: float  # e.g., 0.4481 for 44.81%
    delta: float  # Negative for puts, e.g., -0.143753

    # Return metrics
    premium_return_pct: float  # e.g., 0.01 for 1.0%
    annualized_return_pct: float
    profit_probability: float  # e.g., 0.8554 for 85.54%

    # Metadata
    source: str = "barchart_csv"
    raw_row: dict | None = field(default=None, repr=False)

    def to_dict(self) -> dict:
        """Convert to dictionary representation.

        Returns:
            Dictionary with all fields serialized
        """
        return {
            "symbol": self.symbol,
            "expiration": self.expiration.isoformat(),
            "strike": self.strike,
            "option_type": self.option_type,
            "underlying_price": self.underlying_price,
            "bid": self.bid,
            "dte": self.dte,
            "moneyness_pct": self.moneyness_pct,
            "breakeven": self.breakeven,
            "breakeven_pct": self.breakeven_pct,
            "volume": self.volume,
            "open_interest": self.open_interest,
            "iv_rank": self.iv_rank,
            "delta": self.delta,
            "premium_return_pct": self.premium_return_pct,
            "annualized_return_pct": self.annualized_return_pct,
            "profit_probability": self.profit_probability,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "BarchartCandidate":
        """Create BarchartCandidate from dictionary.

        Args:
            data: Dictionary with candidate data

        Returns:
            BarchartCandidate instance
        """
        # Convert expiration string to date if needed
        expiration = data["expiration"]
        if isinstance(expiration, str):
            from datetime import datetime

            expiration = datetime.fromisoformat(expiration).date()

        return cls(
            symbol=data["symbol"],
            expiration=expiration,
            strike=data["strike"],
            option_type=data["option_type"],
            underlying_price=data["underlying_price"],
            bid=data["bid"],
            dte=data["dte"],
            moneyness_pct=data["moneyness_pct"],
            breakeven=data["breakeven"],
            breakeven_pct=data["breakeven_pct"],
            volume=data["volume"],
            open_interest=data["open_interest"],
            iv_rank=data["iv_rank"],
            delta=data["delta"],
            premium_return_pct=data["premium_return_pct"],
            annualized_return_pct=data["annualized_return_pct"],
            profit_probability=data["profit_probability"],
            source=data.get("source", "barchart_csv"),
        )
