"""Daily position snapshot service.

Phase 2.6D - Position Monitoring
Captures daily snapshots for all open positions to track P&L evolution,
Greeks changes, and path data for learning engine analysis.
"""

from datetime import date, datetime
from typing import List, Optional

from ib_insync import Index, Stock
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import Trade, PositionSnapshot
from src.utils.calc import calc_pnl, calc_pnl_pct
from src.utils.market_data import safe_price
from src.utils.timezone import us_trading_date


class PositionSnapshotService:
    """Captures daily snapshots for open positions.

    Designed to run at market close (4 PM ET) daily to capture
    end-of-day position state for all open trades.
    """

    def __init__(self, ibkr_client, db_session: Session):
        """Initialize position snapshot service.

        Args:
            ibkr_client: IBKR client for market data
            db_session: Database session
        """
        self.ibkr = ibkr_client
        self.db = db_session

    def capture_all_open_positions(self) -> List[PositionSnapshot]:
        """Capture snapshots for all open positions.

        Called daily at market close (4 PM ET) to capture end-of-day state.

        Returns:
            List of captured snapshots
        """
        # Get all open trades (no exit_date)
        open_trades = (
            self.db.query(Trade)
            .filter(Trade.exit_date.is_(None))
            .all()
        )

        logger.info(f"Capturing snapshots for {len(open_trades)} open positions")

        snapshots = []
        today = us_trading_date()

        for trade in open_trades:
            try:
                # Check if already captured today
                existing = (
                    self.db.query(PositionSnapshot)
                    .filter(
                        PositionSnapshot.trade_id == trade.id,
                        PositionSnapshot.snapshot_date == today,
                    )
                    .first()
                )

                if existing:
                    logger.debug(
                        f"Snapshot already exists for trade {trade.id} on {today}"
                    )
                    continue

                snapshot = self._capture_single_position(trade, today)
                if snapshot:
                    self.db.add(snapshot)
                    snapshots.append(snapshot)

            except Exception as e:
                logger.error(f"Failed to capture snapshot for trade {trade.id}: {e}")

        self.db.commit()
        logger.info(f"Captured {len(snapshots)} position snapshots")

        return snapshots

    def _capture_single_position(
        self, trade: Trade, snapshot_date: date
    ) -> Optional[PositionSnapshot]:
        """Capture snapshot for a single position.

        Args:
            trade: Trade object
            snapshot_date: Date of snapshot

        Returns:
            PositionSnapshot or None if capture failed
        """
        snapshot = PositionSnapshot(
            trade_id=trade.id,
            snapshot_date=snapshot_date,
            captured_at=datetime.now(),
        )

        try:
            # Get current option price and Greeks
            contract = self.ibkr.get_option_contract(
                trade.symbol,
                trade.expiration.strftime("%Y-%m-%d"),
                trade.strike,
                right="P" if trade.option_type == "PUT" else "C",
            )

            # Qualify contract
            qualified = self.ibkr.ib.qualifyContracts(contract)
            if not qualified:
                logger.warning(f"Could not qualify contract for trade {trade.id}")
                return None

            # Request market data with Greeks
            ticker = self.ibkr.ib.reqMktData(qualified[0], "", False, False)
            self.ibkr.ib.sleep(2)  # Wait for data

            # Capture current premium (NaN-safe)
            snapshot.current_premium = safe_price(ticker)

            # Calculate P&L
            if snapshot.current_premium and trade.entry_premium:
                snapshot.current_pnl = calc_pnl(trade.entry_premium, snapshot.current_premium, trade.contracts)
                snapshot.current_pnl_pct = calc_pnl_pct(snapshot.current_pnl, trade.entry_premium, trade.contracts)

            # Capture Greeks
            if ticker.modelGreeks:
                snapshot.delta = ticker.modelGreeks.delta
                snapshot.theta = ticker.modelGreeks.theta
                snapshot.gamma = ticker.modelGreeks.gamma
                snapshot.vega = ticker.modelGreeks.vega
                snapshot.iv = ticker.modelGreeks.impliedVol

            # Cancel market data
            self.ibkr.ib.cancelMktData(qualified[0])

            # Calculate DTE remaining
            if trade.expiration:
                snapshot.dte_remaining = (trade.expiration - snapshot_date).days

            # Get underlying stock price
            stock = Stock(trade.symbol, "SMART", "USD")
            stock_data = self.ibkr.get_market_data(stock)
            if stock_data:
                snapshot.stock_price = stock_data["last"]

                # Calculate distance to strike
                if snapshot.stock_price and trade.strike:
                    snapshot.distance_to_strike_pct = (
                        snapshot.stock_price - trade.strike
                    ) / snapshot.stock_price

            # Capture VIX
            try:
                vix_contract = Index("VIX", "CBOE", "USD")
                vix_data = self.ibkr.get_market_data(vix_contract)
                if vix_data:
                    snapshot.vix = vix_data["last"]
            except Exception as e:
                logger.debug(f"Failed to capture VIX: {e}")

            # Capture SPY
            try:
                spy_stock = Stock("SPY", "SMART", "USD")
                spy_data = self.ibkr.get_market_data(spy_stock)
                if spy_data:
                    snapshot.spy_price = spy_data["last"]
            except Exception as e:
                logger.debug(f"Failed to capture SPY: {e}")

            logger.debug(
                f"Position snapshot captured for {trade.symbol} trade {trade.id}",
                extra={
                    "current_premium": snapshot.current_premium,
                    "current_pnl": snapshot.current_pnl,
                    "dte_remaining": snapshot.dte_remaining,
                },
            )

            return snapshot

        except Exception as e:
            logger.error(
                f"Error capturing position snapshot for trade {trade.id}: {e}",
                exc_info=True,
            )
            return None
