"""Naked put strategy implementation.

This module implements the user's proven naked put selling strategy,
replicating their manual approach exactly before any learning-based improvements.
"""

from datetime import datetime

from loguru import logger

from src.config.baseline_strategy import BaselineStrategy, get_baseline_strategy
from src.strategies.base import BaseStrategy, ExitSignal, TradeOpportunity
from src.tools.ibkr_client import IBKRClient
from src.tools.options_finder import OptionsFinder
from src.tools.screener import StockScreener
from src.utils.calc import fmt_pct


class NakedPutStrategy(BaseStrategy):
    """Naked put selling strategy implementation.

    This strategy sells weekly put options on stocks in uptrend, targeting
    consistent premium income with defined risk management rules.

    Strategy Parameters:
        - OTM Range: 15-20% below current price
        - Premium: $0.30-$0.50 per share
        - DTE: 7-14 days to expiration
        - Position Size: 5 contracts per trade
        - Max Positions: 10 concurrent positions
        - Trend Filter: Only uptrend stocks (Price > 20 EMA > 50 EMA)

    Exit Rules:
        - Profit Target: 50% of max profit
        - Stop Loss: -200% of premium received
        - Time Exit: 3 days before expiration

    Example:
        >>> from src.tools.ibkr_client import IBKRClient
        >>> from src.config.base import IBKRConfig
        >>> config = IBKRConfig()
        >>> client = IBKRClient(config)
        >>> client.connect()
        >>> strategy = NakedPutStrategy(client)
        >>> opportunities = strategy.find_opportunities(max_results=5)
        >>> for opp in opportunities:
        ...     print(f"{opp.symbol}: ${opp.premium} @ {opp.strike}")
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        config: BaselineStrategy | None = None,
    ):
        """Initialize naked put strategy.

        Args:
            ibkr_client: Connected IBKR client
            config: Strategy configuration (defaults to baseline)
        """
        self.ibkr_client = ibkr_client
        self.config = config or get_baseline_strategy()
        self.screener = StockScreener(ibkr_client, self.config)
        self.options_finder = OptionsFinder(ibkr_client, self.config)

        logger.info(
            f"Initialized NakedPutStrategy with config: "
            f"OTM {self.config.otm_range}, Premium {self.config.premium_range}, "
            f"DTE {self.config.dte_range}"
        )

    def find_opportunities(self, max_results: int = 10) -> list[TradeOpportunity]:
        """Find naked put trade opportunities.

        Workflow:
            1. Screen for stocks matching trend/price/volume criteria
            2. For each stock, find options matching OTM/premium/DTE criteria
            3. Rank opportunities by quality (premium, margin efficiency)
            4. Return top N opportunities

        Args:
            max_results: Maximum number of opportunities to return

        Returns:
            list[TradeOpportunity]: Sorted list of trade opportunities

        Example:
            >>> opportunities = strategy.find_opportunities(max_results=5)
            >>> print(f"Found {len(opportunities)} opportunities")
        """
        logger.info(
            f"Scanning for naked put opportunities (max_results={max_results})..."
        )

        # Step 1: Screen for candidate stocks
        candidate_stocks = self.screener.scan_stocks(
            trend_filter=self.config.trend_filter,
            min_price=self.config.min_stock_price,
            max_price=self.config.max_stock_price,
            min_volume=self.config.min_daily_volume,
            max_results=max_results * 3,  # Get 3x to account for filtering
        )

        if not candidate_stocks:
            logger.warning("No candidate stocks found matching criteria")
            return []

        logger.info(f"Found {len(candidate_stocks)} candidate stocks")

        # Step 2: Find options for each candidate stock
        all_opportunities = []
        for stock in candidate_stocks:
            try:
                options = self.options_finder.find_put_options(
                    symbol=stock["symbol"],
                    stock_price=stock["price"],
                    otm_range=self.config.otm_range,
                    premium_range=self.config.premium_range,
                    dte_range=self.config.dte_range,
                    max_results=2,  # Top 2 strikes per stock
                )

                for option in options:
                    opportunity = TradeOpportunity(
                        symbol=stock["symbol"],
                        strike=option["strike"],
                        expiration=option["expiration"],
                        option_type="PUT",
                        premium=option["premium"],
                        contracts=self.config.position_size,
                        otm_pct=option["otm_pct"],
                        dte=option["dte"],
                        stock_price=stock["price"],
                        trend=stock["trend"],
                        sector=stock.get("sector"),
                        confidence=self._calculate_confidence(stock, option),
                        reasoning=self._generate_reasoning(stock, option),
                        margin_required=option.get("margin_required", 0),
                    )
                    all_opportunities.append(opportunity)

            except Exception as e:
                logger.warning(f"Error finding options for {stock['symbol']}: {e}")
                continue

        if not all_opportunities:
            logger.warning("No options found matching strategy criteria")
            return []

        # Step 3: Rank and return top opportunities
        ranked_opportunities = self._rank_opportunities(all_opportunities)
        top_opportunities = ranked_opportunities[:max_results]

        logger.info(
            f"Found {len(top_opportunities)} trade opportunities "
            f"(filtered from {len(all_opportunities)} options)"
        )

        return top_opportunities

    def should_enter_trade(self, opportunity: TradeOpportunity) -> bool:
        """Validate if trade opportunity meets entry criteria.

        Checks:
            - OTM percentage in range
            - Premium in range
            - DTE in range
            - Trend matches filter
            - Confidence threshold met

        Args:
            opportunity: Trade opportunity to validate

        Returns:
            bool: True if all criteria met

        Example:
            >>> if strategy.should_enter_trade(opportunity):
            ...     print("Trade meets all entry criteria")
        """
        # Validate against config
        opportunity_dict = {
            "otm_pct": opportunity.otm_pct,
            "premium": opportunity.premium,
            "dte": opportunity.dte,
            "trend": opportunity.trend,
        }

        if not self.config.validate_opportunity(opportunity_dict):
            logger.debug(
                f"Opportunity {opportunity.symbol} failed validation: "
                f"{opportunity_dict}"
            )
            return False

        # Check confidence threshold
        if opportunity.confidence < 0.5:
            logger.debug(
                f"Opportunity {opportunity.symbol} below confidence threshold: "
                f"{opportunity.confidence}"
            )
            return False

        logger.info(
            f"Trade opportunity validated: {opportunity.symbol} "
            f"${opportunity.strike} PUT @ ${opportunity.premium}"
        )
        return True

    def should_exit_trade(
        self,
        entry_premium: float,
        current_premium: float,
        current_dte: int,
        entry_date: datetime,
    ) -> ExitSignal:
        """Determine if position should be exited.

        Exit Triggers:
            1. Profit Target: Premium drops to 50% of entry (50% profit)
            2. Stop Loss: Loss reaches 200% of premium received
            3. Time Exit: 3 days before expiration

        Args:
            entry_premium: Premium received at entry
            current_premium: Current option premium
            current_dte: Current days to expiration
            entry_date: Date position was entered

        Returns:
            ExitSignal: Exit decision with reason

        Example:
            >>> signal = strategy.should_exit_trade(0.50, 0.25, 5, datetime.now())
            >>> if signal.should_exit:
            ...     print(f"Exit: {signal.reason}")
        """
        profit_pct = (entry_premium - current_premium) / entry_premium

        # Check profit target
        if self.config.should_exit_profit_target(entry_premium, current_premium):
            return ExitSignal(
                should_exit=True,
                reason="profit_target",
                confidence=1.0,
                current_premium=current_premium,
                profit_pct=profit_pct,
            )

        # Check stop loss
        if self.config.should_exit_stop_loss(entry_premium, current_premium):
            return ExitSignal(
                should_exit=True,
                reason="stop_loss",
                confidence=1.0,
                current_premium=current_premium,
                profit_pct=profit_pct,
            )

        # Check time exit
        if self.config.should_exit_time(current_dte):
            return ExitSignal(
                should_exit=True,
                reason="time_exit",
                confidence=1.0,
                current_premium=current_premium,
                profit_pct=profit_pct,
            )

        # No exit signal
        return ExitSignal(
            should_exit=False,
            reason="holding",
            confidence=1.0,
            current_premium=current_premium,
            profit_pct=profit_pct,
        )

    def get_position_size(self, opportunity: TradeOpportunity) -> int:
        """Calculate position size for trade.

        Currently uses fixed position size from config. Future versions
        may implement dynamic sizing based on risk/volatility.

        Args:
            opportunity: Trade opportunity

        Returns:
            int: Number of contracts (currently always config.position_size)

        Example:
            >>> size = strategy.get_position_size(opportunity)
            >>> print(f"Trade {size} contracts")
        """
        # For baseline strategy, use fixed position size
        return self.config.position_size

    def validate_configuration(self) -> bool:
        """Validate strategy configuration.

        Checks:
            - OTM range is valid (0-1)
            - Premium range is positive
            - DTE range is positive
            - Position size is positive
            - Max positions is positive

        Returns:
            bool: True if configuration valid

        Raises:
            ValueError: If configuration invalid

        Example:
            >>> strategy.validate_configuration()
            True
        """
        # Validate OTM range
        if not (0 < self.config.otm_range[0] < self.config.otm_range[1] < 1):
            raise ValueError(
                f"Invalid OTM range: {self.config.otm_range}. "
                "Must be between 0 and 1, with min < max"
            )

        # Validate premium range
        if not (0 < self.config.premium_range[0] < self.config.premium_range[1]):
            raise ValueError(
                f"Invalid premium range: {self.config.premium_range}. "
                "Must be positive with min < max"
            )

        # Validate DTE range
        if not (0 < self.config.dte_range[0] < self.config.dte_range[1]):
            raise ValueError(
                f"Invalid DTE range: {self.config.dte_range}. "
                "Must be positive with min < max"
            )

        # Validate position sizing
        if self.config.position_size <= 0:
            raise ValueError(
                f"Invalid position size: {self.config.position_size}. "
                "Must be positive"
            )

        if self.config.max_positions <= 0:
            raise ValueError(
                f"Invalid max positions: {self.config.max_positions}. "
                "Must be positive"
            )

        logger.info("Strategy configuration validated successfully")
        return True

    def _calculate_confidence(self, stock: dict, option: dict) -> float:
        """Calculate confidence score for an opportunity.

        Factors:
            - Premium quality (higher within range = better)
            - OTM positioning (middle of range = better)
            - Trend strength (stronger trend = better)
            - Liquidity (higher volume = better)

        Args:
            stock: Stock data dictionary
            option: Option data dictionary

        Returns:
            float: Confidence score 0.0-1.0
        """
        score = 0.0

        # Premium quality (30%)
        premium = option["premium"]
        premium_range = self.config.premium_range
        premium_pct = (premium - premium_range[0]) / (
            premium_range[1] - premium_range[0]
        )
        score += 0.3 * min(1.0, premium_pct)

        # OTM positioning (25%) - prefer middle of range
        otm_pct = option["otm_pct"]
        otm_range = self.config.otm_range
        otm_mid = (otm_range[0] + otm_range[1]) / 2
        otm_distance = abs(otm_pct - otm_mid) / (otm_range[1] - otm_range[0])
        score += 0.25 * (1.0 - otm_distance)

        # Trend strength (25%)
        trend_score = stock.get("trend_score", 0.5)
        score += 0.25 * trend_score

        # Volume quality (20%)
        volume_score = min(
            1.0,
            stock.get("volume", 0) / (self.config.min_daily_volume * 2),
        )
        score += 0.2 * volume_score

        return round(score, 2)

    def _generate_reasoning(self, stock: dict, option: dict) -> str:
        """Generate human-readable reasoning for trade selection.

        Args:
            stock: Stock data dictionary
            option: Option data dictionary

        Returns:
            str: Reasoning text
        """
        return (
            f"{stock['symbol']} in {stock['trend']} with "
            f"{fmt_pct(option['otm_pct'])} OTM put @ ${option['premium']:.2f} premium, "
            f"{option['dte']} DTE. "
            f"Stock price ${stock['price']:.2f}, strike ${option['strike']:.2f}."
        )

    def _rank_opportunities(
        self, opportunities: list[TradeOpportunity]
    ) -> list[TradeOpportunity]:
        """Rank opportunities by quality.

        Ranking Criteria:
            1. Confidence score (primary)
            2. Premium (higher better)
            3. Margin efficiency (premium/margin ratio)

        Args:
            opportunities: List of opportunities to rank

        Returns:
            list[TradeOpportunity]: Sorted list (best first)
        """
        return sorted(
            opportunities,
            key=lambda x: (
                x.confidence,
                x.premium,
                x.premium / max(x.margin_required, 1),  # Avoid division by zero
            ),
            reverse=True,
        )
