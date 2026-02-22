"""Exit management for automated position closing.

This module handles automated exits based on:
- Profit targets (50% of max profit)
- Stop losses (-200% of premium)
- Time-based exits (3 days before expiration)
- Emergency exits
- Exit reason logging
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from ib_insync import LimitOrder, MarketOrder
from loguru import logger

from src.config.baseline_strategy import BaselineStrategy
from src.data.models import Trade
from src.execution.position_monitor import PositionMonitor, PositionStatus
from src.utils.calc import calc_pnl, calc_pnl_pct
from src.utils.position_key import _normalize_right, position_key_from_trade
from src.utils.timezone import us_eastern_now
from src.tools.ibkr_client import IBKRClient


def round_to_penny(price: float) -> float:
    """Round price to nearest $0.01 (penny).

    Options can be traded in $0.01 increments. This ensures
    order prices are valid and not rejected by IBKR.

    Args:
        price: Price to round

    Returns:
        float: Price rounded to 2 decimal places

    Example:
        >>> round_to_penny(0.0909390384)
        0.09
        >>> round_to_penny(0.42633039739)
        0.43
    """
    return round(price, 2)


@dataclass
class ExitDecision:
    """Decision to exit a position.

    Attributes:
        should_exit: Whether to exit the position
        reason: Reason for exit
        exit_type: Type of exit (limit, market, emergency)
        limit_price: Limit price for exit order (if limit)
        urgency: Urgency level (low, medium, high, critical)
        message: Human-readable message
    """

    should_exit: bool
    reason: str
    exit_type: str = "limit"
    limit_price: float | None = None
    urgency: str = "low"
    message: str = ""


@dataclass
class ExitResult:
    """Result of exit attempt.

    Attributes:
        success: Whether exit was successful
        position_id: Position that was exited
        order_id: Exit order ID
        exit_price: Actual exit price (if filled)
        exit_reason: Reason for exit
        error_message: Error message (if failed)
    """

    success: bool
    position_id: str
    order_id: int | None = None
    exit_price: float | None = None
    exit_reason: str = ""
    error_message: str | None = None


class ExitManager:
    """Manage automated position exits.

    The ExitManager monitors positions and executes exits when:
    - Profit target reached (50% of max profit)
    - Stop loss triggered (-200% of premium)
    - Time exit reached (3 DTE)
    - Emergency exit requested

    Example:
        >>> manager = ExitManager(ibkr_client, position_monitor, config)
        >>> decisions = manager.evaluate_exits()
        >>> for decision in decisions:
        ...     if decision.should_exit:
        ...         result = manager.execute_exit(position_id, decision)
        ...         print(f"Exit: {result.exit_reason}")
    """

    def __init__(
        self,
        ibkr_client: IBKRClient,
        position_monitor: PositionMonitor,
        config: BaselineStrategy,
        dry_run: bool = False,
    ):
        """Initialize exit manager.

        Args:
            ibkr_client: Connected IBKR client
            position_monitor: Position monitor instance
            config: Strategy configuration
            dry_run: If True, prevent all DB writes and order placements
        """
        self.ibkr_client = ibkr_client
        self.position_monitor = position_monitor
        self.config = config
        self.dry_run = dry_run
        # Track positions with in-flight exit orders to prevent duplicates
        # position_id -> (order_id, exit_reason)
        self._exit_orders_placed: dict[str, tuple[int, str]] = {}

        # Reconcile any pending exits left over from a previous session
        if not dry_run:
            self._reconcile_pending_exits_on_startup()
        else:
            logger.info("Dry-run mode — skipping pending exit reconciliation")

        logger.info("Initialized ExitManager")

    @staticmethod
    def _find_trade_by_position_id(session, position_id: str) -> Optional["Trade"]:
        """Find a trade record by parsing a position_id (SYMBOL_STRIKE_YYYYMMDD_RIGHT).

        Falls back to exact trade_id match if the composite key lookup fails.
        """
        from src.data.models import Trade

        parts = position_id.split("_")
        if len(parts) == 4:
            symbol, strike_str, exp_str, right = parts
            try:
                strike = float(strike_str)
                from datetime import date as date_type
                expiration = date_type(
                    int(exp_str[:4]), int(exp_str[4:6]), int(exp_str[6:8])
                )
                # Normalize right so both "P"/"PUT" match DB option_type
                normalized = _normalize_right(right)
                option_types = [normalized]
                if normalized == "P":
                    option_types.append("PUT")
                elif normalized == "C":
                    option_types.append("CALL")
                trade = session.query(Trade).filter(
                    Trade.symbol == symbol,
                    Trade.strike == strike,
                    Trade.expiration == expiration,
                    Trade.option_type.in_(option_types),
                    Trade.exit_date.is_(None),
                ).first()
                if trade:
                    return trade
            except (ValueError, IndexError):
                pass

        # Fallback: exact trade_id match
        return session.query(Trade).filter(
            Trade.trade_id == position_id
        ).first()

    def evaluate_exits(self) -> dict[str, ExitDecision]:
        """Evaluate all positions for exit signals.

        Returns:
            dict: Map of position_id to ExitDecision

        Example:
            >>> decisions = manager.evaluate_exits()
            >>> for pos_id, decision in decisions.items():
            ...     if decision.should_exit:
            ...         print(f"Exit {pos_id}: {decision.reason}")
        """
        logger.info("Evaluating positions for exit signals...")

        positions = self.position_monitor.get_all_positions()
        decisions = {}

        for position in positions:
            decision = self._evaluate_position(position)
            decisions[position.position_id] = decision

            if decision.should_exit:
                logger.info(
                    f"EXIT SIGNAL: {position.symbol} - {decision.reason} "
                    f"(urgency: {decision.urgency})"
                )

        return decisions

    def execute_exit(
        self, position_id: str, decision: ExitDecision
    ) -> ExitResult:
        """Execute exit for a position.

        Args:
            position_id: Position to exit
            decision: Exit decision with details

        Returns:
            ExitResult: Result of exit attempt

        Example:
            >>> result = manager.execute_exit(position_id, decision)
            >>> if result.success:
            ...     print(f"Exited at ${result.exit_price}")
        """
        logger.info(f"Executing exit for {position_id}: {decision.reason}")

        # SAFETY: Prevent duplicate exit orders for the same position
        if position_id in self._exit_orders_placed:
            prev_order_id, prev_reason = self._exit_orders_placed[position_id]
            logger.warning(
                f"⚠ DUPLICATE EXIT BLOCKED: {position_id} already has exit order "
                f"(Order ID: {prev_order_id}, reason: {prev_reason}). Skipping to prevent over-closing."
            )
            return ExitResult(
                success=False,
                position_id=position_id,
                exit_reason=decision.reason,
                error_message=f"Exit order already placed (Order ID: {prev_order_id})",
            )

        try:
            # Get current position status
            position_status = self.position_monitor.update_position(position_id)

            if not position_status:
                return ExitResult(
                    success=False,
                    position_id=position_id,
                    exit_reason=decision.reason,
                    error_message="Position not found",
                )

            # CRITICAL: Validate position size against database
            from src.data.database import get_db_session

            with get_db_session() as session:
                trade_record = self._find_trade_by_position_id(session, position_id)

                if trade_record and trade_record.contracts != position_status.contracts:
                    logger.error(
                        f"⚠️ POSITION SIZE MISMATCH DETECTED!\n"
                        f"  Position: {position_status.symbol} ${position_status.strike}\n"
                        f"  Database says: {trade_record.contracts} contracts\n"
                        f"  IBKR says: {position_status.contracts} contracts\n"
                        f"  THIS COULD CAUSE OVER-CLOSING!\n"
                        f"  Using DATABASE quantity to prevent position reversal."
                    )

                    # SAFETY: Use database quantity, not IBKR quantity
                    # This prevents buying more contracts than we sold
                    position_status.contracts = trade_record.contracts

            # SAFETY: Verify the position is actually SHORT in IBKR before placing BUY
            ib_positions = self.ibkr_client.get_positions()
            for ib_pos in ib_positions:
                c = ib_pos.contract
                if (hasattr(c, 'symbol') and hasattr(c, 'strike') and
                    c.symbol == position_status.symbol and
                    float(c.strike) == float(position_status.strike)):
                    if int(ib_pos.position) >= 0:
                        logger.error(
                            f"⚠ BLOCKED: {position_status.symbol} ${position_status.strike} "
                            f"is LONG ({int(ib_pos.position)} contracts) in IBKR — "
                            f"cannot BUY to close. Needs manual cleanup."
                        )
                        return ExitResult(
                            success=False,
                            position_id=position_id,
                            exit_reason=decision.reason,
                            error_message=f"Position is LONG ({int(ib_pos.position)}), not short",
                        )
                    break

            # Create exit order (BUY to close short position)
            order = self._create_exit_order(
                position_status, decision.exit_type, decision.limit_price
            )

            # Get option contract with actual expiration date
            contract = self.ibkr_client.get_option_contract(
                symbol=position_status.symbol,
                expiration=position_status.expiration_date,
                strike=position_status.strike,
                right="P" if position_status.option_type == "P" else "C",
            )

            # Qualify contract
            qualified = self.ibkr_client.qualify_contract(contract)
            if not qualified:
                return ExitResult(
                    success=False,
                    position_id=position_id,
                    exit_reason=decision.reason,
                    error_message="Failed to qualify contract",
                )

            # Place exit order
            logger.info(
                f"Placing {decision.exit_type.upper()} exit order for "
                f"{position_status.symbol} ${position_status.strike} "
                f"{position_status.option_type}"
            )

            import asyncio
            trade = asyncio.run(self.ibkr_client.place_order(
                qualified,
                order,
                reason=f"{decision.exit_type.upper()} exit for {position_status.symbol}"
            ))

            # Record that we placed an exit order for this position
            self._exit_orders_placed[position_id] = (trade.order.orderId, decision.reason)

            # Mark position as "exit pending" in DB so get_all_positions() excludes it
            try:
                from src.data.database import get_db_session

                with get_db_session() as session:
                    trade_record = self._find_trade_by_position_id(session, position_id)
                    if trade_record:
                        trade_record.order_id = trade.order.orderId
                        trade_record.tws_status = "Submitted"
                        session.commit()
                        logger.info(
                            f"Marked {position_id} as exit-pending in DB "
                            f"(order_id={trade.order.orderId})"
                        )
            except Exception as e:
                logger.error(f"Failed to mark exit-pending in DB: {e}")

            # Wait for order to be processed and poll for fill status
            # Market orders can fill in milliseconds, but may show PendingSubmit initially
            max_wait_seconds = 30 if decision.exit_type == "market" else 10
            check_interval = 1  # Check every second

            logger.info(f"Waiting up to {max_wait_seconds}s for order to fill...")

            for i in range(max_wait_seconds):
                import asyncio
                asyncio.run(self.ibkr_client.sleep(check_interval))

                # Log current status for debugging
                if i == 0 or i % 5 == 0:  # Log every 5 seconds
                    logger.info(
                        f"Order status at {i+1}s: {trade.orderStatus.status} "
                        f"(Order ID: {trade.order.orderId})"
                    )

                # Check if order is in a terminal or working state
                status = trade.orderStatus.status

                if status == "Filled":
                    # Order filled - capture exit data immediately
                    exit_price = trade.orderStatus.avgFillPrice
                    logger.info(f"✓ Exit filled @ ${exit_price:.2f}")

                    # ============================================================
                    # Phase 2.6E Integration: Capture Exit Snapshot
                    # ============================================================
                    try:
                        from src.data.database import get_db_session
                        from src.services.exit_snapshot import ExitSnapshotService

                        # Update trade record in database with exit info
                        with get_db_session() as session:
                            # Find the trade record by composite key
                            trade_record = self._find_trade_by_position_id(session, position_id)

                            if trade_record:
                                # Update trade with exit details
                                trade_record.exit_date = us_eastern_now()
                                trade_record.exit_premium = exit_price
                                trade_record.exit_reason = decision.reason

                                # Calculate P&L
                                trade_record.profit_loss = calc_pnl(trade_record.entry_premium, exit_price, trade_record.contracts)
                                trade_record.profit_pct = calc_pnl_pct(trade_record.profit_loss, trade_record.entry_premium, trade_record.contracts)

                                session.commit()

                                # Capture comprehensive exit snapshot
                                exit_service = ExitSnapshotService(self.ibkr_client, session)
                                exit_snapshot = exit_service.capture_exit_snapshot(
                                    trade=trade_record,
                                    exit_premium=exit_price,
                                    exit_reason=decision.reason
                                )
                                exit_service.save_snapshot(exit_snapshot)

                                logger.info(
                                    f"✓ Exit snapshot captured (Win: {exit_snapshot.win}, "
                                    f"ROI: {exit_snapshot.roi_pct:.1%}, "
                                    f"Quality: {exit_snapshot.trade_quality_score:.2f})"
                                )
                            else:
                                logger.warning(f"Trade record not found for position {position_id}")

                    except Exception as snapshot_error:
                        # Don't fail exit if snapshot fails - log and continue
                        logger.error(
                            f"Failed to capture exit snapshot: {snapshot_error}",
                            exc_info=True
                        )

                    return ExitResult(
                        success=True,
                        position_id=position_id,
                        order_id=trade.order.orderId,
                        exit_price=exit_price,
                        exit_reason=decision.reason,
                    )

                elif status in ["Cancelled", "Inactive", "ApiCancelled"]:
                    # Order was cancelled or rejected
                    logger.error(f"Exit order {status}: {trade.orderStatus.whyHeld}")
                    return ExitResult(
                        success=False,
                        position_id=position_id,
                        exit_reason=decision.reason,
                        error_message=f"Order {status}: {trade.orderStatus.whyHeld}",
                    )

                elif status in ["PendingSubmit", "PreSubmitted", "Submitted"]:
                    # Order is working - keep waiting
                    continue

                else:
                    # Unknown status - log warning and keep waiting
                    logger.warning(f"Unexpected order status: {status}")
                    continue

            # Timeout - order didn't fill in time
            final_status = trade.orderStatus.status
            logger.warning(
                f"Exit order timeout after {max_wait_seconds}s - "
                f"final status: {final_status} (Order ID: {trade.order.orderId})"
            )

            # For PendingSubmit status after timeout, the order is likely rejected
            # (e.g., due to invalid tick size - Error 110)
            if final_status == "PendingSubmit":
                logger.error(
                    f"Order stuck in PendingSubmit after {max_wait_seconds}s - likely rejected. "
                    f"Cancelling order {trade.order.orderId}"
                )
                try:
                    self.ibkr_client.cancel_order(trade)
                except Exception as e:
                    logger.warning(f"Failed to cancel order: {e}")

                return ExitResult(
                    success=False,
                    position_id=position_id,
                    exit_reason=decision.reason,
                    error_message=f"Order stuck in PendingSubmit - likely rejected (check Error 110 logs)",
                )

            # For PreSubmitted/Submitted, order is working and may fill
            elif final_status in ["PreSubmitted", "Submitted"]:
                logger.info(
                    f"Order still working after timeout - "
                    f"treating as success (Order ID: {trade.order.orderId})"
                )
                return ExitResult(
                    success=True,
                    position_id=position_id,
                    order_id=trade.order.orderId,
                    exit_price=None,  # Not filled yet
                    exit_reason=decision.reason,
                )
            else:
                return ExitResult(
                    success=False,
                    position_id=position_id,
                    exit_reason=decision.reason,
                    error_message=f"Timeout - final status: {final_status}",
                )

        except Exception as e:
            logger.error(f"Error executing exit: {e}", exc_info=True)
            return ExitResult(
                success=False,
                position_id=position_id,
                exit_reason=decision.reason,
                error_message=str(e),
            )

    def emergency_exit_all(self) -> list[ExitResult]:
        """Execute emergency exit for all positions using market orders.

        Returns:
            list[ExitResult]: Results for each position

        Example:
            >>> results = manager.emergency_exit_all()
            >>> print(f"Exited {len(results)} positions")
        """
        logger.critical("=" * 80)
        logger.critical("🚨 EMERGENCY EXIT ALL POSITIONS - LIQUIDATING ALL HOLDINGS 🚨")
        logger.critical("=" * 80)

        positions = self.position_monitor.get_all_positions()
        logger.warning(f"Found {len(positions)} positions to liquidate")

        results = []
        success_count = 0
        failed_count = 0

        for i, position in enumerate(positions, 1):
            logger.warning(
                f"[{i}/{len(positions)}] Emergency exit: {position.symbol} "
                f"${position.strike} {position.option_type} "
                f"(Position ID: {position.position_id})"
            )

            decision = ExitDecision(
                should_exit=True,
                reason="emergency_exit",
                exit_type="market",
                urgency="critical",
                message=f"Emergency exit for {position.symbol}",
            )

            result = self.execute_exit(position.position_id, decision)
            results.append(result)

            if result.success:
                success_count += 1
                if result.exit_price:
                    logger.warning(
                        f"  ✓ Exit successful - filled @ ${result.exit_price:.2f} "
                        f"(Order ID: {result.order_id})"
                    )
                else:
                    logger.warning(
                        f"  ✓ Exit order placed successfully "
                        f"(Order ID: {result.order_id}) - waiting for fill"
                    )
            else:
                failed_count += 1
                logger.error(
                    f"  ✗ Exit failed: {result.error_message} "
                    f"(Position ID: {position.position_id})"
                )

        logger.critical("=" * 80)
        logger.critical(
            f"EMERGENCY EXIT COMPLETE: "
            f"{success_count} successful, {failed_count} failed out of {len(results)} total"
        )
        logger.critical("=" * 80)

        return results

    def check_pending_exits(self) -> dict[str, str]:
        """Check status of all pending exit orders and update DB accordingly.

        Called once per watch cycle. For each tracked exit order:
        - Filled: record exit in DB, remove from tracking
        - Cancelled/Inactive: clear DB markers so position is re-evaluable
        - Working (Submitted/PreSubmitted): no action, report status

        Returns:
            dict: position_id -> status string for display
        """
        if not self._exit_orders_placed:
            return {}

        # Process pending IB events so order statuses are current
        try:
            self.ibkr_client.ib.sleep(0.5)
        except Exception as e:
            logger.debug(f"ib.sleep during pending exit check: {e}")

        results = {}
        ib_trades = self.ibkr_client.get_trades()

        # Build lookup: order_id -> ib_trade
        trade_by_order = {}
        for t in ib_trades:
            trade_by_order[t.order.orderId] = t

        # Iterate over a copy since we may modify the dict
        for position_id, (order_id, exit_reason) in list(self._exit_orders_placed.items()):
            ib_trade = trade_by_order.get(order_id)

            if ib_trade is None:
                # Order not found in session — likely from a previous session
                # or already cleaned up. Clear DB markers.
                logger.warning(
                    f"Pending exit order {order_id} for {position_id} not found in IB trades — clearing"
                )
                self._clear_pending_exit_in_db(position_id)
                del self._exit_orders_placed[position_id]
                results[position_id] = "order_not_found"
                continue

            status = ib_trade.orderStatus.status

            if status == "Filled":
                exit_price = ib_trade.orderStatus.avgFillPrice
                logger.info(
                    f"Pending exit FILLED: {position_id} @ ${exit_price:.2f} "
                    f"(Order ID: {order_id})"
                )
                self._record_fill_in_db(position_id, exit_price, exit_reason)
                del self._exit_orders_placed[position_id]
                results[position_id] = f"filled@${exit_price:.2f}"

            elif status in ("Cancelled", "Inactive", "ApiCancelled"):
                logger.warning(
                    f"Pending exit {status}: {position_id} (Order ID: {order_id}) — "
                    f"position will be re-evaluated next cycle"
                )
                self._clear_pending_exit_in_db(position_id)
                del self._exit_orders_placed[position_id]
                results[position_id] = status.lower()

            else:
                # Working: Submitted, PreSubmitted, PendingSubmit
                results[position_id] = f"working ({status})"
                logger.debug(
                    f"Pending exit still working: {position_id} — {status} "
                    f"(Order ID: {order_id})"
                )

        return results

    def _record_fill_in_db(self, position_id: str, exit_price: float, exit_reason: str) -> None:
        if self.dry_run:
            logger.info(f"[DRY RUN] Would record fill for {position_id} @ ${exit_price:.2f}")
            return

        """Record a filled exit order in the database.

        Updates the trade with exit_date, exit_premium, P&L, and clears
        the pending order markers.

        Args:
            position_id: Position identifier
            exit_price: Fill price
            exit_reason: Reason for exit
        """
        try:
            from src.data.database import get_db_session

            with get_db_session() as session:
                trade_record = self._find_trade_by_position_id(session, position_id)
                if not trade_record:
                    logger.warning(f"Cannot record fill — trade not found for {position_id}")
                    return

                trade_record.exit_date = us_eastern_now()
                trade_record.exit_premium = exit_price
                trade_record.exit_reason = exit_reason
                trade_record.profit_loss = calc_pnl(trade_record.entry_premium, exit_price, trade_record.contracts)
                trade_record.profit_pct = calc_pnl_pct(trade_record.profit_loss, trade_record.entry_premium, trade_record.contracts)

                # Clear pending markers
                trade_record.tws_status = None

                session.commit()
                logger.info(
                    f"Recorded exit fill for {position_id}: "
                    f"P&L=${trade_record.profit_loss:.2f} ({trade_record.profit_pct:.1%})"
                )

                # Capture exit snapshot (best-effort)
                try:
                    from src.services.exit_snapshot import ExitSnapshotService
                    exit_service = ExitSnapshotService(self.ibkr_client, session)
                    exit_snapshot = exit_service.capture_exit_snapshot(
                        trade=trade_record,
                        exit_premium=exit_price,
                        exit_reason=exit_reason
                    )
                    exit_service.save_snapshot(exit_snapshot)
                    logger.info(
                        f"Exit snapshot captured (Win: {exit_snapshot.win}, "
                        f"ROI: {exit_snapshot.roi_pct:.1%})"
                    )
                except Exception as snap_err:
                    logger.error(f"Failed to capture exit snapshot: {snap_err}")

        except Exception as e:
            logger.error(f"Failed to record fill in DB for {position_id}: {e}", exc_info=True)

    def _clear_pending_exit_in_db(self, position_id: str) -> None:
        """Clear pending exit markers in DB so position becomes re-evaluable.

        Sets order_id and tws_status back to NULL.

        Args:
            position_id: Position identifier
        """
        if self.dry_run:
            logger.info(f"[DRY RUN] Would clear pending exit markers for {position_id}")
            return
        try:
            from src.data.database import get_db_session

            with get_db_session() as session:
                trade_record = self._find_trade_by_position_id(session, position_id)
                if trade_record:
                    trade_record.order_id = None
                    trade_record.tws_status = None
                    session.commit()
                    logger.info(f"Cleared pending exit markers for {position_id}")
        except Exception as e:
            logger.error(f"Failed to clear pending exit in DB for {position_id}: {e}")

    def _reconcile_pending_exits_on_startup(self) -> None:
        """Reconcile pending exits left over from a previous session.

        Called from __init__. Queries DB for trades with tws_status set but
        exit_date NULL, then matches against live IBKR trades by order_id.
        """
        try:
            from src.data.database import get_db_session

            with get_db_session() as session:
                pending_trades = session.query(Trade).filter(
                    Trade.tws_status.isnot(None),
                    Trade.exit_date.is_(None),
                ).all()

                if not pending_trades:
                    return

                logger.info(
                    f"Reconciling {len(pending_trades)} pending exit(s) from previous session"
                )

                # Get live trades from IBKR
                try:
                    ib_trades = self.ibkr_client.get_trades()
                    trade_by_order = {t.order.orderId: t for t in ib_trades}
                except Exception:
                    trade_by_order = {}

                for trade_record in pending_trades:
                    order_id = trade_record.order_id
                    position_id = position_key_from_trade(trade_record)

                    ib_trade = trade_by_order.get(order_id) if order_id else None

                    if ib_trade and ib_trade.orderStatus.status == "Filled":
                        # Order filled while we were away
                        exit_price = ib_trade.orderStatus.avgFillPrice
                        logger.info(
                            f"Reconcile: {position_id} order {order_id} FILLED @ ${exit_price:.2f}"
                        )
                        trade_record.exit_date = us_eastern_now()
                        trade_record.exit_premium = exit_price
                        trade_record.exit_reason = "reconciled_fill"
                        trade_record.profit_loss = calc_pnl(trade_record.entry_premium, exit_price, trade_record.contracts)
                        trade_record.profit_pct = calc_pnl_pct(trade_record.profit_loss, trade_record.entry_premium, trade_record.contracts)
                        trade_record.tws_status = None

                        # Capture exit snapshot (best-effort)
                        try:
                            from src.services.exit_snapshot import ExitSnapshotService
                            exit_service = ExitSnapshotService(self.ibkr_client, session)
                            exit_snapshot = exit_service.capture_exit_snapshot(
                                trade=trade_record,
                                exit_premium=exit_price,
                                exit_reason="reconciled_fill",
                            )
                            exit_service.save_snapshot(exit_snapshot)
                            logger.info(
                                f"  Exit snapshot captured (Win: {exit_snapshot.win}, "
                                f"ROI: {exit_snapshot.roi_pct:.1%})"
                            )
                        except Exception as snap_err:
                            logger.warning(f"  Exit snapshot failed for {position_id}: {snap_err}")

                    elif ib_trade and ib_trade.orderStatus.status in (
                        "Submitted", "PreSubmitted", "PendingSubmit"
                    ):
                        # Order still working — re-add to in-memory tracker
                        logger.info(
                            f"Reconcile: {position_id} order {order_id} still working "
                            f"({ib_trade.orderStatus.status})"
                        )
                        self._exit_orders_placed[position_id] = (
                            order_id, trade_record.tws_status or "unknown"
                        )

                    else:
                        # Order not found or cancelled — clear markers
                        status_str = ib_trade.orderStatus.status if ib_trade else "not_found"
                        logger.warning(
                            f"Reconcile: {position_id} order {order_id} {status_str} — "
                            f"clearing markers"
                        )
                        trade_record.order_id = None
                        trade_record.tws_status = None

                session.commit()
                logger.info("Pending exit reconciliation complete")

        except Exception as e:
            logger.error(f"Failed to reconcile pending exits on startup: {e}", exc_info=True)

    def _evaluate_position(self, position: PositionStatus) -> ExitDecision:
        """Evaluate a single position for exit.

        Args:
            position: Position status

        Returns:
            ExitDecision: Exit decision
        """
        # Guard: skip exit evaluation when market data is stale
        # Without live prices, P&L is $0 and stop loss would never trigger.
        # Explicitly flag this rather than silently holding.
        if position.market_data_stale:
            logger.warning(
                f"STALE DATA: {position.symbol} ${position.strike:.0f} — "
                f"no live market data, skipping exit evaluation "
                f"(stop loss NOT active for this position)"
            )
            return ExitDecision(
                should_exit=False,
                reason="stale_data",
                message=(
                    f"{position.symbol}: NO LIVE DATA — exit evaluation skipped "
                    f"(stop loss inactive)"
                ),
            )

        # Check profit target first (highest priority)
        if self._should_exit_profit_target(position):
            return ExitDecision(
                should_exit=True,
                reason="profit_target",
                exit_type="limit",
                limit_price=round_to_penny(position.current_premium * 1.01),  # Slightly above current
                urgency="medium",
                message=(
                    f"{position.symbol}: Profit target reached "
                    f"({position.current_pnl_pct:.1%})"
                ),
            )

        # Check stop loss (second priority)
        if self._should_exit_stop_loss(position):
            return ExitDecision(
                should_exit=True,
                reason="stop_loss",
                exit_type="market",  # Market order for stop loss
                urgency="high",
                message=(
                    f"{position.symbol}: Stop loss triggered "
                    f"({position.current_pnl_pct:.1%})"
                ),
            )

        # Check time exit (third priority)
        if self._should_exit_time(position):
            return ExitDecision(
                should_exit=True,
                reason="time_exit",
                exit_type="limit",
                limit_price=round_to_penny(position.current_premium * 1.02),
                urgency="medium",
                message=f"{position.symbol}: Time exit ({position.dte} DTE)",
            )

        # No exit signal
        return ExitDecision(
            should_exit=False,
            reason="holding",
            message=f"{position.symbol}: Holding position",
        )

    def _should_exit_profit_target(self, position: PositionStatus) -> bool:
        """Check if profit target reached.

        Args:
            position: Position status

        Returns:
            bool: True if should exit for profit target
        """
        target = self.config.exit_rules.profit_target
        return position.current_pnl_pct >= target

    def _should_exit_stop_loss(self, position: PositionStatus) -> bool:
        """Check if stop loss triggered.

        Args:
            position: Position status

        Returns:
            bool: True if should exit for stop loss
        """
        stop = abs(self.config.exit_rules.stop_loss)
        return position.current_pnl_pct <= -stop

    def _should_exit_time(self, position: PositionStatus) -> bool:
        """Check if time exit should trigger.

        Args:
            position: Position status

        Returns:
            bool: True if should exit for time
        """
        time_exit_dte = self.config.exit_rules.time_exit_dte
        return position.dte <= time_exit_dte

    def _create_exit_order(
        self, position: PositionStatus, order_type: str, limit_price: float | None
    ):
        """Create exit order.

        Args:
            position: Position to exit
            order_type: 'limit' or 'market'
            limit_price: Limit price (for limit orders)

        Returns:
            Order: IBKR order object
        """
        # BUY to close short position
        action = "BUY"
        quantity = position.contracts

        if order_type == "limit":
            if limit_price is None:
                limit_price = round_to_penny(position.current_premium * 1.01)
            else:
                # Always round provided limit_price to ensure valid tick size
                limit_price = round_to_penny(limit_price)

            order = LimitOrder(
                action=action, totalQuantity=quantity, lmtPrice=limit_price
            )
            order.tif = "DAY"  # Explicitly set Time-In-Force
            logger.debug(f"Created LIMIT exit order: {action} {quantity} @ ${limit_price:.2f}")

        else:  # market
            order = MarketOrder(action=action, totalQuantity=quantity)
            order.tif = "DAY"  # Explicitly set Time-In-Force
            logger.debug(f"Created MARKET exit order: {action} {quantity}")

        return order
