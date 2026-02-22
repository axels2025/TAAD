"""CLI command implementations for NakedTrader.

Provides the execution logic for nt, nt-watch, and nt-status commands.
"""

import time
from datetime import datetime
from pathlib import Path

from loguru import logger
from rich.console import Console
from rich.table import Table

from src.data.database import get_db_session
from src.data.models import Trade
from src.data.repositories import StockPositionRepository, TradeRepository
from src.nakedtrader.config import NakedTraderConfig
from src.nakedtrader.watcher import get_open_nt_trades, run_watch_cycle
from src.nakedtrader.workflow import run_daily_trade
from src.services.market_calendar import MarketCalendar
from src.tools.ibkr_client import IBKRClient

DEFAULT_CONFIG_PATH = "config/daily_spx_options.yaml"


def run_nt(
    symbol: str,
    client: IBKRClient,
    console: Console,
    config_path: str = DEFAULT_CONFIG_PATH,
    contracts: int | None = None,
    dry_run: bool = True,
    no_wait: bool = False,
    stop_loss: bool | None = None,
    delta: float | None = None,
    dte: int | None = None,
    skip_confirm: bool = False,
) -> bool:
    """Execute the NakedTrader daily trade workflow.

    Args:
        symbol: Underlying symbol (SPX, XSP, SPY).
        client: Connected IBKR client.
        console: Rich console for output.
        config_path: Path to YAML config file.
        contracts: Override number of contracts.
        dry_run: Simulate without placing orders.
        no_wait: Skip market open wait.
        stop_loss: Override stop-loss setting.
        delta: Override target delta.
        dte: Override max DTE.
        skip_confirm: Skip user confirmation.

    Returns:
        True if trade was placed/simulated successfully.
    """
    # Load config with CLI overrides
    config = NakedTraderConfig.from_yaml(config_path)
    config = config.with_overrides(
        symbol=symbol,
        contracts=contracts,
        delta=delta,
        dte=dte,
        stop_loss=stop_loss,
    )

    console.print(f"[bold]NakedTrader - Daily {symbol} Put Selling[/bold]")
    console.print(f"[dim]Config: {config_path}[/dim]")
    console.print(
        f"[dim]Delta target: {config.strike.delta_target:.3f} "
        f"({config.strike.delta_min:.3f}-{config.strike.delta_max:.3f}), "
        f"DTE: {config.dte.min}-{config.dte.max}, "
        f"Contracts: {config.instrument.contracts}[/dim]"
    )

    if dry_run:
        console.print("[cyan]Mode: DRY RUN (no real orders)[/cyan]")
    else:
        console.print("[yellow]Mode: LIVE (real paper orders)[/yellow]")

    return run_daily_trade(
        client=client,
        config=config,
        console=console,
        dry_run=dry_run,
        skip_wait=no_wait,
        skip_confirm=skip_confirm,
    )


def run_nt_watch(
    client: IBKRClient,
    console: Console,
    config_path: str = DEFAULT_CONFIG_PATH,
    interval: int | None = None,
    once: bool = False,
    wait: bool = False,
) -> None:
    """Monitor open NakedTrader positions.

    Args:
        client: Connected IBKR client.
        console: Rich console for output.
        config_path: Path to YAML config file.
        interval: Override refresh interval seconds.
        once: Run once then exit.
        wait: Wait for market open instead of exiting when closed.
    """
    config = NakedTraderConfig.from_yaml(config_path)
    refresh_secs = interval or config.watch.interval_seconds

    console.print("[bold]NakedTrader - Position Watch[/bold]")

    # Check if market is open
    cal = MarketCalendar()
    if not cal.is_market_open():
        session_type = cal.get_current_session()
        remaining = cal.time_until_open()
        hours, rem = divmod(int(remaining.total_seconds()), 3600)
        mins = rem // 60
        next_open = cal.next_market_open()

        if not wait:
            console.print(
                f"[yellow]Market is {session_type.value}. "
                f"Opens in {hours}h {mins}m "
                f"({next_open.strftime('%a %b %d %H:%M')} ET).[/yellow]"
            )

            # Show open positions so the user knows what would be watched
            with get_db_session() as session:
                open_trades = get_open_nt_trades(session)

            if open_trades:
                console.print(
                    f"\n[cyan]{len(open_trades)} open position(s) "
                    f"will be monitored when market opens:[/cyan]"
                )
                table = Table(show_header=True, box=None, padding=(0, 2))
                table.add_column("Symbol", style="cyan")
                table.add_column("Strike", justify="right")
                table.add_column("Exp", style="dim")
                table.add_column("DTE", justify="right")
                table.add_column("Entry$", justify="right")
                table.add_column("Bracket", style="dim")

                now = datetime.now().date()
                for t in open_trades:
                    dte = (t.expiration - now).days if t.expiration else "?"
                    table.add_row(
                        t.symbol,
                        f"${t.strike:.0f}",
                        t.expiration.strftime("%m/%d") if t.expiration else "?",
                        str(dte),
                        f"${t.entry_premium:.2f}" if t.entry_premium else "?",
                        t.bracket_status or "?",
                    )
                console.print(table)
            else:
                console.print("\n[dim]No open positions.[/dim]")

            console.print("\n[dim]Use --wait to wait for market open.[/dim]")
            return

        console.print(
            f"[yellow]Market is {session_type.value}. "
            f"Opens in {hours}h {mins}m "
            f"({next_open.strftime('%a %b %d %H:%M')} ET). "
            f"Waiting...[/yellow]"
        )
        while not cal.is_market_open():
            time.sleep(30)
        console.print("[green]Market is open.[/green]\n")

    with get_db_session() as session:
        if once:
            run_watch_cycle(client, session, config, console)
            return

        console.print(f"[dim]Refreshing every {refresh_secs}s (Ctrl+C to stop)[/dim]\n")
        try:
            while True:
                console.clear()
                console.print(
                    f"[bold]NakedTrader Watch[/bold] "
                    f"[dim]{datetime.now().strftime('%H:%M:%S')}[/dim]\n"
                )
                run_watch_cycle(client, session, config, console)
                time.sleep(refresh_secs)
        except KeyboardInterrupt:
            console.print("\n[dim]Watch stopped[/dim]")


def run_nt_status(
    console: Console,
    history: int = 20,
) -> None:
    """Display NakedTrader trade history and performance summary.

    Offline - no IBKR connection required.

    Args:
        console: Rich console for output.
        history: Number of recent trades to show.
    """
    with get_db_session() as session:
        repo = TradeRepository(session)

        # Get all NT trades
        all_trades = (
            session.query(Trade)
            .filter(Trade.trade_strategy == "nakedtrader")
            .order_by(Trade.entry_date.desc())
            .all()
        )

        if not all_trades:
            console.print("[dim]No NakedTrader trades found[/dim]")
            return

        # Summary stats
        closed = [t for t in all_trades if t.exit_date is not None]
        open_trades = [t for t in all_trades if t.exit_date is None]

        wins = [t for t in closed if t.profit_loss and t.profit_loss > 0]
        losses = [t for t in closed if t.profit_loss and t.profit_loss <= 0]
        total_pnl = sum(t.profit_loss or 0 for t in closed)
        avg_premium = (
            sum(t.entry_premium or 0 for t in all_trades) / len(all_trades)
            if all_trades
            else 0
        )
        win_rate = len(wins) / len(closed) if closed else 0

        # Summary panel
        summary = Table(title="NakedTrader Summary", show_header=False, box=None)
        summary.add_column("Metric", style="cyan")
        summary.add_column("Value", style="yellow")

        summary.add_row("Total Trades", str(len(all_trades)))
        summary.add_row("Open", str(len(open_trades)))
        summary.add_row("Closed", str(len(closed)))
        summary.add_row("Win Rate", f"{win_rate:.0%}" if closed else "N/A")
        summary.add_row("Total P&L", f"${total_pnl:+,.0f}" if closed else "N/A")
        summary.add_row("Avg Premium", f"${avg_premium:.2f}")

        console.print(summary)
        console.print()

        # Recent trades table
        recent = all_trades[:history]
        table = Table(title=f"Recent Trades (last {history})")
        table.add_column("Date", style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Strike", justify="right")
        table.add_column("DTE", justify="right")
        table.add_column("Entry$", justify="right")
        table.add_column("Exit$", justify="right")
        table.add_column("P&L$", justify="right")
        table.add_column("Status")

        for trade in recent:
            entry_date = trade.entry_date.strftime("%m/%d %H:%M") if trade.entry_date else "?"

            if trade.exit_date:
                pnl_str = f"${trade.profit_loss:+.0f}" if trade.profit_loss is not None else "?"
                pnl_style = "green" if (trade.profit_loss or 0) > 0 else "red"
                exit_str = f"${trade.exit_premium:.2f}" if trade.exit_premium is not None else "?"
                status = trade.bracket_status or trade.exit_reason or "closed"
            else:
                pnl_str = "-"
                pnl_style = "dim"
                exit_str = "-"
                status = "[bold green]active[/bold green]"

            table.add_row(
                entry_date,
                trade.symbol,
                f"${trade.strike:.0f}",
                str(trade.dte) if trade.dte else "?",
                f"${trade.entry_premium:.2f}" if trade.entry_premium else "?",
                exit_str,
                f"[{pnl_style}]{pnl_str}[/{pnl_style}]",
                status,
            )

        console.print(table)


def run_nt_stocks(
    console: Console,
    history: int = 20,
) -> None:
    """Display stock positions from option assignments.

    Offline - no IBKR connection required. Shows open and closed stock
    positions with combined option + stock P&L.

    Args:
        console: Rich console for output.
        history: Number of closed positions to show.
    """
    with get_db_session() as session:
        repo = StockPositionRepository(session)

        open_positions = repo.get_open_positions()
        all_positions = repo.get_all(limit=history)

        if not all_positions:
            console.print("[dim]No stock positions from assignments[/dim]")
            return

        # Open positions table
        if open_positions:
            console.print(f"\n[bold]Open Stock Positions ({len(open_positions)})[/bold]")
            open_table = Table(show_header=True)
            open_table.add_column("Symbol", style="cyan")
            open_table.add_column("Shares", justify="right")
            open_table.add_column("Cost Basis", justify="right")
            open_table.add_column("Option P&L", justify="right")
            open_table.add_column("Origin Trade", style="dim")
            open_table.add_column("Assigned", style="dim")

            for sp in open_positions:
                opt_pnl_str = f"${sp.option_pnl:+,.0f}" if sp.option_pnl is not None else "?"
                opt_style = "green" if (sp.option_pnl or 0) > 0 else "red"

                open_table.add_row(
                    sp.symbol,
                    str(sp.shares),
                    f"${sp.cost_basis_per_share:.2f}",
                    f"[{opt_style}]{opt_pnl_str}[/{opt_style}]",
                    sp.origin_trade_id,
                    sp.assigned_date.strftime("%m/%d") if sp.assigned_date else "?",
                )

            console.print(open_table)
        else:
            console.print("\n[dim]No open stock positions[/dim]")

        # Closed positions table
        closed_positions = [sp for sp in all_positions if sp.closed_date is not None]
        if closed_positions:
            console.print(f"\n[bold]Closed Stock Positions ({len(closed_positions)})[/bold]")
            closed_table = Table(show_header=True)
            closed_table.add_column("Symbol", style="cyan")
            closed_table.add_column("Shares", justify="right")
            closed_table.add_column("Cost Basis", justify="right")
            closed_table.add_column("Sale Price", justify="right")
            closed_table.add_column("Stock P&L", justify="right")
            closed_table.add_column("Option P&L", justify="right")
            closed_table.add_column("Total P&L", justify="right")
            closed_table.add_column("Closed", style="dim")

            for sp in closed_positions:
                stock_pnl_str = f"${sp.stock_pnl:+,.0f}" if sp.stock_pnl is not None else "?"
                stock_style = "green" if (sp.stock_pnl or 0) > 0 else "red"
                opt_pnl_str = f"${sp.option_pnl:+,.0f}" if sp.option_pnl is not None else "?"
                opt_style = "green" if (sp.option_pnl or 0) > 0 else "red"
                total_pnl_str = f"${sp.total_pnl:+,.0f}" if sp.total_pnl is not None else "?"
                total_style = "green" if (sp.total_pnl or 0) > 0 else "red"

                closed_table.add_row(
                    sp.symbol,
                    str(sp.shares),
                    f"${sp.cost_basis_per_share:.2f}",
                    f"${sp.sale_price_per_share:.2f}" if sp.sale_price_per_share else "?",
                    f"[{stock_style}]{stock_pnl_str}[/{stock_style}]",
                    f"[{opt_style}]{opt_pnl_str}[/{opt_style}]",
                    f"[{total_style}]{total_pnl_str}[/{total_style}]",
                    sp.closed_date.strftime("%m/%d") if sp.closed_date else "?",
                )

            console.print(closed_table)
