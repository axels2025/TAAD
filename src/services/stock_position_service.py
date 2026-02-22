"""Stock position tracking service for option assignments.

When a naked put is assigned, IBKR converts the option to 100 shares
of stock per contract. This service manages the resulting stock positions
and computes combined option + stock P&L without double-counting.

P&L Model (Option A):
    option_pnl = premium collected (from origin trade's profit_loss)
    stock_pnl  = (sale_price - strike) * shares  (uses STRIKE as cost basis)
    total_pnl  = option_pnl + stock_pnl
    irs_cost_basis = strike - premium/share  (for tax compliance only)
"""

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import StockPosition, Trade
from src.data.repositories import StockPositionRepository, TradeRepository
from src.services.assignment_detector import AssignmentEvent
from src.utils.timezone import us_eastern_now


class StockPositionService:
    """CRUD and P&L operations for stock positions from assignments."""

    def __init__(self, session: Session):
        """Initialize with database session.

        Args:
            session: SQLAlchemy session
        """
        self.repo = StockPositionRepository(session)
        self.trade_repo = TradeRepository(session)
        self.session = session

    def create_from_assignment(self, event: AssignmentEvent) -> StockPosition | None:
        """Create a stock position when an option assignment is detected.

        Looks up the origin trade, computes cost basis from strike price,
        and sets trade.lifecycle_status = 'stock_held'.

        Args:
            event: AssignmentEvent from the assignment detector

        Returns:
            Created StockPosition, or None if origin trade not found
        """
        if not event.matched_trade_id:
            logger.warning("AssignmentEvent has no matched_trade_id, skipping")
            return None

        # Check for duplicate
        existing = self.repo.get_by_origin_trade(event.matched_trade_id)
        if existing:
            logger.info(
                f"StockPosition already exists for trade {event.matched_trade_id}"
            )
            return existing

        # Look up origin trade
        trade = self.trade_repo.get_by_id(event.matched_trade_id)
        if not trade:
            logger.error(f"Origin trade {event.matched_trade_id} not found")
            return None

        # Cost basis = strike price (Option A: no premium in cost basis)
        cost_basis = trade.strike

        # IRS cost basis = strike - premium per share (for tax compliance)
        shares_per_contract = 100
        premium_per_share = (
            trade.entry_premium / shares_per_contract
            if trade.entry_premium
            else 0.0
        )
        irs_cost_basis = trade.strike - premium_per_share

        # Option P&L = the trade's profit_loss at time of assignment
        option_pnl = trade.profit_loss or 0.0

        stock_position = StockPosition(
            symbol=event.symbol,
            shares=event.shares,
            cost_basis_per_share=cost_basis,
            irs_cost_basis_per_share=irs_cost_basis,
            origin_trade_id=event.matched_trade_id,
            assigned_date=event.detection_time,
            option_pnl=option_pnl,
        )

        self.repo.create(stock_position)

        # Update the origin trade's lifecycle status
        trade.lifecycle_status = "stock_held"
        trade.option_pnl = option_pnl
        self.session.flush()

        logger.info(
            f"Created StockPosition: {event.symbol} x{event.shares} shares, "
            f"cost_basis=${cost_basis:.2f}, irs_cost=${irs_cost_basis:.2f}, "
            f"option_pnl=${option_pnl:.2f}"
        )

        return stock_position

    def close_position(
        self,
        stock_position: StockPosition,
        sale_price_per_share: float,
        close_reason: str = "sold",
    ) -> StockPosition:
        """Close a stock position and compute combined P&L.

        stock_pnl = (sale_price - cost_basis) * shares
        total_pnl = option_pnl + stock_pnl

        Also updates the origin trade with stock_pnl, total_pnl,
        and sets lifecycle_status = 'fully_closed'.

        Args:
            stock_position: StockPosition to close
            sale_price_per_share: Price per share at which stock was sold
            close_reason: Reason for closing (sold, partial_sold, etc.)

        Returns:
            Updated StockPosition with P&L calculated
        """
        # Compute P&L
        stock_pnl = (
            (sale_price_per_share - stock_position.cost_basis_per_share)
            * stock_position.shares
        )
        option_pnl = stock_position.option_pnl or 0.0
        total_pnl = option_pnl + stock_pnl

        # Update stock position
        stock_position.closed_date = us_eastern_now()
        stock_position.sale_price_per_share = sale_price_per_share
        stock_position.close_reason = close_reason
        stock_position.stock_pnl = stock_pnl
        stock_position.total_pnl = total_pnl

        # Update origin trade
        trade = self.trade_repo.get_by_id(stock_position.origin_trade_id)
        if trade:
            trade.stock_pnl = stock_pnl
            trade.total_pnl = total_pnl
            trade.lifecycle_status = "fully_closed"

        self.session.flush()

        logger.info(
            f"Closed StockPosition: {stock_position.symbol} x{stock_position.shares} "
            f"@ ${sale_price_per_share:.2f} — "
            f"stock_pnl=${stock_pnl:.2f}, option_pnl=${option_pnl:.2f}, "
            f"total_pnl=${total_pnl:.2f}"
        )

        return stock_position

    def get_open_positions(self) -> list[StockPosition]:
        """Get all open (held) stock positions.

        Returns:
            List of StockPosition where closed_date is NULL
        """
        return self.repo.get_open_positions()

    def get_combined_pnl(self, trade_id: str) -> dict | None:
        """Get combined option + stock P&L for a trade.

        Args:
            trade_id: Trade ID to look up

        Returns:
            Dictionary with P&L breakdown, or None if no stock position exists
        """
        stock_pos = self.repo.get_by_origin_trade(trade_id)
        if not stock_pos:
            return None

        return {
            "trade_id": trade_id,
            "symbol": stock_pos.symbol,
            "shares": stock_pos.shares,
            "cost_basis": stock_pos.cost_basis_per_share,
            "irs_cost_basis": stock_pos.irs_cost_basis_per_share,
            "option_pnl": stock_pos.option_pnl,
            "stock_pnl": stock_pos.stock_pnl,
            "total_pnl": stock_pos.total_pnl,
            "sale_price": stock_pos.sale_price_per_share,
            "status": "closed" if stock_pos.closed_date else "held",
        }
