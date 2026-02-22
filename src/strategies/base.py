"""Base strategy interface for trading strategies.

This module defines the abstract interface that all trading strategies
must implement. This ensures consistent behavior across different strategies.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class TradeOpportunity:
    """Represents a potential trade opportunity.

    Attributes:
        symbol: Stock ticker symbol
        strike: Option strike price
        expiration: Option expiration date
        option_type: Option type (PUT or CALL)
        premium: Premium per share
        contracts: Number of contracts
        otm_pct: Percentage out-of-the-money
        dte: Days to expiration
        stock_price: Current stock price
        trend: Market trend (uptrend, downtrend, sideways)
        sector: Stock sector
        confidence: Strategy confidence score (0-1)
        reasoning: Why this trade was selected
        margin_required: Estimated margin requirement
        margin_efficiency_pct: Margin efficiency as percentage (premium/margin * 100)
        margin_efficiency_ratio: Margin efficiency as ratio (e.g., "1:12")
    """

    symbol: str
    strike: float
    expiration: datetime
    option_type: str
    premium: float
    contracts: int
    otm_pct: float
    dte: int
    stock_price: float
    trend: str
    sector: str | None = None
    confidence: float = 0.0
    reasoning: str = ""
    margin_required: float = 0.0
    margin_efficiency_pct: float = 0.0
    margin_efficiency_ratio: str = ""

    def calculate_margin_efficiency(self) -> None:
        """Calculate margin efficiency ratio and percentage.

        Your manual check: $400 premium with $4000-$8000 margin = 5-10%
        This is equivalent to 1:10 to 1:20 ratio.

        Formula:
        - margin_efficiency_pct = (premium * 100 / margin_required) * 100
        - margin_efficiency_ratio = "1:{ratio:.0f}" where ratio = margin / (premium * 100)
        """
        if self.margin_required > 0 and self.premium > 0:
            # Premium is per share, need to multiply by 100 for contract value
            premium_value = self.premium * 100
            self.margin_efficiency_pct = (premium_value / self.margin_required) * 100
            ratio = self.margin_required / premium_value
            self.margin_efficiency_ratio = f"1:{ratio:.0f}"
        else:
            self.margin_efficiency_pct = 0.0
            self.margin_efficiency_ratio = "N/A"

    def to_dict(self) -> dict:
        """Convert to dictionary representation."""
        return {
            "symbol": self.symbol,
            "strike": self.strike,
            "expiration": self.expiration.isoformat(),
            "option_type": self.option_type,
            "premium": self.premium,
            "contracts": self.contracts,
            "otm_pct": self.otm_pct,
            "dte": self.dte,
            "stock_price": self.stock_price,
            "trend": self.trend,
            "sector": self.sector,
            "confidence": self.confidence,
            "reasoning": self.reasoning,
            "margin_required": self.margin_required,
            "margin_efficiency_pct": self.margin_efficiency_pct,
            "margin_efficiency_ratio": self.margin_efficiency_ratio,
        }


@dataclass
class ExitSignal:
    """Represents an exit signal for a position.

    Attributes:
        should_exit: Whether position should be exited
        reason: Reason for exit (profit_target, stop_loss, time_exit, manual)
        confidence: Confidence in exit decision (0-1)
        current_premium: Current option premium
        profit_pct: Current profit percentage
    """

    should_exit: bool
    reason: str
    confidence: float = 1.0
    current_premium: float = 0.0
    profit_pct: float = 0.0


class BaseStrategy(ABC):
    """Abstract base class for trading strategies.

    All trading strategies must inherit from this class and implement
    the required methods for finding opportunities and managing exits.
    """

    @abstractmethod
    def find_opportunities(self, max_results: int = 10) -> list[TradeOpportunity]:
        """Find trade opportunities matching strategy criteria.

        Args:
            max_results: Maximum number of opportunities to return

        Returns:
            list[TradeOpportunity]: List of trade opportunities sorted by quality

        Example:
            >>> strategy = NakedPutStrategy()
            >>> opportunities = strategy.find_opportunities(max_results=5)
            >>> for opp in opportunities:
            ...     print(f"{opp.symbol}: ${opp.premium}")
        """
        pass

    @abstractmethod
    def should_enter_trade(self, opportunity: TradeOpportunity) -> bool:
        """Validate if a trade opportunity meets entry criteria.

        Args:
            opportunity: Trade opportunity to validate

        Returns:
            bool: True if trade should be entered

        Example:
            >>> strategy = NakedPutStrategy()
            >>> opp = TradeOpportunity(...)
            >>> if strategy.should_enter_trade(opp):
            ...     execute_trade(opp)
        """
        pass

    @abstractmethod
    def should_exit_trade(
        self,
        entry_premium: float,
        current_premium: float,
        current_dte: int,
        entry_date: datetime,
    ) -> ExitSignal:
        """Determine if a position should be exited.

        Args:
            entry_premium: Premium received at entry
            current_premium: Current option premium
            current_dte: Current days to expiration
            entry_date: Date position was entered

        Returns:
            ExitSignal: Exit signal with reason and confidence

        Example:
            >>> strategy = NakedPutStrategy()
            >>> signal = strategy.should_exit_trade(0.50, 0.25, 5, entry_date)
            >>> if signal.should_exit:
            ...     print(f"Exit reason: {signal.reason}")
        """
        pass

    @abstractmethod
    def get_position_size(self, opportunity: TradeOpportunity) -> int:
        """Calculate position size for a trade opportunity.

        Args:
            opportunity: Trade opportunity

        Returns:
            int: Number of contracts to trade

        Example:
            >>> strategy = NakedPutStrategy()
            >>> size = strategy.get_position_size(opportunity)
            >>> print(f"Trade {size} contracts")
        """
        pass

    @abstractmethod
    def validate_configuration(self) -> bool:
        """Validate strategy configuration is correct.

        Returns:
            bool: True if configuration is valid

        Raises:
            ValueError: If configuration is invalid

        Example:
            >>> strategy = NakedPutStrategy()
            >>> strategy.validate_configuration()
            True
        """
        pass
