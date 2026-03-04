"""Trade decision context snapshot dataclasses.

This module defines dataclasses for capturing complete market and decision
context at trade time, enabling the learning engine to analyze patterns.
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class MarketContext:
    """Market-wide context at time of trade.

    Attributes:
        timestamp: When context was captured
        spy_price: SPY (S&P 500 ETF) price
        spy_change_pct: Daily change percentage for SPY
        qqq_price: QQQ (Nasdaq-100 ETF) price
        qqq_change_pct: Daily change percentage for QQQ
        vix: VIX (volatility index) level
        vix_change_pct: Daily change percentage for VIX
        advance_decline_ratio: Market breadth (advancing/declining stocks)
        new_highs: Number of stocks at 52-week highs
        new_lows: Number of stocks at 52-week lows
        sector_leaders: Top performing sectors [(sector, change_pct), ...]
        sector_laggards: Bottom performing sectors [(sector, change_pct), ...]
    """

    timestamp: datetime
    spy_price: float
    spy_change_pct: float
    qqq_price: float
    qqq_change_pct: float
    vix: float
    vix_change_pct: float
    advance_decline_ratio: float | None = None
    new_highs: int | None = None
    new_lows: int | None = None
    sector_leaders: list[tuple[str, float]] = field(default_factory=list)
    sector_laggards: list[tuple[str, float]] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "spy_price": self.spy_price,
            "spy_change_pct": self.spy_change_pct,
            "qqq_price": self.qqq_price,
            "qqq_change_pct": self.qqq_change_pct,
            "vix": self.vix,
            "vix_change_pct": self.vix_change_pct,
            "advance_decline_ratio": self.advance_decline_ratio,
            "new_highs": self.new_highs,
            "new_lows": self.new_lows,
            "sector_leaders": self.sector_leaders,
            "sector_laggards": self.sector_laggards,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MarketContext":
        """Create from dictionary (JSON deserialization)."""
        return cls(
            timestamp=datetime.fromisoformat(data["timestamp"]),
            spy_price=data["spy_price"],
            spy_change_pct=data["spy_change_pct"],
            qqq_price=data["qqq_price"],
            qqq_change_pct=data["qqq_change_pct"],
            vix=data["vix"],
            vix_change_pct=data["vix_change_pct"],
            advance_decline_ratio=data.get("advance_decline_ratio"),
            new_highs=data.get("new_highs"),
            new_lows=data.get("new_lows"),
            sector_leaders=data.get("sector_leaders", []),
            sector_laggards=data.get("sector_laggards", []),
        )


@dataclass
class UnderlyingContext:
    """Context for the specific underlying at trade time.

    Attributes:
        symbol: Stock ticker symbol
        timestamp: When context was captured
        current_price: Current stock price
        open_price: Opening price for the day
        high_price: High price for the day
        low_price: Low price for the day
        previous_close: Previous day's closing price
        sma_20: 20-day simple moving average
        sma_50: 50-day simple moving average
        trend_direction: Trend classification (uptrend, downtrend, sideways)
        trend_strength: Trend strength on 0-1 scale
        iv_rank: Implied volatility rank (0-100 percentile)
        iv_percentile: Implied volatility percentile
        historical_vol_20d: 20-day historical volatility
        volume: Current day volume
        avg_volume_20d: 20-day average volume
        relative_volume: volume / avg_volume
        support_levels: Identified support price levels
        resistance_levels: Identified resistance price levels
    """

    symbol: str
    timestamp: datetime
    current_price: float
    open_price: float
    high_price: float
    low_price: float
    previous_close: float
    sma_20: float | None = None
    sma_50: float | None = None
    trend_direction: str = "unknown"
    trend_strength: float = 0.0
    iv_rank: float | None = None
    iv_percentile: float | None = None
    historical_vol_20d: float | None = None
    volume: int = 0
    avg_volume_20d: int = 0
    relative_volume: float = 0.0
    support_levels: list[float] = field(default_factory=list)
    resistance_levels: list[float] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "symbol": self.symbol,
            "timestamp": self.timestamp.isoformat(),
            "current_price": self.current_price,
            "open_price": self.open_price,
            "high_price": self.high_price,
            "low_price": self.low_price,
            "previous_close": self.previous_close,
            "sma_20": self.sma_20,
            "sma_50": self.sma_50,
            "trend_direction": self.trend_direction,
            "trend_strength": self.trend_strength,
            "iv_rank": self.iv_rank,
            "iv_percentile": self.iv_percentile,
            "historical_vol_20d": self.historical_vol_20d,
            "volume": self.volume,
            "avg_volume_20d": self.avg_volume_20d,
            "relative_volume": self.relative_volume,
            "support_levels": self.support_levels,
            "resistance_levels": self.resistance_levels,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "UnderlyingContext":
        """Create from dictionary (JSON deserialization)."""
        return cls(
            symbol=data["symbol"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            current_price=data["current_price"],
            open_price=data["open_price"],
            high_price=data["high_price"],
            low_price=data["low_price"],
            previous_close=data["previous_close"],
            sma_20=data.get("sma_20"),
            sma_50=data.get("sma_50"),
            trend_direction=data.get("trend_direction", "unknown"),
            trend_strength=data.get("trend_strength", 0.0),
            iv_rank=data.get("iv_rank"),
            iv_percentile=data.get("iv_percentile"),
            historical_vol_20d=data.get("historical_vol_20d"),
            volume=data.get("volume", 0),
            avg_volume_20d=data.get("avg_volume_20d", 0),
            relative_volume=data.get("relative_volume", 0.0),
            support_levels=data.get("support_levels", []),
            resistance_levels=data.get("resistance_levels", []),
        )


@dataclass
class DecisionContext:
    """Complete context captured at trade decision time.

    Attributes:
        decision_id: Unique identifier for this decision
        timestamp: When decision was made
        market: Market-wide context
        underlying: Underlying-specific context
        strategy_params: Strategy parameters at decision time
        ai_confidence_score: AI confidence score (if using AI)
        ai_reasoning: AI reasoning text (if using AI)
        source: Source of opportunity (manual, barchart, etc.)
        rank_position: Position in ranked list (1-based)
        rank_score: Ranking score
        rank_factors: Factors contributing to ranking
    """

    decision_id: str
    timestamp: datetime
    market: MarketContext
    underlying: UnderlyingContext
    strategy_params: dict
    source: str
    rank_position: int
    rank_score: float
    rank_factors: dict = field(default_factory=dict)
    ai_confidence_score: float | None = None
    ai_reasoning: str | None = None

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "decision_id": self.decision_id,
            "timestamp": self.timestamp.isoformat(),
            "market": self.market.to_dict(),
            "underlying": self.underlying.to_dict(),
            "strategy_params": self.strategy_params,
            "source": self.source,
            "rank_position": self.rank_position,
            "rank_score": self.rank_score,
            "rank_factors": self.rank_factors,
            "ai_confidence_score": self.ai_confidence_score,
            "ai_reasoning": self.ai_reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DecisionContext":
        """Create from dictionary (JSON deserialization)."""
        return cls(
            decision_id=data["decision_id"],
            timestamp=datetime.fromisoformat(data["timestamp"]),
            market=MarketContext.from_dict(data["market"]),
            underlying=UnderlyingContext.from_dict(data["underlying"]),
            strategy_params=data["strategy_params"],
            source=data["source"],
            rank_position=data["rank_position"],
            rank_score=data["rank_score"],
            rank_factors=data.get("rank_factors", {}),
            ai_confidence_score=data.get("ai_confidence_score"),
            ai_reasoning=data.get("ai_reasoning"),
        )
