"""Bracket order placement for NakedTrader.

Places IBKR native parent-child bracket orders: a SELL parent with a
profit-take BUY child (and optional stop-loss BUY child). Uses the
ib_insync synchronous placeOrder pattern.
"""

from dataclasses import dataclass
from datetime import datetime

from ib_insync import LimitOrder, Option, Order
from loguru import logger

from src.nakedtrader.config import NakedTraderConfig
from src.nakedtrader.strike_selector import StrikeSelection
from src.tools.ibkr_client import IBKRClient


@dataclass
class BracketOrderResult:
    """Result of bracket order placement."""

    success: bool
    parent_order_id: int | None = None
    profit_take_order_id: int | None = None
    stop_loss_order_id: int | None = None
    fill_price: float | None = None
    fill_time: datetime | None = None
    error: str | None = None


def build_option_contract(
    client: IBKRClient,
    selection: StrikeSelection,
) -> Option | None:
    """Build and qualify the option contract for trading.

    Args:
        client: Connected IBKR client.
        selection: Strike selection result.

    Returns:
        Qualified Option contract, or None if qualification fails.
    """
    contract = client.get_option_contract(
        symbol=selection.symbol,
        expiration=selection.quote.expiration,
        strike=selection.quote.strike,
        right="P",
        exchange="SMART",
        trading_class=selection.trading_class,
    )
    qualified = client.qualify_contract(contract)
    if not qualified:
        logger.error(
            f"Could not qualify option contract: {selection.symbol} "
            f"${selection.quote.strike} {selection.quote.expiration}"
        )
    return qualified


def place_bracket_order(
    client: IBKRClient,
    contract: Option,
    selection: StrikeSelection,
    config: NakedTraderConfig,
    dry_run: bool = True,
) -> BracketOrderResult:
    """Place a bracket order: SELL parent + BUY profit-take + optional BUY stop-loss.

    Uses IBKR parent-child order linking. The last child has transmit=True
    to send the entire group atomically.

    Args:
        client: Connected IBKR client.
        contract: Qualified option contract.
        selection: Strike selection with entry premium and exit prices.
        config: NakedTrader configuration.
        dry_run: If True, simulate without placing real orders.

    Returns:
        BracketOrderResult with order IDs or error.
    """
    contracts_qty = config.instrument.contracts
    entry_premium = selection.quote.bid
    pt_price = selection.profit_take_price
    sl_price = selection.stop_loss_price

    if dry_run:
        logger.info(
            f"[DRY RUN] Would place bracket: "
            f"SELL {contracts_qty}x {selection.symbol} ${selection.quote.strike}P "
            f"@ ${entry_premium:.2f}, PT @ ${pt_price:.2f}"
            + (f", SL @ ${sl_price:.2f}" if sl_price else "")
        )
        return BracketOrderResult(
            success=True,
            error="dry_run",
        )

    client.ensure_connected()
    has_stop = sl_price is not None and config.exit.stop_loss_enabled

    # Parent order: SELL to open
    parent = LimitOrder(
        action="SELL",
        totalQuantity=contracts_qty,
        lmtPrice=entry_premium,
        tif="DAY",
        transmit=False,  # Don't send until children attached
    )

    parent_trade = client.ib.placeOrder(contract, parent)
    parent_id = parent_trade.order.orderId
    logger.info(f"Parent SELL order placed: orderId={parent_id}")

    # Child 1: Profit-take BUY
    profit_take = LimitOrder(
        action="BUY",
        totalQuantity=contracts_qty,
        lmtPrice=pt_price,
        tif="GTC",
        parentId=parent_id,
        transmit=not has_stop,  # Transmit if no stop-loss child follows
    )
    pt_trade = client.ib.placeOrder(contract, profit_take)
    pt_id = pt_trade.order.orderId
    logger.info(f"Profit-take BUY order placed: orderId={pt_id}, price=${pt_price:.2f}")

    # Child 2: Stop-loss BUY (optional)
    sl_id = None
    if has_stop:
        stop_loss = LimitOrder(
            action="BUY",
            totalQuantity=contracts_qty,
            lmtPrice=sl_price,
            tif="GTC",
            parentId=parent_id,
            transmit=True,  # Last child transmits the entire group
        )
        sl_trade = client.ib.placeOrder(contract, stop_loss)
        sl_id = sl_trade.order.orderId
        logger.info(f"Stop-loss BUY order placed: orderId={sl_id}, price=${sl_price:.2f}")

    return BracketOrderResult(
        success=True,
        parent_order_id=parent_id,
        profit_take_order_id=pt_id,
        stop_loss_order_id=sl_id,
    )


def wait_for_fill(
    client: IBKRClient,
    parent_order_id: int,
    timeout_seconds: int = 300,
) -> tuple[float | None, datetime | None]:
    """Wait for the parent SELL order to fill.

    Polls IBKR order status until filled or timeout.

    Args:
        client: Connected IBKR client.
        parent_order_id: Order ID of the parent SELL order.
        timeout_seconds: Maximum seconds to wait for fill.

    Returns:
        Tuple of (fill_price, fill_time) or (None, None) if not filled.
    """
    import time

    start = time.time()
    while (time.time() - start) < timeout_seconds:
        client.ib.sleep(2)

        for trade in client.ib.trades():
            if trade.order.orderId == parent_order_id:
                status = trade.orderStatus.status
                if status == "Filled":
                    fill_price = trade.orderStatus.avgFillPrice
                    fill_time = datetime.now()
                    logger.info(
                        f"Parent order {parent_order_id} filled "
                        f"@ ${fill_price:.2f}"
                    )
                    return fill_price, fill_time
                elif status in ("Cancelled", "Inactive"):
                    logger.warning(
                        f"Parent order {parent_order_id} {status}"
                    )
                    return None, None

        elapsed = int(time.time() - start)
        if elapsed % 30 == 0 and elapsed > 0:
            logger.debug(f"Waiting for fill... {elapsed}s elapsed")

    logger.warning(
        f"Parent order {parent_order_id} not filled after {timeout_seconds}s"
    )
    return None, None
