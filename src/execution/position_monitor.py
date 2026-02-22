"""Position monitoring for real-time tracking and P&L calculation.

This module monitors open positions with:
- Real-time position tracking
- P&L calculation (realized and unrealized)
- Greeks monitoring (delta, theta, gamma, vega)
- Position aging
- Alert generation
- 15-minute update intervals
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ib_insync import Option
from loguru import logger

from src.config.baseline_strategy import BaselineStrategy
from src.data.models import Position
from src.utils.position_key import position_key_from_contract, position_key_from_trade
from src.utils.timezone import us_eastern_now, us_trading_date
from src.data.repositories import PositionRepository, TradeRepository
from src.services.assignment_detector import AssignmentDetector, AssignmentEvent
from src.tools.ibkr_client import IBKRClient


@dataclass
class PositionAlert:
    """Alert for position approaching trigger.

    Attributes:
        position_id: Position identifier
        alert_type: Type of alert (profit_target, stop_loss, time_exit)
        severity: Alert severity (info, warning, critical)
        message: Alert message
        current_value: Current value triggering alert
        threshold: Threshold value
    """

    position_id: str
    alert_type: str
    severity: str
    message: str
    current_value: float
    threshold: float
    timestamp: datetime = None

    def __post_init__(self):
        """Set timestamp if not provided."""
        if self.timestamp is None:
            self.timestamp = datetime.now()


@dataclass
class PositionStatus:
    """Current status of a position.

    Attributes:
        position_id: Position identifier
        symbol: Stock symbol
        strike: Option strike
        option_type: PUT or CALL
        expiration_date: Option expiration date (YYYYMMDD format)
        contracts: Number of contracts
        entry_premium: Premium received at entry
        current_premium: Current option premium
        current_pnl: Current profit/loss in dollars
        current_pnl_pct: Current profit/loss as percentage
        days_held: Days since position opened
        dte: Days to expiration
        delta: Option delta
        theta: Option theta
        gamma: Option gamma
        vega: Option vega
        approaching_profit_target: Whether nearing profit target
        approaching_stop_loss: Whether nearing stop loss
        approaching_expiration: Whether nearing expiration
    """

    position_id: str
    symbol: str
    strike: float
    option_type: str
    expiration_date: str  # YYYYMMDD format
    contracts: int
    entry_premium: float
    current_premium: float
    current_pnl: float
    current_pnl_pct: float
    days_held: int
    dte: int
    delta: float | None = None
    theta: float | None = None
    gamma: float | None = None
    vega: float | None = None
    underlying_price: float | None = None
    entry_stock_price: float | None = None
    approaching_profit_target: bool = False
    approaching_stop_loss: bool = False
    approaching_expiration: bool = False
    market_data_stale: bool = False


class PositionMonitor:
    """Monitor open positions in real-time.

    The PositionMonitor tracks all open positions with:
    - Real-time price updates
    - P&L calculations
    - Greeks monitoring
    - Alert generation
    - Database persistence

    Example:
        >>> monitor = PositionMonitor(ibkr_client, config)
        >>> positions = monitor.get_all_positions()
        >>> for pos in positions:
        ...     print(f"{pos.symbol}: P&L {pos.current_pnl_pct:.1%}")
        >>> alerts = monitor.check_alerts()
        >>> for alert in alerts:
        ...     print(f"ALERT: {alert.message}")
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        config: BaselineStrategy,
        position_repository: PositionRepository | None = None,
        trade_repository: TradeRepository | None = None,
        update_interval_minutes: int = 15,
    ):
        """Initialize position monitor.

        Args:
            ibkr_client: Connected IBKR client
            config: Strategy configuration
            position_repository: Position data repository
            trade_repository: Trade data repository
            update_interval_minutes: How often to update positions
        """
        self.ibkr_client = ibkr_client
        self.config = config
        self.position_repository = position_repository
        self.trade_repository = trade_repository
        self.update_interval_minutes = update_interval_minutes
        self.last_update = None
        self.assignment_detector = AssignmentDetector(ibkr_client)

        logger.info(
            f"Initialized PositionMonitor "
            f"(update interval: {update_interval_minutes} min)"
        )

    def close_expired_positions(self, dry_run: bool = False) -> list[dict]:
        """Auto-close positions whose expiration date has passed.

        Queries the database for open trades (exit_date IS NULL) where
        expiration < today, and closes them with exit_reason='expired'.
        For expired puts that were not assigned, full premium is kept (exit_premium=0).

        Args:
            dry_run: If True, report expired positions without modifying the database.

        Returns:
            list[dict]: List of closed position summaries
        """
        from src.data.database import get_db_session
        from src.data.models import Trade

        today = us_trading_date()
        closed = []

        with get_db_session() as session:
            expired_trades = session.query(Trade).filter(
                Trade.exit_date.is_(None),
                Trade.expiration < today,
            ).all()

            for trade in expired_trades:
                entry_prem = trade.entry_premium or 0.0
                profit_loss = entry_prem * (trade.contracts or 0) * 100

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would auto-close expired: {trade.symbol} ${trade.strike} "
                        f"exp {trade.expiration} — premium kept (${profit_loss:.2f})"
                    )
                    closed.append({
                        "symbol": trade.symbol,
                        "strike": trade.strike,
                        "expiration": str(trade.expiration),
                        "profit_loss": profit_loss,
                    })
                    continue

                trade.exit_date = us_eastern_now()
                trade.exit_premium = 0.0
                trade.exit_reason = "expired"
                trade.profit_loss = profit_loss
                trade.profit_pct = 1.0  # 100% of premium kept
                trade.days_held = (trade.expiration - trade.entry_date.date()).days if trade.entry_date else 0

                # Capture exit snapshot (best-effort)
                try:
                    from src.services.exit_snapshot import ExitSnapshotService
                    exit_service = ExitSnapshotService(self.ibkr_client, session)
                    exit_snapshot = exit_service.capture_exit_snapshot(
                        trade=trade,
                        exit_premium=0.0,
                        exit_reason="expired",
                    )
                    exit_service.save_snapshot(exit_snapshot)
                    logger.debug(f"  Exit snapshot captured for expired {trade.symbol}")
                except Exception as snap_err:
                    logger.warning(f"  Exit snapshot failed for expired {trade.symbol}: {snap_err}")

                logger.info(
                    f"Auto-closed expired position: {trade.symbol} ${trade.strike} "
                    f"exp {trade.expiration} — full premium kept "
                    f"(${trade.profit_loss or 0:.2f})"
                )
                closed.append({
                    "symbol": trade.symbol,
                    "strike": trade.strike,
                    "expiration": str(trade.expiration),
                    "profit_loss": trade.profit_loss,
                })

            if closed and not dry_run:
                session.commit()
                logger.info(f"Auto-closed {len(closed)} expired position(s)")

        return closed

    def get_all_positions(self) -> list[PositionStatus]:
        """Get all open positions from DATABASE first, then enrich with IBKR pricing.

        This method uses the database as the source of truth for which positions
        exist, and uses IBKR only for current market pricing. This ensures that
        positions are monitored even if IBKR's positions() API returns empty due
        to cache delays, connection issues, or API problems.

        Returns:
            list[PositionStatus]: List of position statuses

        Example:
            >>> positions = monitor.get_all_positions()
            >>> print(f"Open positions: {len(positions)}")
        """
        logger.info("Retrieving all open positions from database...")

        try:
            # 1. Get open trades from database (source of truth)
            from src.data.database import get_db_session
            from src.data.models import Trade

            with get_db_session() as session:
                from sqlalchemy import or_
                open_trades = session.query(Trade).filter(
                    Trade.exit_date.is_(None),
                    or_(Trade.tws_status.is_(None), Trade.tws_status != "Submitted"),  # Exclude only pending exit orders
                ).all()

            if not open_trades:
                logger.info("No open trades in database")
                return []

            logger.info(f"Found {len(open_trades)} open trades in database")

            # 2. Get positions from IBKR for current pricing
            ib_positions = self.ibkr_client.get_positions()
            logger.info(f"Found {len(ib_positions)} positions in IBKR")

            # Warn if discrepancy detected
            if len(open_trades) != len(ib_positions):
                logger.warning(
                    f"⚠ SYNC WARNING: Database shows {len(open_trades)} open trades "
                    f"but IBKR shows {len(ib_positions)} positions"
                )

            # 3. Build lookup map: (symbol, strike, expiration) -> IBKR position
            ibkr_map = {}
            for ib_pos in ib_positions:
                contract = ib_pos.contract
                # Check if it's an option
                is_option = isinstance(contract, Option) or (
                    hasattr(contract, 'right') and
                    hasattr(contract, 'strike') and
                    hasattr(contract, 'lastTradeDateOrContractMonth')
                )

                if is_option:
                    # Only include SHORT positions (negative quantity)
                    # Long positions (positive) are from erroneous BUY fills and should be ignored
                    if int(ib_pos.position) >= 0:
                        logger.warning(
                            f"⚠ Ignoring LONG position: {contract.symbol} "
                            f"${contract.strike} x{int(ib_pos.position)} "
                            f"(not a short put — may need manual cleanup)"
                        )
                        continue

                    key = (
                        contract.symbol,
                        float(contract.strike),
                        contract.lastTradeDateOrContractMonth
                    )
                    ibkr_map[key] = ib_pos

            # 4. Build position statuses using database + IBKR pricing
            position_statuses = []

            for trade in open_trades:
                # Format expiration to YYYYMMDD for matching
                exp_str = trade.expiration.strftime("%Y%m%d")
                key = (trade.symbol, float(trade.strike), exp_str)

                # Try to find matching IBKR position
                ib_pos = ibkr_map.get(key)

                if ib_pos:
                    # Have IBKR data - get current pricing
                    status = self._get_position_status(ib_pos)
                    if status:
                        position_statuses.append(status)
                else:
                    # No IBKR data - create status with stale pricing
                    logger.warning(
                        f"⚠ {trade.symbol} ${trade.strike} exp {exp_str} not found in IBKR - "
                        f"using entry premium (P&L will show $0.00)"
                    )
                    status = self._create_status_from_trade(trade)
                    position_statuses.append(status)

            # Enrich with entry stock prices for underlying drop detection
            self._enrich_entry_stock_prices(position_statuses, open_trades)

            logger.info(f"Built {len(position_statuses)} position statuses")
            return position_statuses

        except Exception as e:
            logger.error(f"Error getting positions: {e}", exc_info=True)
            return []

    def update_position(self, position_id: str) -> PositionStatus | None:
        """Update a specific position with current market data.

        Args:
            position_id: Position identifier

        Returns:
            PositionStatus: Updated position status or None

        Example:
            >>> status = monitor.update_position("POS123")
            >>> if status:
            ...     print(f"P&L: ${status.current_pnl:.2f}")
        """
        logger.debug(f"Updating position {position_id}...")

        try:
            # Get position from IBKR
            positions = self.ibkr_client.get_positions()

            for ib_pos in positions:
                if self._get_position_id(ib_pos) == position_id:
                    status = self._get_position_status(ib_pos)

                    # Update database if repository available
                    if status and self.position_repository:
                        self._save_position_to_db(status)

                    return status

            logger.warning(f"Position {position_id} not found")
            return None

        except Exception as e:
            logger.error(f"Error updating position {position_id}: {e}")
            return None

    def update_all_positions(self) -> list[PositionStatus]:
        """Update all positions with current market data.

        Returns:
            list[PositionStatus]: Updated position statuses

        Example:
            >>> statuses = monitor.update_all_positions()
            >>> for status in statuses:
            ...     print(f"{status.symbol}: {status.current_pnl_pct:.1%}")
        """
        logger.info("Updating all positions...")

        positions = self.get_all_positions()

        # Save to database
        if self.position_repository:
            for position in positions:
                self._save_position_to_db(position)

        self.last_update = datetime.now()
        logger.info(f"Updated {len(positions)} positions at {self.last_update}")

        return positions

    def check_alerts(self) -> list[PositionAlert]:
        """Check all positions for alert conditions.

        Alert Conditions:
        - Approaching profit target (within 10% of 50% target)
        - Approaching stop loss (within 20% of -200% stop)
        - Approaching expiration (<=3 DTE)

        Returns:
            list[PositionAlert]: List of active alerts

        Example:
            >>> alerts = monitor.check_alerts()
            >>> for alert in alerts:
            ...     if alert.severity == "critical":
            ...         print(f"CRITICAL: {alert.message}")
        """
        logger.debug("Checking for position alerts...")

        positions = self.get_all_positions()
        alerts = []

        for position in positions:
            # Check profit target (50% of max profit)
            profit_target = self.config.exit_rules.profit_target
            if position.current_pnl_pct >= profit_target * 0.9:
                alerts.append(
                    PositionAlert(
                        position_id=position.position_id,
                        alert_type="profit_target",
                        severity="info"
                        if position.current_pnl_pct < profit_target
                        else "warning",
                        message=(
                            f"{position.symbol} ${position.strike} {position.option_type}: "
                            f"Profit at {position.current_pnl_pct:.1%} "
                            f"(target: {profit_target:.1%})"
                        ),
                        current_value=position.current_pnl_pct,
                        threshold=profit_target,
                    )
                )

            # Check stop loss (-200% of premium)
            stop_loss = abs(self.config.exit_rules.stop_loss)
            if position.current_pnl_pct <= -stop_loss * 0.8:
                alerts.append(
                    PositionAlert(
                        position_id=position.position_id,
                        alert_type="stop_loss",
                        severity="critical"
                        if position.current_pnl_pct <= -stop_loss
                        else "warning",
                        message=(
                            f"{position.symbol} ${position.strike} {position.option_type}: "
                            f"Loss at {position.current_pnl_pct:.1%} "
                            f"(stop: {-stop_loss:.1%})"
                        ),
                        current_value=position.current_pnl_pct,
                        threshold=-stop_loss,
                    )
                )

            # Check time exit (3 days before expiration)
            time_exit_dte = self.config.exit_rules.time_exit_dte
            if position.dte <= time_exit_dte + 1:
                alerts.append(
                    PositionAlert(
                        position_id=position.position_id,
                        alert_type="time_exit",
                        severity="critical"
                        if position.dte <= time_exit_dte
                        else "warning",
                        message=(
                            f"{position.symbol} ${position.strike} {position.option_type}: "
                            f"{position.dte} DTE (time exit at {time_exit_dte} DTE)"
                        ),
                        current_value=position.dte,
                        threshold=time_exit_dte,
                    )
                )

            # Check assignment risk: near-money + approaching expiration
            if (
                position.dte <= 7
                and position.underlying_price is not None
                and position.strike > 0
            ):
                otm_pct = (position.underlying_price - position.strike) / position.underlying_price
                if otm_pct < 0.03:  # Less than 3% OTM (or ITM)
                    itm = position.underlying_price <= position.strike
                    severity = "critical" if itm or position.dte <= 3 else "warning"
                    alerts.append(
                        PositionAlert(
                            position_id=position.position_id,
                            alert_type="assignment_risk",
                            severity=severity,
                            message=(
                                f"{position.symbol} ${position.strike} {position.option_type}: "
                                f"ASSIGNMENT RISK — {'ITM' if itm else f'OTM {otm_pct:.1%}'} "
                                f"with {position.dte} DTE "
                                f"(stock=${position.underlying_price:.2f}) — "
                                f"consider rolling or closing"
                            ),
                            current_value=otm_pct,
                            threshold=0.03,
                        )
                    )

            # Check delta breach (option moving closer to ITM)
            if position.delta is not None:
                abs_delta = abs(position.delta)
                if abs_delta > 0.50:
                    alerts.append(
                        PositionAlert(
                            position_id=position.position_id,
                            alert_type="delta_breach",
                            severity="critical",
                            message=(
                                f"{position.symbol} ${position.strike} {position.option_type}: "
                                f"Delta breach: delta={position.delta:.2f} "
                                f"(>0.50, deep ITM risk)"
                            ),
                            current_value=abs_delta,
                            threshold=0.50,
                        )
                    )
                elif abs_delta > 0.30:
                    alerts.append(
                        PositionAlert(
                            position_id=position.position_id,
                            alert_type="delta_breach",
                            severity="warning",
                            message=(
                                f"{position.symbol} ${position.strike} {position.option_type}: "
                                f"Delta elevated: delta={position.delta:.2f} "
                                f"(>0.30, thesis weakening)"
                            ),
                            current_value=abs_delta,
                            threshold=0.30,
                        )
                    )

            # Check underlying stock price drop from entry
            if (
                position.underlying_price is not None
                and position.entry_stock_price is not None
                and position.entry_stock_price > 0
            ):
                drop_pct = (
                    (position.entry_stock_price - position.underlying_price)
                    / position.entry_stock_price
                )
                if drop_pct > 0.10:
                    alerts.append(
                        PositionAlert(
                            position_id=position.position_id,
                            alert_type="underlying_drop",
                            severity="critical",
                            message=(
                                f"{position.symbol} ${position.strike} {position.option_type}: "
                                f"Underlying down {drop_pct:.0%} from entry "
                                f"(${position.entry_stock_price:.2f} → ${position.underlying_price:.2f}) "
                                f"— review position"
                            ),
                            current_value=drop_pct,
                            threshold=0.10,
                        )
                    )
                elif drop_pct > 0.05:
                    alerts.append(
                        PositionAlert(
                            position_id=position.position_id,
                            alert_type="underlying_drop",
                            severity="warning",
                            message=(
                                f"{position.symbol} ${position.strike} {position.option_type}: "
                                f"Underlying down {drop_pct:.0%} from entry "
                                f"(${position.entry_stock_price:.2f} → ${position.underlying_price:.2f})"
                            ),
                            current_value=drop_pct,
                            threshold=0.05,
                        )
                    )

        # Check for option assignments (stock positions from assigned puts)
        try:
            assignment_events = self.assignment_detector.check_for_assignments()
            for event in assignment_events:
                alerts.append(
                    PositionAlert(
                        position_id=event.matched_trade_id or f"ASSIGN_{event.symbol}",
                        alert_type="assignment",
                        severity="critical",
                        message=(
                            f"POSSIBLE ASSIGNMENT: {event.symbol} — "
                            f"{event.shares} shares in account "
                            f"(avg cost ${event.avg_cost:.2f})"
                            + (
                                f" | Matched: ${event.matched_strike} put "
                                f"exp {event.matched_expiration}"
                                if event.matched_strike
                                else ""
                            )
                        ),
                        current_value=float(event.shares),
                        threshold=0.0,
                    )
                )
        except Exception as e:
            logger.error(f"Error during assignment detection: {e}")

        if alerts:
            logger.info(f"Generated {len(alerts)} alerts")
            for alert in alerts:
                logger.info(f"  [{alert.severity.upper()}] {alert.message}")

        return alerts

    def _get_position_status(self, ib_position) -> PositionStatus | None:
        """Get current status for an IBKR position.

        Args:
            ib_position: IBKR position object

        Returns:
            PositionStatus: Position status or None
        """
        try:
            contract = ib_position.contract
            position_size = int(ib_position.position)

            # Qualify contract to populate exchange field (fixes Error 321)
            qualified_contract = self.ibkr_client.qualify_contract(contract)
            if not qualified_contract:
                logger.warning(f"Could not qualify contract for {contract.symbol}")
                return None

            # Get market data using wrapper
            # Use 5-second timeout to allow sufficient time for market data to arrive
            import asyncio
            import math
            quote = asyncio.run(self.ibkr_client.get_quote(qualified_contract, timeout=5.0))

            # Calculate entry premium per contract
            # avgCost is the per-contract cost basis (negative for short positions)
            # For short options: avgCost = -(premium × 100)
            # So entry premium per contract = abs(avgCost) / 100
            entry_premium = abs(ib_position.avgCost) / 100

            # Get current premium per contract
            # Check for valid data (not None, not NaN, greater than 0)
            def is_valid(value):
                """Check if value is valid price data."""
                return value is not None and not math.isnan(value) and value > 0

            # Try multiple data sources in order of preference
            current_premium = None
            data_stale = False

            # 1. Prefer bid/ask midpoint (most accurate during market hours)
            if quote.is_valid and is_valid(quote.bid) and is_valid(quote.ask):
                current_premium = (quote.bid + quote.ask) / 2
                logger.debug(f"{contract.symbol} ${contract.strike}: Using bid/ask mid ${current_premium:.2f}")

            # 2. Fall back to last traded price (good when market closed)
            elif is_valid(quote.last):
                current_premium = quote.last
                logger.debug(f"{contract.symbol} ${contract.strike}: Using last price ${current_premium:.2f}")

            # 3. No data available - use entry premium as fallback
            else:
                current_premium = entry_premium
                data_stale = True
                logger.warning(
                    f"STALE DATA: No market data for {contract.symbol} ${contract.strike} "
                    f"(conId={contract.conId}, exchange={contract.exchange or 'unknown'}, "
                    f"tradingClass={getattr(contract, 'tradingClass', 'unknown')}, "
                    f"expiry={contract.lastTradeDateOrContractMonth}) "
                    f"- Using entry premium, P&L will show $0.00, stop loss INACTIVE"
                )

            # Market data cleanup handled by get_quote() (cancels subscription after each call)

            # Calculate P&L
            # For short positions: profit when premium decreases
            # P&L = (entry - current) × contracts × 100
            current_pnl = (entry_premium - current_premium) * abs(position_size) * 100
            current_pnl_pct = (
                (entry_premium - current_premium) / entry_premium
                if entry_premium > 0
                else 0
            )

            # Calculate days to expiration
            exp_date = datetime.strptime(
                contract.lastTradeDateOrContractMonth, "%Y%m%d"
            ).date()
            today = us_trading_date()
            dte = (exp_date - today).days

            # Calculate days held (would need trade entry date from database)
            days_held = 0  # Placeholder

            # Get Greeks (if available from quote)
            # Note: Greeks require live market data subscription
            # For snapshot quotes, Greeks are typically not available
            delta_value = getattr(quote, "delta", None) if hasattr(quote, 'delta') else None
            theta_value = getattr(quote, "theta", None) if hasattr(quote, 'theta') else None
            gamma_value = getattr(quote, "gamma", None) if hasattr(quote, 'gamma') else None
            vega_value = getattr(quote, "vega", None) if hasattr(quote, 'vega') else None

            # Get current underlying stock price (for drop detection)
            underlying_price = None
            try:
                from ib_insync import Stock
                stock_contract = Stock(contract.symbol, "SMART", "USD")
                stock_qualified = self.ibkr_client.qualify_contract(stock_contract)
                if stock_qualified:
                    stock_quote = asyncio.run(
                        self.ibkr_client.get_quote(stock_qualified, timeout=5.0)
                    )
                    if stock_quote.is_valid:
                        if is_valid(stock_quote.last):
                            underlying_price = stock_quote.last
                        elif is_valid(stock_quote.bid) and is_valid(stock_quote.ask):
                            underlying_price = (stock_quote.bid + stock_quote.ask) / 2
            except Exception as e:
                logger.debug(f"Could not fetch underlying price for {contract.symbol}: {e}")

            # Check alert conditions
            profit_target = self.config.exit_rules.profit_target
            stop_loss = abs(self.config.exit_rules.stop_loss)
            time_exit_dte = self.config.exit_rules.time_exit_dte

            return PositionStatus(
                position_id=self._get_position_id(ib_position),
                symbol=contract.symbol,
                strike=contract.strike,
                option_type=contract.right,
                expiration_date=contract.lastTradeDateOrContractMonth,
                contracts=abs(position_size),
                entry_premium=entry_premium,
                current_premium=current_premium,
                current_pnl=current_pnl,
                current_pnl_pct=current_pnl_pct,
                days_held=days_held,
                dte=dte,
                delta=delta_value,
                theta=theta_value,
                gamma=gamma_value,
                vega=vega_value,
                underlying_price=underlying_price,
                approaching_profit_target=current_pnl_pct >= profit_target * 0.9,
                approaching_stop_loss=current_pnl_pct <= -stop_loss * 0.8,
                approaching_expiration=dte <= time_exit_dte + 1,
                market_data_stale=data_stale,
            )

        except Exception as e:
            logger.error(f"Error getting position status: {e}", exc_info=True)
            return None

    def _get_position_id(self, ib_position) -> str:
        """Generate position identifier.

        Args:
            ib_position: IBKR position object

        Returns:
            str: Position ID
        """
        return position_key_from_contract(ib_position.contract)

    def _enrich_entry_stock_prices(
        self,
        statuses: list[PositionStatus],
        open_trades: list,
    ) -> None:
        """Look up entry stock prices from TradeEntrySnapshot for drop detection.

        Queries the database for entry snapshots and sets entry_stock_price
        on matching position statuses.

        Args:
            statuses: Position statuses to enrich
            open_trades: Open trade objects from database
        """
        try:
            from src.data.database import get_db_session
            from src.data.models import TradeEntrySnapshot

            with get_db_session() as session:
                for trade in open_trades:
                    snapshot = (
                        session.query(TradeEntrySnapshot)
                        .filter(TradeEntrySnapshot.trade_id == trade.id)
                        .first()
                    )
                    if snapshot and snapshot.stock_price:
                        pos_id = position_key_from_trade(trade)
                        for status in statuses:
                            if status.position_id == pos_id:
                                status.entry_stock_price = snapshot.stock_price
                                break
        except Exception as e:
            logger.debug(f"Could not look up entry stock prices: {e}")

    def _create_status_from_trade(self, trade) -> PositionStatus:
        """Create PositionStatus from database trade when IBKR has no data.

        This is used as a fallback when IBKR's positions() API doesn't return
        a position that exists in the database. Uses entry premium as current
        premium (resulting in $0 P&L), but still allows time exit evaluation
        since DTE can be calculated from expiration date.

        Args:
            trade: Trade object from database

        Returns:
            PositionStatus: Position status with stale pricing
        """
        from datetime import datetime

        # Calculate DTE (works without market data)
        today = us_trading_date()
        dte = (trade.expiration - today).days

        # Calculate days held
        entry_date = trade.entry_date.date() if hasattr(trade.entry_date, 'date') else trade.entry_date
        days_held = (today - entry_date).days

        # Use entry premium as current (P&L = 0 since we have no current data)
        current_premium = trade.entry_premium
        current_pnl = 0.0
        current_pnl_pct = 0.0

        # Get config thresholds
        profit_target = self.config.exit_rules.profit_target
        stop_loss = abs(self.config.exit_rules.stop_loss)
        time_exit_dte = self.config.exit_rules.time_exit_dte

        return PositionStatus(
            position_id=position_key_from_trade(trade),
            symbol=trade.symbol,
            strike=trade.strike,
            option_type=trade.option_type,
            expiration_date=trade.expiration.strftime("%Y%m%d"),
            contracts=trade.contracts,
            entry_premium=trade.entry_premium,
            current_premium=current_premium,
            current_pnl=current_pnl,
            current_pnl_pct=current_pnl_pct,
            days_held=days_held,
            dte=dte,
            delta=None,  # No Greeks without market data
            theta=None,
            gamma=None,
            vega=None,
            approaching_profit_target=False,  # Can't determine without current price
            approaching_stop_loss=False,  # Can't determine without current price
            approaching_expiration=dte <= time_exit_dte + 1,  # CAN determine time exit!
            market_data_stale=True,
        )

    def _save_position_to_db(self, status: PositionStatus) -> None:
        """Save position status to database.

        Args:
            status: Position status to save
        """
        if not self.position_repository:
            return

        try:
            # Parse expiration from YYYYMMDD format
            expiration_date = datetime.strptime(
                status.expiration_date, "%Y%m%d"
            ).date()

            # Estimate entry date from days_held
            entry_date = datetime.now() - timedelta(days=status.days_held)

            # Create or update position record
            position = Position(
                position_id=status.position_id,
                trade_id=status.position_id,
                symbol=status.symbol,
                strike=status.strike,
                expiration=expiration_date,
                option_type=status.option_type,
                contracts=status.contracts,
                entry_date=entry_date,
                entry_premium=status.entry_premium,
                dte=status.dte,
                current_premium=status.current_premium,
                current_pnl=status.current_pnl,
                current_pnl_pct=status.current_pnl_pct,
                last_updated=datetime.now(),
                delta=status.delta,
                gamma=status.gamma,
                theta=status.theta,
                vega=status.vega,
                approaching_stop_loss=status.approaching_stop_loss,
                approaching_profit_target=status.approaching_profit_target,
                approaching_expiration=status.approaching_expiration,
            )

            self.position_repository.create_or_update(position)
            logger.debug(f"Position {status.position_id} saved to database")

        except Exception as e:
            logger.error(f"Error saving position to database: {e}")

    def should_update(self) -> bool:
        """Check if positions should be updated based on interval.

        Returns:
            bool: True if update is due
        """
        if self.last_update is None:
            return True

        time_since_update = datetime.now() - self.last_update
        return time_since_update >= timedelta(minutes=self.update_interval_minutes)
