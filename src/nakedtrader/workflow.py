"""NakedTrader workflow orchestrator.

Coordinates the full daily trade flow: config loading, market open wait,
chain retrieval, strike selection, order placement, and trade recording.
"""

import time
from datetime import datetime, time as dt_time, timedelta
from zoneinfo import ZoneInfo

from loguru import logger
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.nakedtrader.chain import (
    get_chain_with_greeks,
    get_underlying_price,
    get_valid_expirations,
)
from src.nakedtrader.config import NakedTraderConfig
from src.nakedtrader.order_manager import (
    BracketOrderResult,
    build_option_contract,
    place_bracket_order,
    wait_for_fill,
)
from src.nakedtrader.strike_selector import StrikeSelection, select_strike
from src.nakedtrader.trade_recorder import record_trade
from src.services.market_calendar import MarketCalendar, MarketSession
from src.tools.ibkr_client import IBKRClient

ET = ZoneInfo("America/New_York")


def wait_for_market_open(
    console: Console,
    config: NakedTraderConfig,
) -> None:
    """Wait for market open + configured delay.

    Uses MarketCalendar to determine when regular session starts,
    then waits for open + open_delay_seconds.

    Args:
        console: Rich console for output.
        config: NakedTrader configuration.
    """
    cal = MarketCalendar()
    now = datetime.now(ET)
    session = cal.get_current_session(now)

    if session == MarketSession.REGULAR:
        console.print("[dim]Market is open[/dim]")
        return

    if session in (MarketSession.WEEKEND, MarketSession.HOLIDAY):
        console.print(f"[yellow]Market is {session.value} today[/yellow]")
        return

    # Pre-market: wait for 9:30 + delay
    open_time = now.replace(hour=9, minute=30, second=0, microsecond=0)
    target = open_time + timedelta(seconds=config.execution.open_delay_seconds)

    if now >= target:
        console.print("[dim]Past open + delay window[/dim]")
        return

    wait_secs = (target - now).total_seconds()
    console.print(
        f"[yellow]Waiting {int(wait_secs)}s for market open + "
        f"{config.execution.open_delay_seconds}s delay...[/yellow]"
    )

    while datetime.now(ET) < target:
        time.sleep(5)

    console.print("[green]Market open + delay complete[/green]")


def check_entry_time(config: NakedTraderConfig) -> bool:
    """Check if current time is before the latest entry cutoff.

    Args:
        config: NakedTrader configuration.

    Returns:
        True if within allowed entry window.
    """
    now = datetime.now(ET)
    parts = config.execution.latest_entry_time.split(":")
    cutoff = dt_time(int(parts[0]), int(parts[1]))
    if now.time() > cutoff:
        logger.warning(
            f"Past latest entry time ({config.execution.latest_entry_time} ET)"
        )
        return False
    return True


def display_trade_plan(
    console: Console,
    selection: StrikeSelection,
    config: NakedTraderConfig,
) -> None:
    """Display a Rich panel with the trade plan for user confirmation.

    Args:
        console: Rich console for output.
        selection: Strike selection result.
        config: NakedTrader configuration.
    """
    q = selection.quote
    contracts = config.instrument.contracts

    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Field", style="cyan")
    table.add_column("Value", style="yellow")

    table.add_row("Symbol", f"{selection.symbol} (via {selection.trading_class})")
    table.add_row("Action", "SELL PUT")
    table.add_row("Strike", f"${q.strike:.2f}")
    table.add_row("Expiration", f"{q.expiration[:4]}-{q.expiration[4:6]}-{q.expiration[6:]}")
    table.add_row("DTE", str(q.dte))
    table.add_row("Delta", f"{q.delta:.4f}")
    table.add_row("OTM %", f"{q.otm_pct:.2%}")
    table.add_row("Contracts", str(contracts))
    table.add_row("Premium (bid)", f"${q.bid:.2f}")
    table.add_row("Premium (ask)", f"${q.ask:.2f}")
    table.add_row("Underlying", f"${selection.underlying_price:.2f}")
    table.add_row("", "")
    table.add_row("Profit Take", f"BUY @ ${selection.profit_take_price:.2f} (GTC)")
    if selection.stop_loss_price:
        table.add_row("Stop Loss", f"BUY @ ${selection.stop_loss_price:.2f} (GTC)")
    else:
        table.add_row("Stop Loss", "[dim]disabled[/dim]")

    total_credit = q.bid * contracts * 100
    max_profit = (q.bid - selection.profit_take_price) * contracts * 100
    table.add_row("", "")
    table.add_row("Total Credit", f"${total_credit:.0f}")
    table.add_row("Max Profit (at PT)", f"${max_profit:.0f}")

    console.print(Panel(table, title="NakedTrader - Trade Plan", border_style="green"))


def run_daily_trade(
    client: IBKRClient,
    config: NakedTraderConfig,
    console: Console,
    dry_run: bool = True,
    skip_wait: bool = False,
    skip_confirm: bool = False,
) -> bool:
    """Execute the full daily NakedTrader workflow.

    Steps:
    1. Verify paper trading mode
    2. Wait for market open (if configured)
    3. Check entry time cutoff
    4. Get underlying price
    5. Retrieve option chain with Greeks
    6. Select best strike by delta target
    7. Display trade plan
    8. Confirm with user
    9. Place bracket orders (or show dry-run summary)
    10. Wait for parent fill
    11. Record trade in database

    Args:
        client: Connected IBKR client.
        config: NakedTrader configuration.
        console: Rich console for output.
        dry_run: If True, simulate without placing orders.
        skip_wait: Skip market open wait.
        skip_confirm: Skip user confirmation prompt.

    Returns:
        True if trade was placed/simulated successfully.
    """
    symbol = config.instrument.default_symbol

    # Step 1: Verify paper trading
    if not dry_run:
        _verify_paper_trading(client)

    # Step 2: Wait for market open
    if not skip_wait and config.execution.wait_for_open:
        wait_for_market_open(console, config)

    # Step 3: Check entry time (skip_wait bypasses this too)
    if not skip_wait and not check_entry_time(config):
        console.print("[red]Past latest entry time. Aborting. Use --no-wait to override.[/red]")
        return False

    # Step 4: Get underlying price
    console.print(f"\n[bold]Fetching {symbol} price...[/bold]")
    underlying_price = get_underlying_price(client, symbol)
    if not underlying_price:
        console.print(f"[red]Could not get {symbol} price. Is market data subscribed?[/red]")
        return False
    console.print(f"  {symbol}: ${underlying_price:.2f}")

    # Step 5: Get valid expirations
    console.print(f"[bold]Finding expirations (DTE {config.dte.min}-{config.dte.max})...[/bold]")
    expirations = get_valid_expirations(client, symbol, config)
    if not expirations:
        console.print("[red]No valid expirations in DTE range[/red]")
        return False

    for exp, dte in expirations:
        console.print(f"  {exp} (DTE {dte})")

    # Step 6: Retrieve chain + select strike (try each expiration)
    selection: StrikeSelection | None = None
    for exp, dte in expirations:
        console.print(f"\n[bold]Fetching chain for {exp} (DTE {dte})...[/bold]")
        chain = get_chain_with_greeks(client, symbol, exp, underlying_price, config)

        if chain.error:
            console.print(f"  [yellow]{chain.error}[/yellow]")
            continue

        console.print(f"  Got {len(chain.quotes)} strikes with Greeks")
        selection = select_strike(chain, config)
        if selection:
            break
        console.print(f"  [yellow]No suitable strike for {exp}[/yellow]")

    if not selection:
        console.print("\n[red]No suitable strike found across all expirations[/red]")
        return False

    # Step 7: Display trade plan
    console.print()
    display_trade_plan(console, selection, config)

    # Step 8: Confirm
    if not skip_confirm and not dry_run:
        import typer
        if not typer.confirm("\nPlace this trade?"):
            console.print("[yellow]Cancelled[/yellow]")
            return False

    # Step 9: Place orders
    if dry_run:
        console.print("\n[bold cyan]DRY RUN - No orders placed[/bold cyan]")
        return True

    console.print("\n[bold]Placing bracket orders...[/bold]")
    contract = build_option_contract(client, selection)
    if not contract:
        console.print("[red]Could not qualify option contract[/red]")
        return False

    bracket = place_bracket_order(client, contract, selection, config, dry_run=False)
    if not bracket.success:
        console.print(f"[red]Order failed: {bracket.error}[/red]")
        return False

    console.print(f"  Parent SELL: orderId={bracket.parent_order_id}")
    console.print(f"  Profit-Take BUY: orderId={bracket.profit_take_order_id}")
    if bracket.stop_loss_order_id:
        console.print(f"  Stop-Loss BUY: orderId={bracket.stop_loss_order_id}")

    # Step 10: Wait for fill
    console.print(f"\n[bold]Waiting for fill (up to {config.execution.fill_timeout_seconds}s)...[/bold]")
    fill_price, fill_time = wait_for_fill(
        client, bracket.parent_order_id, config.execution.fill_timeout_seconds
    )

    if fill_price:
        console.print(f"  [green]Filled @ ${fill_price:.2f}[/green]")
    else:
        console.print("  [yellow]Not yet filled (order remains active)[/yellow]")

    # Step 11: Record trade + capture entry snapshot
    from src.data.database import get_db_session

    with get_db_session() as session:
        trade = record_trade(
            session=session,
            selection=selection,
            bracket=bracket,
            fill_price=fill_price,
            fill_time=fill_time,
        )
        trade.contracts = config.instrument.contracts
        session.commit()
        console.print(f"\n[green]Trade recorded: {trade.trade_id}[/green]")

        # Step 12: Capture entry snapshot (98+ fields for learning engine)
        try:
            from src.services.entry_snapshot import EntrySnapshotService

            snapshot_service = EntrySnapshotService(ibkr_client=client)
            q = selection.quote
            snapshot = snapshot_service.capture_entry_snapshot(
                trade_id=trade.id,
                opportunity_id=None,
                symbol=selection.symbol,
                strike=q.strike,
                expiration=datetime.strptime(q.expiration, "%Y%m%d"),
                option_type="PUT",
                entry_premium=fill_price or q.bid,
                contracts=config.instrument.contracts,
                stock_price=selection.underlying_price,
                dte=q.dte,
                source="nakedtrader",
                live_delta_at_selection=q.delta,
            )
            snapshot_service.save_snapshot(snapshot, session)
            console.print(
                f"  Entry snapshot captured "
                f"(quality: {snapshot.data_quality_score:.0%})"
            )
        except Exception as e:
            logger.warning(f"Entry snapshot failed (trade still saved): {e}")
            console.print(f"  [yellow]Entry snapshot failed: {e}[/yellow]")

    return True


def _verify_paper_trading(client: IBKRClient) -> None:
    """Verify we're connected to a paper trading account.

    Raises:
        RuntimeError: If not connected to paper trading.
    """
    import os

    port = int(os.getenv("IBKR_PORT", "7497"))
    paper = os.getenv("PAPER_TRADING", "true").lower() == "true"

    if port != 7497 or not paper:
        raise RuntimeError(
            "NakedTrader requires paper trading mode. "
            f"Current port={port}, PAPER_TRADING={paper}"
        )
