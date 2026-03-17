"""Covered call detection and pairing service.

Detects when a short call Trade is covered by a held StockPosition
on the same symbol. Used to:
- Suppress false assignment risk alerts (assignment is the intended outcome)
- Present paired positions correctly to Claude
- Track the wheel strategy lifecycle (put assigned → stock held → call sold)
"""

from __future__ import annotations

from dataclasses import dataclass

from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import StockPosition, Trade


@dataclass
class CoveredCallPair:
    """A detected covered call: stock position + short call trade."""

    stock_position: StockPosition
    call_trade: Trade
    shares_covered: int  # min(stock.shares, call.contracts * 100)
    fully_covered: bool  # shares >= contracts * 100


class CoveredCallDetector:
    """Detects and manages covered call pairings.

    A covered call exists when:
    1. An open StockPosition exists for a symbol (closed_date IS NULL)
    2. An open short CALL Trade exists for the same symbol (exit_date IS NULL)
    3. The stock shares are sufficient to cover the call contracts

    Args:
        db: SQLAlchemy session
    """

    def __init__(self, db: Session):
        self.db = db

    def detect_pairs(self) -> list[CoveredCallPair]:
        """Find all open covered call pairs by matching stocks to calls.

        Returns:
            List of CoveredCallPair objects for each detected pairing.
        """
        # Get open stock positions
        open_stocks = (
            self.db.query(StockPosition)
            .filter(StockPosition.closed_date.is_(None))
            .all()
        )
        if not open_stocks:
            return []

        stock_by_symbol: dict[str, StockPosition] = {}
        for sp in open_stocks:
            stock_by_symbol[sp.symbol] = sp

        # Get open CALL trades
        open_calls = (
            self.db.query(Trade)
            .filter(
                Trade.exit_date.is_(None),
                Trade.option_type.in_(("CALL", "C")),
            )
            .all()
        )

        pairs = []
        for call_trade in open_calls:
            stock_pos = stock_by_symbol.get(call_trade.symbol)
            if stock_pos is None:
                continue

            contracts = call_trade.contracts or 0
            shares_needed = contracts * 100
            shares_available = stock_pos.shares or 0
            shares_covered = min(shares_available, shares_needed)
            fully_covered = shares_available >= shares_needed

            pairs.append(CoveredCallPair(
                stock_position=stock_pos,
                call_trade=call_trade,
                shares_covered=shares_covered,
                fully_covered=fully_covered,
            ))

        return pairs

    def get_covered_symbols(self) -> set[str]:
        """Return set of symbols that have covered calls.

        Fast check for alert suppression — avoids loading full pairs.

        Returns:
            Set of symbol strings where stock + short call exist.
        """
        open_stock_symbols = {
            sp.symbol
            for sp in self.db.query(StockPosition.symbol)
            .filter(StockPosition.closed_date.is_(None))
            .all()
        }
        if not open_stock_symbols:
            return set()

        open_call_symbols = {
            t.symbol
            for t in self.db.query(Trade.symbol)
            .filter(
                Trade.exit_date.is_(None),
                Trade.option_type.in_(("CALL", "C")),
            )
            .all()
        }

        return open_stock_symbols & open_call_symbols

    def is_covered_call(self, trade: Trade) -> bool:
        """Check if a specific CALL trade is covered by stock.

        Args:
            trade: Trade record to check.

        Returns:
            True if a matching open StockPosition exists.
        """
        if trade.option_type not in ("CALL", "C"):
            return False

        return (
            self.db.query(StockPosition)
            .filter(
                StockPosition.symbol == trade.symbol,
                StockPosition.closed_date.is_(None),
            )
            .first()
            is not None
        )

    def link_covered_call(
        self, stock_position: StockPosition, call_trade: Trade
    ) -> None:
        """Link a covered call trade to its stock position.

        Args:
            stock_position: The held stock position.
            call_trade: The short call Trade to link.
        """
        stock_position.covered_call_trade_id = call_trade.trade_id
        logger.info(
            f"Linked covered call: {call_trade.symbol} "
            f"${call_trade.strike}C → stock position (id={stock_position.id})"
        )

    def unlink_covered_call(self, stock_position: StockPosition) -> None:
        """Clear the covered call link (call expired or closed).

        Args:
            stock_position: The stock position to unlink.
        """
        old_id = stock_position.covered_call_trade_id
        stock_position.covered_call_trade_id = None
        if old_id:
            logger.info(
                f"Unlinked covered call {old_id} from "
                f"{stock_position.symbol} stock position"
            )

    def unlink_by_trade_id(self, trade_id: str) -> None:
        """Clear covered call link for a specific trade ID.

        Used when a call trade expires or is closed.

        Args:
            trade_id: The trade_id of the expired/closed call.
        """
        stock_pos = (
            self.db.query(StockPosition)
            .filter(StockPosition.covered_call_trade_id == trade_id)
            .first()
        )
        if stock_pos:
            self.unlink_covered_call(stock_pos)

    def auto_link_unlinked_calls(self) -> list[CoveredCallPair]:
        """Find open CALL trades matching open StockPositions and link them.

        Called on daemon startup to establish linkage for manually-created
        covered call Trade records.

        Returns:
            List of newly linked CoveredCallPair objects.
        """
        pairs = self.detect_pairs()
        linked = []

        for pair in pairs:
            current_link = pair.stock_position.covered_call_trade_id
            if current_link != pair.call_trade.trade_id:
                self.link_covered_call(pair.stock_position, pair.call_trade)
                linked.append(pair)

        if linked:
            self.db.commit()
            logger.info(
                f"Auto-linked {len(linked)} covered call pair(s): "
                f"{[p.call_trade.symbol for p in linked]}"
            )

        return linked
