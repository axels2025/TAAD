"""Order reconciliation system for syncing database with TWS state.

This module provides institutional-grade reconciliation between:
- Database records (what we think happened)
- TWS state (what actually happened)

Solves the problem of database/TWS divergence:
- Orders that filled but database shows 'submitted'
- Orders that were cancelled but database doesn't know
- Missing fill prices, fill times, actual quantities
- Commission tracking for accurate P&L

Uses comprehensive IBKR methods:
- ib.orders() - Open orders
- ib.trades() - All trades this session
- ib.executions() - Execution details
- ib.fills() - Fill details with commissions
- ib.positions() - Current positions
"""

from dataclasses import dataclass, field
from datetime import date, datetime

from loguru import logger

from src.services.assignment_detector import AssignmentEvent
from src.tools.ibkr_client import IBKRClient
from src.utils.calc import calc_pnl, calc_pnl_pct
from src.utils.position_key import (
    generate_trade_id,
    position_key_from_contract,
    position_key_from_trade,
)
from src.utils.timezone import us_eastern_now, us_trading_date


@dataclass
class Discrepancy:
    """A detected discrepancy between database and TWS.

    Attributes:
        type: Type of discrepancy (STATUS_MISMATCH, FILL_PRICE_MISMATCH, etc.)
        field: Field name that differs
        db_value: Value in database
        tws_value: Value in TWS
        resolved: Whether the discrepancy was resolved
        resolution: How it was resolved
    """

    type: str
    field: str
    db_value: any
    tws_value: any
    resolved: bool = False
    resolution: str = ""


@dataclass
class ReconciledTrade:
    """Details of a reconciled trade.

    Attributes:
        symbol: Stock symbol
        order_id: IBKR order ID
        db_status: Status in database
        tws_status: Status in TWS
        fill_price: Fill price (None if not filled)
        commission: Commission charged
        discrepancy: Detected discrepancy (None if perfect match)
    """

    symbol: str
    order_id: int
    db_status: str
    tws_status: str
    fill_price: float | None = None
    commission: float | None = None
    discrepancy: Discrepancy | None = None


@dataclass
class ReconciliationReport:
    """Complete reconciliation report.

    Attributes:
        date: Date reconciled
        reconciled: List of reconciled trades
        orphans: Orders in TWS but not in database
        missing_in_tws: Orders in database but not in TWS
        total_discrepancies: Number of discrepancies found
        total_resolved: Number of discrepancies resolved
    """

    date: date
    reconciled: list[ReconciledTrade] = field(default_factory=list)
    orphans: list = field(default_factory=list)
    missing_in_tws: list = field(default_factory=list)

    @property
    def total_reconciled(self) -> int:
        """Total trades reconciled."""
        return len(self.reconciled)

    @property
    def total_discrepancies(self) -> int:
        """Total discrepancies found."""
        return sum(1 for r in self.reconciled if r.discrepancy)

    @property
    def total_resolved(self) -> int:
        """Total discrepancies resolved."""
        return sum(1 for r in self.reconciled if r.discrepancy and r.discrepancy.resolved)

    def add_reconciled(
        self,
        db_trade,
        ib_trade,
        discrepancy: Discrepancy | None = None,
        fill_price: float | None = None,
        commission: float | None = None,
    ) -> None:
        """Add a reconciled trade."""
        # Use provided fill_price/commission (from _reconcile_single),
        # fall back to orderStatus for current-session trades.
        if fill_price is None:
            avg = ib_trade.orderStatus.avgFillPrice
            fill_price = avg if avg and avg > 0 else None

        self.reconciled.append(
            ReconciledTrade(
                symbol=db_trade.symbol if hasattr(db_trade, "symbol") else "Unknown",
                order_id=db_trade.order_id if hasattr(db_trade, "order_id") else 0,
                db_status=db_trade.tws_status if hasattr(db_trade, "tws_status") and db_trade.tws_status else "Unknown",
                tws_status=ib_trade.orderStatus.status,
                fill_price=fill_price,
                commission=commission,
                discrepancy=discrepancy,
            )
        )

    def add_orphan(self, ib_trade) -> None:
        """Add an orphan order (in TWS, not in database)."""
        self.orphans.append(ib_trade)

    def add_missing_in_tws(self, db_trade) -> None:
        """Add a missing order (in database, not in TWS)."""
        self.missing_in_tws.append(db_trade)


@dataclass
class PositionMismatch:
    """A position quantity mismatch.

    Attributes:
        contract_key: Unique contract identifier
        db_quantity: Quantity in database
        ibkr_quantity: Quantity in IBKR
        difference: Difference (IBKR - DB)
    """

    contract_key: str
    db_quantity: int
    ibkr_quantity: int

    @property
    def difference(self) -> int:
        """Calculate difference."""
        return self.ibkr_quantity - self.db_quantity


@dataclass
class PositionReconciliationReport:
    """Report for position reconciliation.

    Attributes:
        quantity_mismatches: Positions with quantity differences
        in_ibkr_not_db: Positions in IBKR but not in database
        in_db_not_ibkr: Positions in database but not in IBKR
    """

    quantity_mismatches: list[PositionMismatch] = field(default_factory=list)
    in_ibkr_not_db: list = field(default_factory=list)
    in_db_not_ibkr: list = field(default_factory=list)
    assignments: list[AssignmentEvent] = field(default_factory=list)

    @property
    def has_discrepancies(self) -> bool:
        """Check if any discrepancies exist."""
        return bool(
            self.quantity_mismatches
            or self.in_ibkr_not_db
            or self.in_db_not_ibkr
            or self.assignments
        )

    def add_quantity_mismatch(
        self, contract_key: str, db_qty: int, ibkr_qty: int
    ) -> None:
        """Add a quantity mismatch."""
        self.quantity_mismatches.append(
            PositionMismatch(
                contract_key=contract_key,
                db_quantity=db_qty,
                ibkr_quantity=ibkr_qty,
            )
        )

    def add_in_ibkr_not_db(self, contract_key: str, ib_position) -> None:
        """Add a position in IBKR but not in database."""
        self.in_ibkr_not_db.append((contract_key, ib_position))

    def add_in_db_not_ibkr(self, contract_key: str, db_trade) -> None:
        """Add a position in database but not in IBKR."""
        self.in_db_not_ibkr.append((contract_key, db_trade))


class OrderReconciliation:
    """Reconcile database order records with TWS state.

    This solves the problem of database/TWS divergence by querying
    comprehensive data from IBKR and updating the database to match reality.

    Example:
        >>> reconciler = OrderReconciliation(ibkr_client, trade_repo)
        >>> report = await reconciler.sync_all_orders()
        >>> print(f"Reconciled {report.total_reconciled} trades")
        >>> print(f"Found {report.total_discrepancies} discrepancies")
    """

    def __init__(self, ibkr_client: IBKRClient, trade_repository=None):
        """Initialize order reconciliation.

        Args:
            ibkr_client: IBKR client for querying TWS
            trade_repository: Repository for database operations (optional)
        """
        self.client = ibkr_client
        self.trade_repo = trade_repository

        logger.debug("OrderReconciliation initialized")

    async def sync_all_orders(
        self,
        sync_date: date | None = None,
        include_filled: bool = True,
    ) -> ReconciliationReport:
        """Sync all orders from a given date (default: today).

        Steps:
        1. Query TWS for all orders, trades, executions, fills
        2. Match to database records by order_id
        3. Update database with actual status, fill price, commission
        4. Generate discrepancy report

        Args:
            sync_date: Date to sync (default: today)
            include_filled: Include filled orders in sync (default: True)

        Returns:
            ReconciliationReport with complete reconciliation details

        Example:
            >>> report = await reconciler.sync_all_orders()
            >>> for trade in report.reconciled:
            ...     if trade.discrepancy:
            ...         print(f"Discrepancy: {trade.discrepancy.type}")
        """
        if sync_date is None:
            sync_date = us_trading_date()

        logger.info(f"Starting order reconciliation for {sync_date}")

        report = ReconciliationReport(date=sync_date)

        # Get comprehensive data from IBKR
        logger.debug("Fetching trades from TWS (current session)...")
        ib_trades = self.client.get_trades()
        logger.info(f"TWS current session: {len(ib_trades)} trades")
        seen_order_ids = {t.order.orderId for t in ib_trades}

        # Merge open trades from prior sessions.
        # ib.trades() only returns the current API session, so orders placed
        # in earlier sessions that are still open won't appear.
        # ib.openTrades() returns Trade objects for ALL open orders.
        try:
            logger.debug("Fetching open trades from TWS (all sessions)...")
            open_trades = self.client.ib.openTrades()
            merged_open = 0
            for ot in open_trades:
                if ot.order.orderId not in seen_order_ids:
                    ib_trades.append(ot)
                    seen_order_ids.add(ot.order.orderId)
                    merged_open += 1
            if merged_open:
                logger.info(f"Merged {merged_open} open trades from prior sessions")
        except Exception as e:
            logger.warning(f"Failed to fetch open trades: {e}")

        # Also fetch completed orders from previous sessions.
        # reqCompletedOrders returns filled/cancelled orders from prior sessions.
        logger.debug("Fetching completed orders from TWS (all sessions)...")
        try:
            completed_trades = self.client.ib.reqCompletedOrders(apiOnly=False)
            logger.info(f"TWS completed orders (prior sessions): {len(completed_trades)}")

            # Merge, deduplicating by order ID
            for ct in completed_trades:
                if ct.order.orderId not in seen_order_ids:
                    ib_trades.append(ct)
                    seen_order_ids.add(ct.order.orderId)
        except Exception as e:
            logger.warning(f"Failed to fetch completed orders: {e}")

        logger.debug("Fetching executions from TWS...")
        ib_executions = self.client.get_executions()

        logger.debug("Fetching fills from TWS...")
        ib_fills = self.client.get_fills()

        # Fetch reqExecutions (active server request — returns fills with real
        # orderIds, permIds, and actual fill prices even for prior-session orders).
        # This is additive: if it fails, the existing flow works as before.
        req_exec_fills = []
        try:
            logger.debug("Fetching reqExecutions from TWS (server request)...")
            result = self.client.get_req_executions()
            req_exec_fills = list(result)
            logger.info(f"reqExecutions returned {len(req_exec_fills)} fills")
        except Exception as e:
            logger.warning(f"reqExecutions failed (non-fatal, continuing): {e}")

        # Build reqExecutions lookups by orderId, permId, and contract key
        req_exec_by_order_id = {}   # orderId -> list[Fill]
        req_exec_by_perm_id = {}    # permId  -> list[Fill]
        req_exec_by_contract = {}   # (symbol, strike, exp_date) -> list[Fill]
        for fill in req_exec_fills:
            ex = fill.execution
            oid = ex.orderId
            pid = ex.permId
            if oid and oid != 0:
                req_exec_by_order_id.setdefault(oid, []).append(fill)
            if pid and pid != 0:
                req_exec_by_perm_id.setdefault(pid, []).append(fill)
            # Contract key for fallback matching
            try:
                c = fill.contract
                exp_str = str(c.lastTradeDateOrContractMonth)
                if len(exp_str) == 8:
                    from datetime import date as _d
                    exp = _d(int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8]))
                else:
                    from datetime import date as _d
                    exp = _d.fromisoformat(exp_str)
                ckey = (c.symbol, float(c.strike), exp)
                req_exec_by_contract.setdefault(ckey, []).append(fill)
            except Exception:
                pass

        # Enrich completed orders that have orderId=0 or avgFillPrice=0
        # by looking up their permId in reqExecutions.
        if req_exec_fills:
            enriched_count = 0
            for ib_trade in ib_trades:
                order = ib_trade.order
                status = ib_trade.orderStatus

                # Restore real orderId from reqExecutions via permId
                if (not order.orderId or order.orderId == 0) and order.permId:
                    perm_fills = req_exec_by_perm_id.get(order.permId, [])
                    if perm_fills:
                        real_oid = perm_fills[0].execution.orderId
                        if real_oid and real_oid != 0:
                            logger.debug(
                                f"Enriched orderId: permId={order.permId} -> "
                                f"orderId={real_oid} ({ib_trade.contract.symbol})"
                            )
                            order.orderId = real_oid
                            enriched_count += 1

                # Restore fill price from reqExecutions if avgFillPrice=0
                if (not status.avgFillPrice or status.avgFillPrice == 0):
                    # Try by orderId first, then permId
                    fills_for_order = []
                    if order.orderId and order.orderId != 0:
                        fills_for_order = req_exec_by_order_id.get(order.orderId, [])
                    if not fills_for_order and order.permId:
                        fills_for_order = req_exec_by_perm_id.get(order.permId, [])

                    if fills_for_order:
                        # Weighted average fill price
                        total_shares = sum(f.execution.shares for f in fills_for_order)
                        if total_shares > 0:
                            wavg = sum(
                                f.execution.avgPrice * f.execution.shares
                                for f in fills_for_order
                            ) / total_shares
                            logger.debug(
                                f"Enriched fill price: orderId={order.orderId} -> "
                                f"${wavg:.4f} ({ib_trade.contract.symbol})"
                            )
                            status.avgFillPrice = wavg

            if enriched_count:
                logger.info(f"Enriched {enriched_count} completed orders with real orderIds from reqExecutions")

        # Build lookup maps
        executions_by_order = self._group_executions_by_order(ib_executions)
        fills_by_order = self._group_fills_by_order(ib_fills)

        # Merge reqExecutions fills into fills_by_order so _reconcile_single()
        # has access to commission and fill data from reqExecutions.
        for fill in req_exec_fills:
            oid = fill.execution.orderId
            if oid and oid != 0:
                if oid not in fills_by_order:
                    fills_by_order[oid] = []
                # Avoid duplicates (same execId)
                existing_exec_ids = {
                    f.execution.execId for f in fills_by_order[oid]
                    if hasattr(f, 'execution') and hasattr(f.execution, 'execId')
                }
                if fill.execution.execId not in existing_exec_ids:
                    fills_by_order[oid].append(fill)

        # Get all orders from database (if repository available)
        if self.trade_repo:
            db_trades = self.trade_repo.get_trades_by_date(sync_date)
            db_by_order_id = {t.order_id: t for t in db_trades if t.order_id}
            logger.info(
                f"DB trades for {sync_date}: {len(db_trades)} total, "
                f"{len(db_by_order_id)} with order_id"
            )
            for t in db_trades:
                logger.debug(f"  DB: order_id={t.order_id}, {t.symbol}, entry={t.entry_date}")

            # Build contract-based lookup for DB trades as fallback.
            # IBKR doesn't preserve orderId across sessions (returns 0),
            # so we match by canonical key (symbol, strike, expiration, right)
            # when orderId fails. Uses position_key_from_trade/contract to
            # normalize P/PUT and C/CALL to the same format.
            db_by_contract = {}
            for t in db_trades:
                try:
                    db_by_contract[position_key_from_trade(t)] = t
                except Exception:
                    pass

            # Reconcile each trade from IBKR
            reconciled_db_ids = set()
            for ib_trade in ib_trades:
                order_id = ib_trade.order.orderId
                db_trade = None

                # Try matching by orderId first
                if order_id and order_id != 0 and order_id in db_by_order_id:
                    db_trade = db_by_order_id[order_id]
                else:
                    # Fallback: match by canonical contract key
                    # (symbol, strike, expiration, right — with P/PUT normalization)
                    try:
                        key = position_key_from_contract(ib_trade.contract)
                        db_trade = db_by_contract.get(key)
                        if db_trade:
                            logger.info(
                                f"Matched {ib_trade.contract.symbol} by contract details "
                                f"(IBKR permId={ib_trade.order.permId}, "
                                f"DB order_id={db_trade.order_id})"
                            )
                    except Exception:
                        pass

                if db_trade:
                    reconciled_db_ids.add(db_trade.id)
                    executions = executions_by_order.get(order_id, [])
                    fills = fills_by_order.get(order_id, [])

                    discrepancy, fill_price, commission = self._reconcile_single(
                        db_trade,
                        ib_trade,
                        executions,
                        fills,
                    )
                    report.add_reconciled(db_trade, ib_trade, discrepancy, fill_price, commission)
                else:
                    # Order in TWS but not in database (orphan)
                    report.add_orphan(ib_trade)
                    sym = ib_trade.contract.symbol if hasattr(ib_trade, 'contract') else 'Unknown'
                    logger.warning(
                        f"Orphan order found: orderId={order_id}, "
                        f"permId={ib_trade.order.permId} ({sym})"
                    )

            # Check for database orders not in TWS order history.
            # Cross-reference against IBKR positions — if the position exists,
            # the order was filled and TWS just purged the old order record.
            ib_positions = self.client.get_positions()
            ib_position_keys = set()
            for pos in ib_positions:
                try:
                    if hasattr(pos.contract, 'strike') and pos.contract.strike:
                        ib_position_keys.add(position_key_from_contract(pos.contract))
                except Exception:
                    pass

            for db_trade in db_trades:
                if db_trade.id in reconciled_db_ids:
                    continue  # Already matched

                # Skip trades that are already closed — nothing to sync
                if db_trade.exit_date is not None:
                    logger.debug(
                        f"Order {db_trade.order_id} ({db_trade.symbol}) not in TWS "
                        f"but already closed (exit_reason={db_trade.exit_reason}), skipping"
                    )
                    continue

                # Check if this trade has a live position in IBKR
                try:
                    trade_key = position_key_from_trade(db_trade)
                    if trade_key in ib_position_keys:
                        logger.debug(
                            f"Order {db_trade.order_id} ({db_trade.symbol}) not in TWS "
                            f"order history but position exists — filled, no action needed"
                        )
                        continue  # Position exists, order was filled, all good
                except Exception:
                    pass

                report.add_missing_in_tws(db_trade)
                exp_str = ""
                if db_trade.expiration:
                    try:
                        from datetime import date as _date, datetime as _dt
                        exp = db_trade.expiration
                        if isinstance(exp, str):
                            exp = _date.fromisoformat(exp)
                        elif isinstance(exp, _dt):
                            exp = exp.date()
                        exp_str = f" {exp.strftime('%b%d')!s}'{exp.strftime('%y')}"
                    except Exception:
                        exp_str = f" {db_trade.expiration}"
                strike_str = f" {db_trade.strike}" if db_trade.strike else ""
                opt_type = f" {db_trade.option_type[0]}" if db_trade.option_type else ""
                logger.warning(
                    f"Order not in TWS and no matching position: {db_trade.order_id} "
                    f"({db_trade.symbol}{exp_str}{strike_str}{opt_type})"
                )
        else:
            logger.warning("No trade repository provided, skipping database sync")

            # Still report on TWS orders
            for ib_trade in ib_trades:
                logger.info(
                    f"TWS Order: {ib_trade.order.orderId} - "
                    f"{ib_trade.contract.symbol if hasattr(ib_trade, 'contract') else 'Unknown'} - "
                    f"{ib_trade.orderStatus.status}"
                )

        logger.info(
            f"Sync complete: {report.total_reconciled} synced, "
            f"{report.total_discrepancies} discrepancies, "
            f"{len(report.orphans)} orphans, "
            f"{len(report.missing_in_tws)} not in TWS history"
        )

        return report

    async def import_orphan_orders(
        self,
        orphan_trades: list,
        dry_run: bool = True
    ) -> int:
        """Import orphan orders from IBKR into database.

        Args:
            orphan_trades: List of IBKR Trade objects that are not in database
            dry_run: If True, only simulate import (don't actually create records)

        Returns:
            Number of trades imported

        Example:
            >>> report = await reconciler.sync_all_orders()
            >>> if report.orphans:
            ...     imported = await reconciler.import_orphan_orders(report.orphans, dry_run=False)
            ...     print(f"Imported {imported} orphan orders")
        """
        if not self.trade_repo:
            logger.error("No trade repository provided - cannot import orphans")
            return 0

        imported_count = 0

        for ib_trade in orphan_trades:
            try:
                contract = ib_trade.contract
                order = ib_trade.order
                status = ib_trade.orderStatus

                # Only import filled orders (ignore working/cancelled orders)
                if status.status != "Filled":
                    logger.debug(
                        f"Skipping non-filled orphan order {order.orderId}: {status.status}"
                    )
                    continue

                # Extract contract details
                # Normalize option_type: IBKR returns "P"/"C", DB uses "PUT"/"CALL"
                symbol = contract.symbol
                strike = float(contract.strike) if hasattr(contract, "strike") else 0.0
                expiration = contract.lastTradeDateOrContractMonth
                raw_right = contract.right if hasattr(contract, "right") else "P"
                option_type = "PUT" if raw_right in ("P", "PUT") else "CALL"

                # Parse expiration date (format: YYYYMMDD)
                from datetime import datetime
                if len(expiration) == 8:
                    exp_date = datetime.strptime(expiration, "%Y%m%d").date()
                else:
                    logger.warning(f"Invalid expiration format: {expiration}")
                    continue

                # Get fill details
                fill_price = status.avgFillPrice
                filled_qty = max(int(status.filled), 1)  # At least 1 contract

                # Generate trade_id — use permId as suffix when orderId is 0
                # (completed orders from prior sessions lose their orderId)
                suffix_id = order.orderId if order.orderId else None
                suffix_str = str(order.permId) if not suffix_id and order.permId else ""
                trade_id = generate_trade_id(
                    symbol, strike, expiration, option_type,
                    order_id=suffix_id, suffix=suffix_str,
                )

                # Check for duplicate trade_id before inserting
                if self.trade_repo.get_by_id(trade_id):
                    logger.info(
                        f"Skipping duplicate orphan: {trade_id} already in DB"
                    )
                    continue

                # Calculate OTM% and DTE (approximate)
                # For index options, try to use strike as reference
                otm_pct = None
                dte = (exp_date - us_trading_date()).days

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would import: {symbol} ${strike} {exp_date} "
                        f"x{filled_qty} @ ${fill_price:.2f} (order_id={order.orderId})"
                    )
                else:
                    # Create Trade record
                    from src.data.models import Trade

                    new_trade = Trade(
                        trade_id=trade_id,
                        symbol=symbol,
                        strike=strike,
                        expiration=exp_date,
                        option_type=option_type,
                        entry_date=us_eastern_now(),  # Use current time as entry (unknown actual)
                        entry_premium=fill_price,
                        contracts=filled_qty,
                        otm_pct=otm_pct,
                        dte=dte,
                        order_id=order.orderId if order.orderId != 0 else None,
                        tws_status=status.status,
                        fill_time=us_eastern_now(),  # Use current time (unknown actual)
                        reconciled_at=us_eastern_now(),
                    )

                    self.trade_repo.create(new_trade)
                    self.trade_repo.session.flush()  # Get new_trade.id for snapshot FK
                    logger.info(
                        f"Imported orphan order: {symbol} ${strike} {exp_date} "
                        f"x{filled_qty} @ ${fill_price:.2f} (order_id={order.orderId})"
                    )

                    # Capture entry snapshot (best-effort)
                    try:
                        from src.services.entry_snapshot import EntrySnapshotService
                        snap_service = EntrySnapshotService(self.client)
                        snapshot = snap_service.capture_entry_snapshot(
                            trade_id=new_trade.id,
                            opportunity_id=None,
                            symbol=symbol,
                            strike=strike,
                            expiration=exp_date,
                            option_type=option_type,
                            entry_premium=fill_price,
                            contracts=filled_qty,
                            stock_price=0,  # Unknown for orphan imports
                            dte=dte,
                            source="reconciliation_order",
                        )
                        snap_service.save_snapshot(snapshot, self.trade_repo.session)
                        logger.debug(f"  Entry snapshot captured for order {order.orderId}")
                    except Exception as snap_err:
                        logger.warning(f"  Entry snapshot failed for order {order.orderId}: {snap_err}")

                imported_count += 1

            except Exception as e:
                # Use str(e) to avoid loguru interpreting curly braces
                # in SQLAlchemy error messages as format placeholders
                err_msg = str(e).replace("{", "{{").replace("}", "}}")
                logger.error(f"Failed to import orphan order {order.orderId}: {err_msg}")
                self.trade_repo.session.rollback()  # Reset session after IntegrityError
                continue

        if dry_run:
            logger.info(f"[DRY RUN] Would import {imported_count} orphan orders")
        else:
            logger.info(f"Successfully imported {imported_count} orphan orders")

        return imported_count

    def _reconcile_single(
        self,
        db_trade,
        ib_trade,
        executions: list,
        fills: list,
    ) -> Discrepancy | None:
        """Reconcile a single order with comprehensive data.

        Args:
            db_trade: Trade record from database
            ib_trade: Trade object from TWS
            executions: List of executions for this order
            fills: List of fills for this order

        Returns:
            Discrepancy if found, None if perfect match
        """
        tws_status = ib_trade.orderStatus.status
        tws_fill_price = ib_trade.orderStatus.avgFillPrice
        tws_filled_qty = ib_trade.orderStatus.filled

        # For completed orders from previous sessions, avgFillPrice may be 0.
        # Try to extract fill price from the trade's fills or the order itself.
        if (not tws_fill_price or tws_fill_price == 0) and hasattr(ib_trade, 'fills') and ib_trade.fills:
            # Use average of fill prices from the trade's own fill list
            fill_prices = [f.execution.avgPrice for f in ib_trade.fills if hasattr(f, 'execution') and f.execution.avgPrice]
            if fill_prices:
                tws_fill_price = sum(fill_prices) / len(fill_prices)
                logger.debug(f"Got fill price ${tws_fill_price:.2f} from trade.fills for {db_trade.symbol}")

        # Also try lmtPrice from the order as last resort (for limit orders, fill >= limit)
        if (not tws_fill_price or tws_fill_price == 0) and hasattr(ib_trade, 'order') and ib_trade.order.lmtPrice:
            tws_fill_price = ib_trade.order.lmtPrice
            logger.debug(f"Using limit price ${tws_fill_price:.2f} as fill price proxy for {db_trade.symbol}")

        # Calculate total commission from fills
        total_commission = sum(
            f.commissionReport.commission
            for f in fills
            if f.commissionReport
        )

        # Also check trade's own fills for commission
        if not total_commission and hasattr(ib_trade, 'fills') and ib_trade.fills:
            total_commission = sum(
                f.commissionReport.commission
                for f in ib_trade.fills
                if hasattr(f, 'commissionReport') and f.commissionReport and f.commissionReport.commission < 1e6
            )

        updates = {}
        discrepancy = None

        # Check status mismatch — Trade model uses tws_status, not status
        db_status = db_trade.tws_status if hasattr(db_trade, "tws_status") and db_trade.tws_status else "Unknown"

        if tws_status == "Filled" and db_status not in ("filled", "Filled"):
            updates["tws_status"] = tws_status
            updates["reconciled_at"] = us_eastern_now()
            if tws_fill_price and tws_fill_price > 0:
                updates["entry_premium"] = tws_fill_price
            if tws_filled_qty:
                updates["contracts"] = int(tws_filled_qty)
            updates["fill_time"] = self._get_fill_time(executions)
            if total_commission:
                updates["commission"] = total_commission

            discrepancy = Discrepancy(
                type="STATUS_MISMATCH",
                field="status",
                db_value=db_status,
                tws_value="Filled",
                resolved=True,
                resolution=f"Updated to {tws_status}, fill price ${tws_fill_price:.2f}",
            )

            logger.info(
                f"Status mismatch resolved: Order {db_trade.order_id} "
                f"was {db_status}, now Filled @ ${tws_fill_price:.2f}"
            )

        elif tws_status == "Cancelled" and db_status not in ("cancelled", "Cancelled"):
            updates["tws_status"] = tws_status
            updates["reconciled_at"] = us_eastern_now()

            discrepancy = Discrepancy(
                type="STATUS_MISMATCH",
                field="status",
                db_value=db_status,
                tws_value="Cancelled",
                resolved=True,
                resolution="Updated to Cancelled",
            )

            logger.info(
                f"Status mismatch resolved: Order {db_trade.order_id} "
                f"was {db_status}, now Cancelled"
            )

        # Check fill price mismatch (for already-filled orders)
        if tws_fill_price and hasattr(db_trade, "entry_premium") and db_trade.entry_premium:
            price_diff = abs(tws_fill_price - db_trade.entry_premium)
            if price_diff > 0.01:
                updates["entry_premium"] = tws_fill_price
                updates["fill_price_discrepancy"] = price_diff
                updates["reconciled_at"] = us_eastern_now()

                discrepancy = Discrepancy(
                    type="FILL_PRICE_MISMATCH",
                    field="entry_premium",
                    db_value=db_trade.entry_premium,
                    tws_value=tws_fill_price,
                    resolved=True,
                    resolution=f"Updated fill price ${db_trade.entry_premium:.2f} → ${tws_fill_price:.2f}",
                )

                logger.warning(
                    f"Fill price mismatch: Order {db_trade.order_id} "
                    f"DB=${db_trade.entry_premium:.2f}, TWS=${tws_fill_price:.2f}"
                )

        # Check commission (may not have been captured)
        if total_commission and (
            not hasattr(db_trade, "commission")
            or not db_trade.commission
            or db_trade.commission == 0
        ):
            updates["commission"] = total_commission
            updates["reconciled_at"] = us_eastern_now()

            logger.info(
                f"Commission added: Order {db_trade.order_id} - ${total_commission:.2f}"
            )

        # Apply updates to database
        if updates and self.trade_repo:
            try:
                for field, value in updates.items():
                    if hasattr(db_trade, field):
                        setattr(db_trade, field, value)
                self.trade_repo.update(db_trade)
                logger.info(f"Database updated for order {db_trade.order_id}: {list(updates.keys())}")
            except Exception as e:
                logger.error(f"Failed to update database for order {db_trade.order_id}: {e}")

        return discrepancy, tws_fill_price, total_commission

    def _group_executions_by_order(self, executions) -> dict[int, list]:
        """Group executions by order ID.

        Args:
            executions: List of execution objects from IBKR

        Returns:
            Dictionary mapping order_id to list of executions
        """
        result = {}
        for exec in executions:
            order_id = exec.orderId
            if order_id not in result:
                result[order_id] = []
            result[order_id].append(exec)
        return result

    def _group_fills_by_order(self, fills) -> dict[int, list]:
        """Group fills by order ID.

        Args:
            fills: List of fill objects from IBKR

        Returns:
            Dictionary mapping order_id to list of fills
        """
        result = {}
        for fill in fills:
            order_id = fill.execution.orderId
            if order_id not in result:
                result[order_id] = []
            result[order_id].append(fill)
        return result

    def _get_fill_time(self, executions: list) -> datetime | None:
        """Get fill time from executions.

        Args:
            executions: List of executions for an order

        Returns:
            Fill time (last execution time) or None if no executions
        """
        if not executions:
            return None
        # Use the last execution time
        return max(e.time for e in executions)

    async def reconcile_positions(self) -> PositionReconciliationReport:
        """End-of-day position reconciliation.

        Compares IBKR positions (ib.positions()) with database positions
        to catch any discrepancies.

        Returns:
            PositionReconciliationReport with any mismatches found

        Example:
            >>> report = await reconciler.reconcile_positions()
            >>> if report.has_discrepancies:
            ...     print("Position mismatches detected!")
        """
        logger.info("Starting position reconciliation")

        report = PositionReconciliationReport()

        # Get positions from IBKR
        ib_positions = self.client.get_positions()

        # Partition positions by type — only options have strike/expiration
        ib_option_positions = []
        ib_stock_positions = []
        for pos in ib_positions:
            sec_type = getattr(pos.contract, "secType", None)
            if sec_type == "OPT":
                ib_option_positions.append(pos)
            elif sec_type == "STK":
                ib_stock_positions.append(pos)
            else:
                logger.debug(f"Skipping non-OPT/STK position: {pos.contract.symbol} ({sec_type})")

        if ib_stock_positions:
            logger.info(
                f"Found {len(ib_stock_positions)} stock positions "
                f"(will check for assignments)"
            )

        # Get positions from database (if repository available)
        if self.trade_repo:
            db_positions = self.trade_repo.get_open_positions()

            # Build lookup by contract key (OPTIONS only — stocks have no strike/expiration)
            ib_by_key = {
                self._position_key(p.contract): p for p in ib_option_positions
            }
            db_by_key = {self._trade_key(t): t for t in db_positions}

            logger.debug(
                f"Reconcile keys — IBKR: {sorted(ib_by_key.keys())} | "
                f"DB: {sorted(db_by_key.keys())}"
            )

            # Check for mismatches
            for key, ib_pos in ib_by_key.items():
                if key in db_by_key:
                    db_pos = db_by_key[key]
                    db_qty = (
                        db_pos.contracts
                        if hasattr(db_pos, "contracts")
                        else 0
                    )
                    # For short positions (sold options), IBKR shows negative quantities
                    # Database stores positive contract counts, so compare absolute values
                    ibkr_qty = abs(int(ib_pos.position))
                    if ibkr_qty != db_qty:
                        report.add_quantity_mismatch(key, db_qty, ibkr_qty)
                        logger.warning(
                            f"Position mismatch: {key} - DB={db_qty}, IBKR={ibkr_qty} (raw: {ib_pos.position})"
                        )
                else:
                    report.add_in_ibkr_not_db(key, ib_pos)
                    logger.warning(f"Position in IBKR but not DB: {key}")

            for key, db_pos in db_by_key.items():
                if key not in ib_by_key:
                    # Check if the option expired
                    if self._close_if_expired(db_pos):
                        logger.info(f"Expired option closed: {key}")
                    else:
                        report.add_in_db_not_ibkr(key, db_pos)
                        logger.warning(f"Position in DB but not IBKR: {key}")

            # Check stock positions for assignments
            if ib_stock_positions and db_positions:
                assignments = self._detect_assignments(
                    ib_stock_positions, db_positions
                )
                report.assignments = assignments

            # Check if any tracked stock positions have been sold
            self._check_stock_position_closures(ib_stock_positions)
        else:
            logger.warning("No trade repository provided, skipping database comparison")

            # Still report on IBKR positions
            for ib_pos in ib_positions:
                logger.info(
                    f"IBKR Position: {ib_pos.contract.symbol} "
                    f"{ib_pos.contract.strike if hasattr(ib_pos.contract, 'strike') else ''} - "
                    f"{ib_pos.position} contracts"
                )

        logger.info(
            f"Position reconciliation complete: "
            f"{len(report.quantity_mismatches)} mismatches, "
            f"{len(report.in_ibkr_not_db)} in IBKR not DB, "
            f"{len(report.in_db_not_ibkr)} in DB not IBKR"
        )

        return report

    def _close_if_expired(self, trade) -> bool:
        """Close a position if its option has expired.

        When an option expires worthless, IBKR removes it with no closing
        trade. This detects that and closes the DB record accordingly.

        Args:
            trade: Trade model with expiration date

        Returns:
            True if the trade was closed as expired
        """
        if not trade.expiration or not self.trade_repo:
            return False

        exp = trade.expiration
        if isinstance(exp, str):
            try:
                exp = date.fromisoformat(exp)
            except ValueError:
                return False
        elif isinstance(exp, datetime):
            exp = exp.date()

        now_et = us_eastern_now()
        today = now_et.date()
        if exp > today:
            return False  # Not yet expired
        if exp == today and now_et.hour < 16:
            return False  # Expires today but market hasn't closed yet

        # Option has expired — close it
        trade.exit_date = datetime.combine(exp, datetime.min.time().replace(hour=16))
        trade.exit_premium = 0.0  # Expired worthless
        trade.exit_reason = "expired"
        try:
            entry_d = trade.entry_date.date() if isinstance(trade.entry_date, datetime) else trade.entry_date
            trade.days_held = (exp - entry_d).days if entry_d else 0
        except (AttributeError, TypeError):
            trade.days_held = 0

        # P&L = full premium kept (entry_premium * contracts * 100)
        if trade.entry_premium and trade.contracts:
            trade.profit_loss = trade.entry_premium * trade.contracts * 100
            trade.profit_pct = 1.0  # 100% of premium kept
            trade.roi = 1.0

        try:
            self.trade_repo.session.commit()
            logger.info(
                f"Closed expired option: {trade.symbol} {trade.strike} "
                f"exp={exp}, P/L=${trade.profit_loss:.2f}"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to close expired option: {e}")
            self.trade_repo.session.rollback()
            return False

    async def import_orphan_positions(
        self,
        dry_run: bool = True
    ) -> int:
        """Import orphan positions from IBKR into database.

        This is safer than import_orphan_orders because it works directly with
        actual positions rather than trying to reconstruct from order history.

        Args:
            dry_run: If True, only simulate import (don't actually create records)

        Returns:
            Number of positions imported

        Example:
            >>> imported = await reconciler.import_orphan_positions(dry_run=False)
            >>> print(f"Imported {imported} orphan positions")
        """
        if not self.trade_repo:
            logger.error("No trade repository provided - cannot import positions")
            return 0

        # Get positions from IBKR
        ib_positions = self.client.get_positions()

        # Get existing positions from database
        db_positions = self.trade_repo.get_open_positions()

        # Build lookup by contract key
        db_by_key = {self._trade_key(t): t for t in db_positions}

        imported_count = 0

        # Fetch VIX and SPY once before the loop (not per position)
        import asyncio
        from datetime import datetime, date
        vix_at_import = None
        spy_at_import = None
        try:
            from ib_insync import Index
            vix_contract = Index("VIX", "CBOE")
            vix_qualified = self.client.qualify_contract(vix_contract)
            if vix_qualified:
                vix_quote = asyncio.run(self.client.get_quote(vix_qualified, timeout=3.0))
                vix_at_import = vix_quote.last if vix_quote.last and vix_quote.last > 0 else None
        except Exception as e:
            logger.warning(f"Could not fetch VIX: {e}")

        try:
            spy_contract = self.client.get_stock_contract("SPY")
            spy_qualified = self.client.qualify_contract(spy_contract)
            if spy_qualified:
                spy_quote = asyncio.run(self.client.get_quote(spy_qualified, timeout=3.0))
                spy_at_import = spy_quote.last if spy_quote.last and spy_quote.last > 0 else None
        except Exception as e:
            logger.warning(f"Could not fetch SPY: {e}")

        for ib_pos in ib_positions:
            try:
                contract = ib_pos.contract
                position_qty = ib_pos.position

                # Only import option positions (stock positions are handled
                # by assignment detection in reconcile_positions())
                if contract.secType != "OPT":
                    logger.info(
                        f"Skipping non-option position: {contract.symbol} "
                        f"({contract.secType}) — handled by assignment detection"
                    )
                    continue

                # Create position key
                pos_key = self._position_key(contract)

                # Skip if already in database
                if pos_key in db_by_key:
                    logger.debug(f"Position already in database: {pos_key}")
                    continue

                # Extract contract details
                # Normalize option_type: IBKR returns "P"/"C", DB uses "PUT"/"CALL"
                symbol = contract.symbol
                strike = float(contract.strike)
                expiration_str = contract.lastTradeDateOrContractMonth
                raw_right = contract.right
                option_type = "PUT" if raw_right in ("P", "PUT") else "CALL"

                # Parse expiration date (format: YYYYMMDD)
                if len(expiration_str) == 8:
                    exp_date = datetime.strptime(expiration_str, "%Y%m%d").date()
                else:
                    logger.warning(f"Invalid expiration format for {symbol}: {expiration_str}")
                    continue

                # Calculate DTE
                dte = (exp_date - us_trading_date()).days

                # Get ACTUAL entry premium from avgCost
                # avgCost is per-contract cost basis (negative for short positions)
                # For short options: avgCost = -(premium × 100)
                # So entry premium per contract = abs(avgCost) / 100
                actual_entry_premium = abs(ib_pos.avgCost) / 100

                # Get current stock price to calculate OTM%
                try:
                    stock_contract = self.client.get_stock_contract(symbol)
                    stock_qualified = self.client.qualify_contract(stock_contract)
                    if stock_qualified:
                        stock_quote = asyncio.run(self.client.get_quote(stock_qualified, timeout=3.0))
                        current_stock_price = stock_quote.last if stock_quote.last and stock_quote.last > 0 else None

                        if current_stock_price:
                            # Calculate current OTM% (not entry OTM, but best we can do)
                            if option_type == "P":
                                otm_pct = (current_stock_price - strike) / current_stock_price
                            else:  # CALL
                                otm_pct = (strike - current_stock_price) / current_stock_price
                        else:
                            otm_pct = None
                    else:
                        otm_pct = None
                except Exception as e:
                    logger.warning(f"Could not fetch stock price for {symbol}: {e}")
                    otm_pct = None

                # Generate unique trade_id — check for duplicates
                trade_id = generate_trade_id(symbol, strike, expiration_str, option_type, suffix="imported")
                existing = self.trade_repo.get_by_id(trade_id)
                if existing:
                    if dry_run:
                        logger.info(
                            f"[DRY RUN] Would update existing: {pos_key} "
                            f"(contracts: {existing.contracts}→{abs(int(position_qty))}, "
                            f"premium: ${existing.entry_premium:.2f}→${actual_entry_premium:.2f})"
                        )
                    else:
                        # Update existing record to match IBKR
                        existing.contracts = abs(int(position_qty))
                        existing.entry_premium = actual_entry_premium
                        existing.reconciled_at = us_eastern_now()
                        if otm_pct is not None:
                            existing.otm_pct = otm_pct
                        existing.dte = dte
                        # Position is open in IBKR — clear any exit data
                        if existing.exit_date is not None:
                            logger.info(
                                f"Re-opening {pos_key}: clearing exit_date={existing.exit_date}, "
                                f"exit_premium={existing.exit_premium}"
                            )
                            existing.exit_date = None
                            existing.exit_premium = None
                            existing.exit_reason = None
                            existing.profit_loss = None
                            existing.profit_pct = None
                        self.trade_repo.session.flush()
                        logger.info(
                            f"Updated existing orphan: {pos_key} "
                            f"(x{abs(int(position_qty))} contracts, premium=${actual_entry_premium:.2f})"
                        )
                    imported_count += 1
                    continue

                if dry_run:
                    logger.info(
                        f"[DRY RUN] Would import: {pos_key} "
                        f"(x{abs(position_qty)} contracts, premium=${actual_entry_premium:.2f}, "
                        f"OTM={f'{otm_pct:.1%}' if otm_pct is not None else 'N/A'} current, DTE={dte})"
                    )
                else:
                    # Create Trade record
                    from src.data.models import Trade

                    new_trade = Trade(
                        trade_id=trade_id,
                        symbol=symbol,
                        strike=strike,
                        expiration=exp_date,
                        option_type=option_type,
                        entry_date=us_eastern_now(),  # Unknown actual entry time - using import time
                        entry_premium=actual_entry_premium,  # ACTUAL from avgCost
                        contracts=abs(int(position_qty)),  # Convert to positive integer
                        otm_pct=otm_pct,  # Current OTM% (not entry OTM%)
                        dte=dte,
                        order_id=None,  # No order ID available from position data
                        tws_status="Filled",  # Position exists, so it must be filled
                        reconciled_at=us_eastern_now(),
                        # Store market data at import time (not entry time)
                        vix_at_entry=vix_at_import,  # Actually "at import" not "at entry"
                        spy_price_at_entry=spy_at_import,  # Actually "at import" not "at entry"
                        # Use ai_reasoning to store import metadata
                        ai_reasoning=f"IMPORTED_POSITION: Reconciled from IBKR on {us_eastern_now().strftime('%Y-%m-%d')}. "
                                    f"Entry premium=${actual_entry_premium:.2f} (actual from avgCost). "
                                    f"OTM%={f'{otm_pct:.1%}' if otm_pct is not None else 'N/A'} (current, not entry). "
                                    f"VIX={vix_at_import}, SPY={spy_at_import} (at import, not entry). "
                                    f"No entry snapshot available. Learning engine should use with caution."
                    )

                    self.trade_repo.create(new_trade)
                    self.trade_repo.session.flush()  # Get new_trade.id for snapshot FK
                    logger.info(
                        f"Imported orphan position: {pos_key} "
                        f"(x{abs(position_qty)} contracts, premium=${actual_entry_premium:.2f}, "
                        f"OTM={f'{otm_pct:.1%}' if otm_pct is not None else 'N/A'} current, DTE={dte})"
                    )

                    # Capture entry snapshot (best-effort, don't fail the import)
                    try:
                        from src.services.entry_snapshot import EntrySnapshotService

                        snap_service = EntrySnapshotService(self.client)
                        snapshot = snap_service.capture_entry_snapshot(
                            trade_id=new_trade.id,
                            opportunity_id=None,
                            symbol=symbol,
                            strike=strike,
                            expiration=exp_date,
                            option_type=option_type,
                            entry_premium=actual_entry_premium,
                            contracts=abs(int(position_qty)),
                            stock_price=current_stock_price or 0,
                            dte=dte,
                            source="reconciliation",
                        )
                        snap_service.save_snapshot(snapshot, self.trade_repo.session)
                        logger.debug(f"  ✓ Entry snapshot captured for {symbol}")
                    except Exception as snap_err:
                        logger.warning(f"  ⚠ Entry snapshot failed for {symbol}: {snap_err}")

                imported_count += 1

            except Exception as e:
                logger.error(f"Failed to import position {pos_key}: {e}")
                try:
                    self.trade_repo.session.rollback()
                except Exception:
                    pass
                continue

        if dry_run:
            logger.info(f"[DRY RUN] Would import {imported_count} orphan positions")
        else:
            logger.info(f"Successfully imported {imported_count} orphan positions")

        return imported_count

    def _detect_assignments(
        self,
        stock_positions: list,
        db_positions: list,
    ) -> list[AssignmentEvent]:
        """Detect option assignments from IBKR stock positions.

        When a naked put is assigned, IBKR removes the option position and
        adds a long stock position (100 shares per contract). This method
        matches stock positions to open PUT trades by symbol.

        Args:
            stock_positions: IBKR stock positions (secType='STK')
            db_positions: Open trades from database

        Returns:
            List of AssignmentEvent for each detected assignment
        """
        # Build lookup of open PUT trades by symbol
        open_puts_by_symbol: dict[str, list] = {}
        for trade in db_positions:
            opt_type = getattr(trade, "option_type", "")
            if str(opt_type).upper() in ("PUT", "P"):
                open_puts_by_symbol.setdefault(trade.symbol, []).append(trade)

        if not open_puts_by_symbol:
            return []

        assignments = []
        for pos in stock_positions:
            contract = pos.contract
            symbol = contract.symbol
            shares = int(pos.position)

            # Only flag long stock (short puts get assigned as long stock)
            if shares <= 0:
                continue

            # Assignment always results in multiples of 100 shares
            if shares % 100 != 0:
                continue

            # Check if we have open puts on this symbol
            matching_puts = open_puts_by_symbol.get(symbol, [])
            if not matching_puts:
                logger.debug(
                    f"Stock position {symbol} x{shares} — no open puts, skipping"
                )
                continue

            # Match to the most recent open put (by entry_date)
            matching_puts.sort(
                key=lambda t: t.entry_date if t.entry_date else datetime.min,
                reverse=True,
            )
            trade = matching_puts[0]

            exp_str = ""
            if trade.expiration:
                exp = trade.expiration
                if hasattr(exp, "strftime"):
                    exp_str = exp.strftime("%Y-%m-%d")
                else:
                    exp_str = str(exp)

            event = AssignmentEvent(
                symbol=symbol,
                shares=shares,
                avg_cost=pos.avgCost,
                matched_trade_id=trade.trade_id,
                matched_strike=trade.strike,
                matched_expiration=exp_str,
            )
            assignments.append(event)

            logger.warning(
                f"Assignment detected: {symbol} x{shares} shares "
                f"(avg cost ${pos.avgCost:.2f}) — "
                f"matched to {trade.trade_id} "
                f"${trade.strike} put exp {exp_str}"
            )

        return assignments

    def _check_stock_position_closures(self, ib_stock_positions: list) -> None:
        """Check if any tracked stock positions have been sold.

        Compares open StockPosition records against current IBKR stock positions.
        If a stock position is gone from IBKR, it was likely sold.

        Args:
            ib_stock_positions: Current IBKR stock positions (secType='STK')
        """
        try:
            from src.data.repositories import StockPositionRepository
            from src.services.stock_position_service import StockPositionService

            session = self.trade_repo.session
            stock_repo = StockPositionRepository(session)
            open_stock_positions = stock_repo.get_open_positions()

            if not open_stock_positions:
                return

            # Build set of symbols with stock positions in IBKR
            ibkr_stock_symbols = set()
            for pos in ib_stock_positions:
                ibkr_stock_symbols.add(pos.contract.symbol)

            svc = StockPositionService(session)

            for sp in open_stock_positions:
                if sp.symbol not in ibkr_stock_symbols:
                    # Stock disappeared from IBKR — it was sold
                    sale_price = self._find_stock_sale_price(sp.symbol)
                    if sale_price:
                        svc.close_position(sp, sale_price, "sold")
                        session.commit()
                        logger.info(
                            f"Stock position closed: {sp.symbol} x{sp.shares} "
                            f"sold @ ${sale_price:.2f}"
                        )
                    else:
                        logger.warning(
                            f"Stock {sp.symbol} gone from IBKR but no sale price found — "
                            f"check IBKR executions manually"
                        )

        except Exception as e:
            logger.error(f"Error checking stock position closures: {e}")

    def _find_stock_sale_price(self, symbol: str) -> float | None:
        """Attempt to find the sale price for a stock from IBKR executions.

        Args:
            symbol: Stock symbol to look up

        Returns:
            Average sale price per share, or None if not found
        """
        try:
            fills = self.client.get_fills()
            stock_fills = [
                f for f in fills
                if hasattr(f, 'contract')
                and f.contract.symbol == symbol
                and getattr(f.contract, 'secType', None) == 'STK'
                and hasattr(f, 'execution')
                and f.execution.side == 'SLD'
            ]

            if stock_fills:
                # Weighted average price
                total_shares = sum(f.execution.shares for f in stock_fills)
                if total_shares > 0:
                    wavg = sum(
                        f.execution.avgPrice * f.execution.shares
                        for f in stock_fills
                    ) / total_shares
                    return wavg
        except Exception as e:
            logger.warning(f"Failed to find stock sale price for {symbol}: {e}")

        return None

    @staticmethod
    def _normalize_right(value: str) -> str:
        """Normalize option right/type to single-char IBKR format (P/C)."""
        v = str(value).upper().strip()
        if v in ("PUT", "P"):
            return "P"
        if v in ("CALL", "C"):
            return "C"
        return v

    def _position_key(self, contract) -> str:
        """Create unique key for position matching.

        Args:
            contract: IBKR contract object

        Returns:
            Canonical position key string
        """
        return position_key_from_contract(contract)

    def _trade_key(self, trade) -> str:
        """Create unique key for trade matching.

        Args:
            trade: Database trade object

        Returns:
            Canonical position key string
        """
        return position_key_from_trade(trade)
