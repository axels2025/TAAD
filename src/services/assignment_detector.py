"""Assignment detection for naked put positions.

Detects when a naked put has been assigned by scanning IBKR for
unexpected stock positions that match open option trades.

When a naked put is assigned, IBKR converts the option position to
100 shares of stock per contract. This detector identifies those
stock positions and creates alerts.
"""

from dataclasses import dataclass, field
from datetime import datetime

from loguru import logger

from src.tools.ibkr_client import IBKRClient
from src.utils.calc import calc_pnl, calc_pnl_pct
from src.utils.timezone import us_eastern_now


@dataclass
class AssignmentEvent:
    """Represents a detected option assignment.

    Attributes:
        symbol: Stock symbol
        shares: Number of shares assigned (positive = long)
        avg_cost: Average cost per share from IBKR
        detection_time: When the assignment was detected
        matched_trade_id: Trade ID of the original option trade (if found)
        matched_strike: Strike price of the assigned option (if found)
        matched_expiration: Expiration of the assigned option (if found)
    """

    symbol: str
    shares: int
    avg_cost: float
    detection_time: datetime = field(default_factory=us_eastern_now)
    matched_trade_id: str | None = None
    matched_strike: float | None = None
    matched_expiration: str | None = None

    @property
    def contracts_assigned(self) -> int:
        """Number of option contracts that were assigned."""
        return abs(self.shares) // 100


class AssignmentDetector:
    """Detect option assignments by monitoring for unexpected stock positions.

    Logic:
    1. Get all positions from IBKR
    2. Filter for STOCK positions (secType='STK')
    3. For each stock position, check if we have/had a matching option trade:
       - Same symbol
       - Position size is multiple of 100 (assignment = 100 shares per contract)
    4. If match found, assignment detected

    Response mode is ALERT only (log critical alert for operator review).

    Example:
        >>> detector = AssignmentDetector(ibkr_client)
        >>> events = detector.check_for_assignments()
        >>> for event in events:
        ...     print(f"ASSIGNMENT: {event.symbol} {event.shares} shares")
    """

    def __init__(self, ibkr_client: IBKRClient):
        """Initialize assignment detector.

        Args:
            ibkr_client: Connected IBKR client
        """
        self.ibkr_client = ibkr_client
        # Track already-reported assignments to avoid duplicate alerts
        self._reported_assignments: set[str] = set()

        logger.info("Initialized AssignmentDetector")

    def check_for_assignments(self) -> list[AssignmentEvent]:
        """Check IBKR positions for unexpected stock positions.

        Scans all IBKR positions for stock (STK) positions that could
        indicate a naked put was assigned. Cross-references with open
        option trades in the database.

        Returns:
            List of AssignmentEvent objects for any detected assignments
        """
        try:
            positions = self.ibkr_client.get_positions()
        except Exception as e:
            logger.error(f"Failed to get IBKR positions for assignment check: {e}")
            return []

        # Separate stock and option positions
        stock_positions = []
        option_symbols = set()

        for pos in positions:
            contract = pos.contract
            sec_type = getattr(contract, "secType", None)

            if sec_type == "STK":
                stock_positions.append(pos)
            elif sec_type == "OPT":
                option_symbols.add(contract.symbol)

        if not stock_positions:
            return []

        logger.debug(
            f"Assignment check: {len(stock_positions)} stock positions, "
            f"{len(option_symbols)} option symbols"
        )

        # Check each stock position against open trades
        events = []
        for pos in stock_positions:
            contract = pos.contract
            symbol = contract.symbol
            shares = int(pos.position)

            # Only flag long stock positions from put assignment
            # (short puts get assigned as long stock)
            if shares <= 0:
                continue

            # Assignment always results in multiples of 100 shares
            if shares % 100 != 0:
                continue

            # Check if we have a matching option trade in the database
            event = self._check_against_trades(symbol, shares, pos.avgCost)
            if event:
                # Deduplicate: only report each assignment once
                dedup_key = f"{symbol}_{shares}_{pos.avgCost:.2f}"
                if dedup_key not in self._reported_assignments:
                    self._reported_assignments.add(dedup_key)
                    self._close_assigned_trade(event)
                    events.append(event)

                    logger.critical(
                        f"POSSIBLE ASSIGNMENT DETECTED: {symbol} — "
                        f"{shares} shares found in account "
                        f"(avg cost ${pos.avgCost:.2f})"
                    )
                    if event.matched_trade_id:
                        logger.critical(
                            f"  Matched to trade {event.matched_trade_id}: "
                            f"${event.matched_strike} put exp {event.matched_expiration}"
                        )

        return events

    def _check_against_trades(
        self,
        symbol: str,
        shares: int,
        avg_cost: float,
    ) -> AssignmentEvent | None:
        """Check if a stock position matches an open or recently-closed put trade.

        Args:
            symbol: Stock symbol
            shares: Number of shares (positive)
            avg_cost: Average cost per share

        Returns:
            AssignmentEvent if match found, None otherwise
        """
        try:
            from src.data.database import get_db_session
            from src.data.models import Trade

            with get_db_session() as session:
                # Look for open put trades on this symbol
                open_puts = (
                    session.query(Trade)
                    .filter(
                        Trade.symbol == symbol,
                        Trade.option_type == "PUT",
                        Trade.exit_date.is_(None),
                    )
                    .all()
                )

                if open_puts:
                    # Match to the most recent open put
                    trade = open_puts[-1]
                    return AssignmentEvent(
                        symbol=symbol,
                        shares=shares,
                        avg_cost=avg_cost,
                        matched_trade_id=trade.trade_id,
                        matched_strike=trade.strike,
                        matched_expiration=trade.expiration.strftime("%Y-%m-%d"),
                    )

                # Also check recently closed puts (within 3 days)
                # Assignment can happen after option expires or is closed
                from datetime import timedelta

                cutoff = us_eastern_now() - timedelta(days=3)
                recent_puts = (
                    session.query(Trade)
                    .filter(
                        Trade.symbol == symbol,
                        Trade.option_type == "PUT",
                        Trade.exit_date >= cutoff,
                    )
                    .order_by(Trade.exit_date.desc())
                    .first()
                )

                if recent_puts:
                    return AssignmentEvent(
                        symbol=symbol,
                        shares=shares,
                        avg_cost=avg_cost,
                        matched_trade_id=recent_puts.trade_id,
                        matched_strike=recent_puts.strike,
                        matched_expiration=recent_puts.expiration.strftime("%Y-%m-%d"),
                    )

        except Exception as e:
            logger.error(f"Error checking trades for {symbol}: {e}")

        # No matching put trade found — stock position is likely from
        # other trading activity (not our naked puts)
        return None

    def _close_assigned_trade(self, event: AssignmentEvent) -> None:
        """Close the matched option trade in the database after assignment.

        Records exit at intrinsic value (strike - stock_price) so the learning
        engine sees the full loss. The stock position is handled manually.
        """
        if not event.matched_trade_id:
            return
        try:
            from src.data.database import get_db_session
            from src.data.models import Trade

            with get_db_session() as session:
                trade = session.query(Trade).filter(
                    Trade.trade_id == event.matched_trade_id
                ).first()
                if not trade or trade.exit_date is not None:
                    return

                # Calculate intrinsic value at assignment
                # For a put: intrinsic = max(strike - stock_price, 0)
                # avg_cost from IBKR is the per-share cost of the assigned stock
                stock_price_at_assignment = event.avg_cost
                intrinsic_value = max(trade.strike - stock_price_at_assignment, 0)

                # Exit premium = intrinsic value (what you'd have to pay to buy back)
                trade.exit_date = event.detection_time
                trade.exit_premium = intrinsic_value
                trade.exit_reason = "assignment"
                trade.profit_loss = calc_pnl(trade.entry_premium, intrinsic_value, trade.contracts)
                trade.profit_pct = calc_pnl_pct(trade.profit_loss, trade.entry_premium, trade.contracts)
                trade.days_held = (event.detection_time.date() - trade.entry_date.date()).days if trade.entry_date else 0

                session.commit()
                logger.critical(
                    f"Auto-closed assigned trade {event.matched_trade_id}: "
                    f"{event.symbol} ${trade.strike}P — "
                    f"intrinsic=${intrinsic_value:.2f}, "
                    f"P&L=${trade.profit_loss:.2f} ({trade.profit_pct:.1%})"
                )

                # Best-effort exit snapshot
                try:
                    from src.services.exit_snapshot import ExitSnapshotService
                    exit_service = ExitSnapshotService(self.ibkr_client, session)
                    exit_snapshot = exit_service.capture_exit_snapshot(
                        trade=trade,
                        exit_premium=intrinsic_value,
                        exit_reason="assignment",
                    )
                    exit_service.save_snapshot(exit_snapshot)
                except Exception as snap_err:
                    logger.warning(f"Exit snapshot failed for assignment: {snap_err}")

                # Create stock position tracking
                try:
                    from src.services.stock_position_service import StockPositionService
                    svc = StockPositionService(session)
                    svc.create_from_assignment(event)
                    session.commit()
                except Exception as stock_err:
                    logger.warning(f"Stock position creation failed for assignment: {stock_err}")

        except Exception as e:
            logger.error(f"Failed to close assigned trade: {e}")
