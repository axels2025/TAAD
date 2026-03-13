"""Exit snapshot service for capturing trade exit data.

Phase 2.6E - Exit Snapshots & Learning Data Preparation
Captures comprehensive exit data when trades close, including outcome analysis,
path statistics, and derived learning features.
"""

from datetime import datetime
from typing import Optional

from ib_insync import Index, Stock
from loguru import logger
from sqlalchemy.orm import Session

from src.data.models import Trade, TradeEntrySnapshot, TradeExitSnapshot, PositionSnapshot
from src.utils.calc import calc_pnl, calc_pnl_pct
from src.utils.timezone import utc_now


def _strip_tz(dt: datetime | None) -> datetime | None:
    """Strip timezone info from a datetime for safe arithmetic.

    PostgreSQL 'timestamp without time zone' stores naive datetimes.
    In-memory values may still carry tzinfo from us_eastern_now() if
    the session hasn't been refreshed.  Stripping before subtraction
    prevents 'can't subtract offset-naive and offset-aware datetimes'.
    """
    if dt is None:
        return None
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


class ExitSnapshotService:
    """Capture comprehensive exit data when trades close.

    Analyzes the complete trade lifecycle from entry through position
    monitoring to exit, computing derived features for learning engine.
    """

    def __init__(self, ibkr_client, db_session: Session):
        """Initialize exit snapshot service.

        Args:
            ibkr_client: IBKR client for market data
            db_session: Database session
        """
        self.ibkr = ibkr_client
        self.db = db_session

    def capture_exit_snapshot(
        self,
        trade: Trade,
        exit_premium: float,
        exit_reason: str,
    ) -> TradeExitSnapshot:
        """Capture complete exit snapshot for a closed trade.

        Args:
            trade: Trade object (should have exit_date populated)
            exit_premium: Premium at exit
            exit_reason: Reason for exit (profit_target, stop_loss, expiration, manual)

        Returns:
            TradeExitSnapshot with all captured data
        """
        logger.info(
            f"Capturing exit snapshot for {trade.symbol} trade {trade.id}",
            extra={
                "symbol": trade.symbol,
                "exit_reason": exit_reason,
                "exit_premium": exit_premium,
            },
        )

        # Initialize snapshot
        snapshot = TradeExitSnapshot(
            trade_id=trade.id,
            exit_date=_strip_tz(trade.exit_date) or utc_now(),
            exit_premium=exit_premium,
            exit_reason=exit_reason,
            captured_at=utc_now(),
        )

        # Calculate basic outcome metrics
        self._calculate_outcome_metrics(snapshot, trade, exit_premium)

        # Capture exit market context
        try:
            self._capture_exit_context(snapshot, trade)
        except Exception as e:
            logger.warning(f"Failed to capture exit context: {e}")

        # Calculate context changes during trade
        try:
            self._calculate_context_changes(snapshot, trade)
        except Exception as e:
            logger.warning(f"Failed to calculate context changes: {e}")

        # Analyze path from position snapshots
        try:
            self._analyze_position_path(snapshot, trade)
        except Exception as e:
            logger.warning(f"Failed to analyze position path: {e}")

        # Calculate learning features
        snapshot.trade_quality_score = snapshot.calculate_quality_score()
        if snapshot.max_drawdown_pct and snapshot.roi_pct:
            # Risk-adjusted return: return per unit of drawdown risk
            snapshot.risk_adjusted_return = snapshot.roi_pct / abs(snapshot.max_drawdown_pct) if snapshot.max_drawdown_pct != 0 else snapshot.roi_pct

        logger.info(
            f"Exit snapshot captured for {trade.symbol} trade {trade.id}",
            extra={
                "win": snapshot.win,
                "roi_pct": snapshot.roi_pct,
                "quality_score": snapshot.trade_quality_score,
            },
        )

        return snapshot

    def _calculate_outcome_metrics(
        self, snapshot: TradeExitSnapshot, trade: Trade, exit_premium: float
    ) -> None:
        """Calculate basic P&L and outcome metrics.

        Args:
            snapshot: Exit snapshot to populate
            trade: Trade object
            exit_premium: Premium at exit
        """
        # Days held — strip tzinfo to avoid naive/aware mismatch
        if trade.exit_date and trade.entry_date:
            snapshot.days_held = (_strip_tz(trade.exit_date) - _strip_tz(trade.entry_date)).days

        # Gross profit (before commissions)
        snapshot.gross_profit = calc_pnl(trade.entry_premium, exit_premium, trade.contracts)
        snapshot.roi_pct = calc_pnl_pct(snapshot.gross_profit, trade.entry_premium, trade.contracts)
        snapshot.win = snapshot.gross_profit > 0

        # Net profit (after commissions, if available)
        # TODO: Integrate commission tracking
        snapshot.net_profit = snapshot.gross_profit  # For now, same as gross

        # ROI on margin (if margin data available)
        entry_snapshot = (
            self.db.query(TradeEntrySnapshot)
            .filter(TradeEntrySnapshot.trade_id == trade.id)
            .first()
        )

        if entry_snapshot and entry_snapshot.margin_requirement and snapshot.gross_profit:
            snapshot.roi_on_margin = snapshot.gross_profit / entry_snapshot.margin_requirement

    def _capture_exit_context(
        self, snapshot: TradeExitSnapshot, trade: Trade
    ) -> None:
        """Capture market context at exit (IV, stock price, VIX).

        Args:
            snapshot: Exit snapshot to populate
            trade: Trade object
        """
        # Get option contract
        contract = self.ibkr.get_option_contract(
            trade.symbol,
            trade.expiration.strftime("%Y-%m-%d"),
            trade.strike,
            right="P" if trade.option_type == "PUT" else "C",
        )

        # Qualify and get data
        qualified = self.ibkr.ib.qualifyContracts(contract)
        if qualified:
            ticker = self.ibkr.ib.reqMktData(qualified[0], "", False, False)
            self.ibkr.ib.sleep(2)

            # Capture exit IV
            if ticker.modelGreeks and ticker.modelGreeks.impliedVol:
                snapshot.exit_iv = ticker.modelGreeks.impliedVol

            self.ibkr.ib.cancelMktData(qualified[0])

        # Get stock price at exit
        stock = Stock(trade.symbol, "SMART", "USD")
        stock_data = self.ibkr.get_market_data(stock)
        if stock_data:
            snapshot.stock_price_at_exit = stock_data["last"]

        # Get VIX at exit
        try:
            vix_contract = Index("VIX", "CBOE", "USD")
            vix_data = self.ibkr.get_market_data(vix_contract)
            if vix_data:
                snapshot.vix_at_exit = vix_data["last"]
        except Exception as e:
            logger.debug(f"Failed to capture VIX at exit: {e}")

    def _calculate_context_changes(
        self, snapshot: TradeExitSnapshot, trade: Trade
    ) -> None:
        """Calculate how market context changed during the trade.

        Args:
            snapshot: Exit snapshot to populate
            trade: Trade object
        """
        # Get entry snapshot for comparison
        entry_snapshot = (
            self.db.query(TradeEntrySnapshot)
            .filter(TradeEntrySnapshot.trade_id == trade.id)
            .first()
        )

        if not entry_snapshot:
            return

        # IV change (IV crush detection)
        if snapshot.exit_iv and entry_snapshot.iv:
            snapshot.iv_change_during_trade = snapshot.exit_iv - entry_snapshot.iv

        # Stock price change
        if snapshot.stock_price_at_exit and entry_snapshot.stock_price:
            snapshot.stock_change_during_trade_pct = (
                snapshot.stock_price_at_exit - entry_snapshot.stock_price
            ) / entry_snapshot.stock_price

        # VIX change
        if snapshot.vix_at_exit and entry_snapshot.vix:
            snapshot.vix_change_during_trade = snapshot.vix_at_exit - entry_snapshot.vix

    def _analyze_position_path(
        self, snapshot: TradeExitSnapshot, trade: Trade
    ) -> None:
        """Analyze position path from daily snapshots.

        Calculates:
        - Closest approach to strike
        - Maximum drawdown
        - Maximum unrealized profit
        - Profit capture efficiency

        Args:
            snapshot: Exit snapshot to populate
            trade: Trade object
        """
        # Get all position snapshots for this trade
        position_snapshots = (
            self.db.query(PositionSnapshot)
            .filter(PositionSnapshot.trade_id == trade.id)
            .order_by(PositionSnapshot.snapshot_date)
            .all()
        )

        if not position_snapshots:
            logger.debug(f"No position snapshots found for trade {trade.id}")
            return

        # Find closest approach to strike
        distances = [
            ps.distance_to_strike_pct
            for ps in position_snapshots
            if ps.distance_to_strike_pct is not None
        ]
        if distances:
            snapshot.closest_to_strike_pct = min(distances)

        # Find max drawdown and max profit
        pnl_pcts = [
            ps.current_pnl_pct
            for ps in position_snapshots
            if ps.current_pnl_pct is not None
        ]

        if pnl_pcts:
            snapshot.max_drawdown_pct = min(pnl_pcts)  # Most negative P&L
            snapshot.max_profit_pct = max(pnl_pcts)  # Most positive P&L

            # Calculate profit capture efficiency
            if snapshot.max_profit_pct and snapshot.max_profit_pct > 0:
                if snapshot.roi_pct:
                    snapshot.max_profit_captured_pct = snapshot.roi_pct / snapshot.max_profit_pct
                    # Clamp to [0, 1]
                    snapshot.max_profit_captured_pct = max(
                        0.0, min(1.0, snapshot.max_profit_captured_pct)
                    )

    def save_snapshot(self, snapshot: TradeExitSnapshot) -> None:
        """Save exit snapshot to database (idempotent).

        If an exit snapshot already exists for this trade_id, it is
        updated rather than duplicated.  This prevents UniqueViolation
        when multiple code paths (expiry close, assignment detector,
        exit manager) all attempt to capture the same exit.

        Args:
            snapshot: Exit snapshot to save
        """
        trade_id = snapshot.trade_id
        try:
            # Check for existing snapshot (unique constraint on trade_id)
            existing = (
                self.db.query(TradeExitSnapshot)
                .filter(TradeExitSnapshot.trade_id == trade_id)
                .first()
            )
            if existing:
                logger.info(
                    f"Exit snapshot already exists for trade {trade_id} "
                    f"(id={existing.id}), skipping duplicate"
                )
                return

            # Capture values before commit expires ORM attributes
            win = snapshot.win
            roi = snapshot.roi_pct

            self.db.add(snapshot)
            self.db.commit()
            logger.info(
                f"Saved exit snapshot for trade {trade_id} "
                f"(win={win}, roi={roi})"
            )
        except Exception as e:
            self.db.rollback()
            logger.error(
                f"Failed to save exit snapshot for trade {trade_id}: {e}",
                exc_info=True,
            )
            raise
