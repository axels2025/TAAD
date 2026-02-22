"""Position watcher for NakedTrader open positions.

Monitors open NakedTrader trades, checks bracket order status, detects
profit-take and stop-loss fills, and updates the database accordingly.
"""

from datetime import datetime

from loguru import logger
from rich.console import Console
from rich.table import Table
from sqlalchemy.orm import Session

from src.data.models import Trade
from src.data.repositories import TradeRepository
from src.nakedtrader.chain import get_underlying_price, INDEX_SYMBOLS
from src.nakedtrader.config import NakedTraderConfig
from src.tools.ibkr_client import IBKRClient
from src.utils.market_data import safe_field


def get_open_nt_trades(session: Session) -> list[Trade]:
    """Get all open NakedTrader trades from the database.

    Args:
        session: Database session.

    Returns:
        List of open trades with trade_strategy='nakedtrader'.
    """
    return (
        session.query(Trade)
        .filter(
            Trade.trade_strategy == "nakedtrader",
            Trade.exit_date.is_(None),
        )
        .order_by(Trade.entry_date.desc())
        .all()
    )


def check_bracket_status(
    client: IBKRClient,
    trade: Trade,
    session: Session,
) -> str:
    """Check if bracket orders (profit-take / stop-loss) have filled.

    Inspects IBKR open orders and completed trades to detect fills.
    Updates the Trade record if a fill is detected.

    Args:
        client: Connected IBKR client.
        trade: Open NakedTrader trade.
        session: Database session.

    Returns:
        Current bracket status string.
    """
    if trade.bracket_status != "active":
        return trade.bracket_status or "unknown"

    # Check IBKR trades for fills on our child orders
    for ibkr_trade in client.ib.trades():
        order_id = ibkr_trade.order.orderId
        status = ibkr_trade.orderStatus.status

        if status != "Filled":
            continue

        if trade.exit_order_id and order_id == trade.exit_order_id:
            # Profit-take filled
            fill_price = ibkr_trade.orderStatus.avgFillPrice
            _close_trade(trade, fill_price, "profit_take", session)
            return "profit_taken"

        if trade.stop_order_id and order_id == trade.stop_order_id:
            # Stop-loss filled
            fill_price = ibkr_trade.orderStatus.avgFillPrice
            _close_trade(trade, fill_price, "stop_loss", session)
            return "stopped"

    # Check if option has expired (past expiration date)
    now = datetime.now()
    if trade.expiration and now.date() > trade.expiration:
        _close_trade(trade, 0.0, "expired", session)
        return "expired"

    return "active"


def get_current_quote(
    client: IBKRClient,
    trade: Trade,
) -> dict | None:
    """Get current market quote for an open NakedTrader position.

    Args:
        client: Connected IBKR client.
        trade: Open trade.

    Returns:
        Dict with bid, ask, mid, delta or None if unavailable.
    """
    from src.nakedtrader.chain import TRADING_CLASS_PREFERENCES

    exp_str = trade.expiration.strftime("%Y%m%d") if trade.expiration else ""
    tc = TRADING_CLASS_PREFERENCES.get(trade.symbol, [trade.symbol])[0]

    contract = client.get_option_contract(
        symbol=trade.symbol,
        expiration=exp_str,
        strike=trade.strike,
        right="P",
        exchange="SMART",
        trading_class=tc,
    )
    qualified = client.qualify_contract(contract)
    if not qualified:
        return None

    ticker = client.ib.reqMktData(qualified, "", False, False)

    # Wait for data
    for _ in range(6):
        client.ib.sleep(0.5)
        if hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
            break

    result = {}
    bid = safe_field(ticker, "bid")
    ask = safe_field(ticker, "ask")

    if bid is not None and bid > 0:
        result["bid"] = bid
    if ask is not None and ask > 0:
        result["ask"] = ask
    if bid and ask:
        result["mid"] = (bid + ask) / 2
    elif bid:
        result["mid"] = bid

    if hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
        greeks = ticker.modelGreeks
        if greeks.delta is not None:
            result["delta"] = abs(greeks.delta)

    try:
        client.ib.cancelMktData(qualified)
    except Exception:
        pass

    return result if result else None


def display_positions(
    console: Console,
    trades: list[Trade],
    quotes: dict[str, dict | None],
    underlying_prices: dict[str, float | None],
) -> None:
    """Display open NakedTrader positions in a Rich table.

    Args:
        console: Rich console for output.
        trades: Open NakedTrader trades.
        quotes: Map of trade_id -> current quote.
        underlying_prices: Map of symbol -> current price.
    """
    table = Table(title="NakedTrader Open Positions")
    table.add_column("Symbol", style="cyan")
    table.add_column("Strike", justify="right")
    table.add_column("Exp", style="dim")
    table.add_column("DTE", justify="right")
    table.add_column("Entry$", justify="right")
    table.add_column("Current$", justify="right")
    table.add_column("P&L$", justify="right")
    table.add_column("P&L%", justify="right")
    table.add_column("Delta", justify="right")
    table.add_column("Status", style="bold")

    for trade in trades:
        quote = quotes.get(trade.trade_id)
        dte = (trade.expiration - datetime.now().date()).days if trade.expiration else "?"

        current_mid = quote.get("mid") if quote else None
        delta = quote.get("delta") if quote else None

        # P&L: sold at entry_premium, would buy back at current mid
        if current_mid is not None and trade.entry_premium:
            pnl = (trade.entry_premium - current_mid) * (trade.contracts or 1) * 100
            pnl_pct = (trade.entry_premium - current_mid) / trade.entry_premium
            pnl_str = f"${pnl:+.0f}"
            pnl_pct_str = f"{pnl_pct:+.0%}"
            pnl_style = "green" if pnl >= 0 else "red"
        else:
            pnl_str = "?"
            pnl_pct_str = "?"
            pnl_style = "dim"

        table.add_row(
            trade.symbol,
            f"${trade.strike:.0f}",
            trade.expiration.strftime("%m/%d") if trade.expiration else "?",
            str(dte),
            f"${trade.entry_premium:.2f}" if trade.entry_premium else "?",
            f"${current_mid:.2f}" if current_mid else "?",
            f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
            f"[{pnl_style}]{pnl_pct_str}[/{pnl_style}]",
            f"{delta:.4f}" if delta else "?",
            trade.bracket_status or "?",
        )

    console.print(table)

    if not trades:
        console.print("[dim]No open NakedTrader positions[/dim]")


def run_watch_cycle(
    client: IBKRClient,
    session: Session,
    config: NakedTraderConfig,
    console: Console,
) -> int:
    """Run a single watch cycle: check positions and display status.

    Args:
        client: Connected IBKR client.
        session: Database session.
        config: NakedTrader configuration.
        console: Rich console for output.

    Returns:
        Number of open positions found.
    """
    trades = get_open_nt_trades(session)

    if not trades:
        console.print("[dim]No open NakedTrader positions[/dim]")
        return 0

    # Check bracket status for each trade
    for trade in trades:
        status = check_bracket_status(client, trade, session)
        if status != trade.bracket_status:
            logger.info(f"{trade.trade_id}: status changed to {status}")

    session.commit()

    # Refresh - some may have closed
    trades = get_open_nt_trades(session)

    # Get current quotes
    quotes: dict[str, dict | None] = {}
    underlying_prices: dict[str, float | None] = {}

    for trade in trades:
        quotes[trade.trade_id] = get_current_quote(client, trade)
        if trade.symbol not in underlying_prices:
            underlying_prices[trade.symbol] = get_underlying_price(client, trade.symbol)

    display_positions(console, trades, quotes, underlying_prices)

    return len(trades)


def _close_trade(
    trade: Trade,
    exit_premium: float,
    reason: str,
    session: Session,
) -> None:
    """Close a trade record with exit data.

    Args:
        trade: Trade to close.
        exit_premium: Price paid to close (0 for expiration).
        reason: Exit reason (profit_take, stop_loss, expired).
        session: Database session.
    """
    now = datetime.now()
    trade.exit_date = now
    trade.exit_premium = exit_premium
    trade.exit_reason = reason
    trade.bracket_status = reason

    if trade.entry_premium:
        pnl_per_share = trade.entry_premium - exit_premium
        trade.profit_loss = pnl_per_share * (trade.contracts or 1) * 100
        trade.profit_pct = pnl_per_share / trade.entry_premium if trade.entry_premium else None
        trade.roi = trade.profit_pct

    if trade.entry_date:
        trade.days_held = (now - trade.entry_date).days

    logger.info(
        f"Closed {trade.trade_id}: {reason}, "
        f"exit=${exit_premium:.2f}, P&L=${trade.profit_loss:.2f}"
    )
