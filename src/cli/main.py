"""Main CLI entry point for the trading system.

This module provides the command-line interface for interacting with
the trading system during development and operation.
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.utils.timezone import utc_now

# Load .env file into environment variables
from dotenv import load_dotenv

load_dotenv()

# Disable Rich help formatting to avoid compatibility issues
os.environ["_TYPER_STANDARD_TRACEBACK"] = "1"

import typer
import click
from click import Context
from pydantic import ValidationError
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.config.base import get_config
from src.config.baseline_strategy import BaselineStrategy
from src.config.logging import setup_logging
from src.data.database import get_db_session, init_database
from src.data.models import ScanOpportunity, ScanResult
from src.data.repositories import TradeRepository
from src.execution.exit_manager import ExitManager
from src.execution.order_executor import OrderExecutor
from src.execution.position_monitor import PositionMonitor
from src.execution.risk_governor import RiskGovernor
from src.services.entry_snapshot import EntrySnapshotService
from src.strategies.base import TradeOpportunity
from src.strategies.naked_put import NakedPutStrategy
from src.tools.ibkr_client import IBKRClient, IBKRConnectionError
from src.tools.efficient_scanner import EfficientOptionScanner
from src.tools.options_finder import OptionsFinder
from src.tools.screener import StockScreener
from src.tools.ibkr_validator import IBKRValidator
from src.tools.manual_trade_entry import ManualTradeManager, ManualTradeEntry
from src.tools.manual_trade_importer import ManualTradeImporter
from src.data.repositories import ScanRepository
from src.utils.calc import calc_pnl, calc_pnl_pct, fmt_pct
from src.cli.commands.execution_commands import (
    run_show_staged,
)
from src.cli.commands.validation_commands import (
    run_premarket_validation,
    run_open_validation,
    run_full_validation,
)
from src.services.premarket_validator import PremarketValidator, ValidationConfig
from src.services.portfolio_builder import PortfolioBuilder, PortfolioConfig
from src.services.limit_price_calculator import LimitPriceCalculator
from src.data.opportunity_state import OpportunityState
from src.execution.opportunity_lifecycle import OpportunityLifecycleManager


# Monkey-patch rich formatter to bypass the bug for main help only
def _patched_rich_format_help(
    obj,
    ctx,
    markup_mode=None,
):
    """Bypass rich formatting and use standard Click formatting."""
    # Use Click's built-in format_help method for TyperGroup only
    formatter = ctx.make_formatter()
    # Manually format help to avoid the make_metavar() bug
    formatter.write_usage(ctx.command_path, "[ OPTIONS] COMMAND [ARGS]...")
    formatter.write_paragraph()
    formatter.write_text(obj.help or "")
    return formatter.getvalue()


try:
    from typer import rich_utils, core

    # Save original
    _original_rich_format_help = rich_utils.rich_format_help

    # Only patch TyperGroup help, not individual commands
    original_typer_group_format_help = core.TyperGroup.format_help

    def patched_typer_group_format_help(self, ctx, formatter):
        """Use patched formatter for main help."""
        formatter.write_usage(ctx.command_path, "[OPTIONS] COMMAND [ARGS]...")
        formatter.write_paragraph()
        formatter.write_text(self.help or "")

        # Write options
        formatter.write_paragraph()
        formatter.write_heading("Options")
        formatter.write_dl([("--help", "Show this message and exit.")])

        # Write commands
        if self.list_commands(ctx):
            formatter.write_paragraph()
            formatter.write_heading("Commands")
            commands = []
            for name in self.list_commands(ctx):
                cmd = self.get_command(ctx, name)
                if cmd:
                    help_text = (
                        cmd.get_short_help_str(100)
                        if hasattr(cmd, "get_short_help_str")
                        else (cmd.help or "")
                    )
                    commands.append((name, help_text))
            formatter.write_dl(commands)

    core.TyperGroup.format_help = patched_typer_group_format_help
except (ImportError, AttributeError):
    pass


def connect_to_ibkr_with_error_handling(
    config, console: Console, show_spinner: bool = True, client_id_override: int | None = None
) -> IBKRClient:
    """Connect to IBKR with user-friendly error messages.

    Args:
        config: Application config object
        console: Rich console for output
        show_spinner: Whether to show connecting spinner
        client_id_override: Override client ID (allows multiple simultaneous connections)

    Returns:
        Connected IBKRClient instance

    Raises:
        typer.Exit: If connection fails
    """
    try:
        # Override client ID if specified (allows multiple simultaneous connections)
        if client_id_override is not None:
            config = config.model_copy(update={"ibkr_client_id": client_id_override})

        if show_spinner:
            with console.status("[bold yellow]Connecting to IBKR..."):
                client = IBKRClient(config.ibkr)
                client.connect()
        else:
            console.print("[dim]Connecting to IBKR...[/dim]")
            client = IBKRClient(config.ibkr)
            client.connect()
        return client
    except (IBKRConnectionError, ConnectionRefusedError, OSError) as e:
        console.print()
        console.print("[bold red]✗ Cannot connect to IB Gateway/TWS[/bold red]\n")
        console.print("[yellow]Please check:[/yellow]")
        console.print(f"  • IB Gateway or TWS is running")
        console.print(f"  • API connections are enabled in settings")
        console.print(f"  • Port {config.ibkr.port} is correct (7497=paper, 7496=live)")
        console.print(f"  • Host {config.ibkr.host} is accessible")
        console.print()
        console.print(f"[dim]Error: {str(e)}[/dim]")
        console.print()
        console.print("[cyan]To test connection:[/cyan]")
        console.print("  nakedtrader test")
        raise typer.Exit(1)


# Disable rich help to avoid compatibility issues
app = typer.Typer(
    name="nakedtrader",
    help="Naked Puts Trading System",
    add_completion=False,
    pretty_exceptions_enable=False,
)

console = Console()

# Phase 5: Daemon subgroup
from src.cli.commands.daemon_commands import daemon_app

app.add_typer(daemon_app, name="daemon")


# ============================================================================
# Helper Functions
# ============================================================================


def display_scan_parameters(
    min_premium: float,
    max_premium: float | None,
    min_otm: float,
    max_otm: float | None,
    min_dte: int,
    max_dte: int | None,
    require_uptrend: bool,
    max_results: int,
    source: str = "CLI arguments",
) -> None:
    """Display scan parameters in a formatted table.

    Args:
        min_premium: Minimum premium
        max_premium: Maximum premium (None = unlimited)
        min_otm: Minimum OTM percentage
        max_otm: Maximum OTM percentage (None = unlimited)
        min_dte: Minimum DTE
        max_dte: Maximum DTE (None = unlimited)
        require_uptrend: Whether uptrend is required
        max_results: Maximum results
        source: Source of parameters (e.g., "CLI arguments" or ".env file")
    """
    # Format max values with "unlimited" if None
    max_premium_str = f"${max_premium:.2f}" if max_premium is not None else "unlimited"
    max_otm_str = f"{max_otm:.0%}" if max_otm is not None else "unlimited"
    max_dte_str = str(max_dte) if max_dte is not None else "unlimited"

    table = Table(title=f"Scan Parameters (from {source})", show_header=False, box=None)
    table.add_column("Parameter", style="cyan", width=20)
    table.add_column("Value", style="yellow")

    table.add_row("Premium Range:", f"${min_premium:.2f} - {max_premium_str}")
    table.add_row("OTM Range:", f"{min_otm:.0%} - {max_otm_str}")
    table.add_row("DTE Range:", f"{min_dte} - {max_dte_str} days")
    table.add_row(
        "Trend Filter:", "Uptrend required" if require_uptrend else "Any trend"
    )
    table.add_row("Max Results:", str(max_results))

    console.print(table)
    console.print()


# ============================================================================
# Infrastructure Commands
# ============================================================================


@app.command(name="init")
def init() -> None:
    """Initialize the trading system (database, config, etc.)."""
    try:
        console.print("[bold blue]Initializing trading system...[/bold blue]")

        # Load configuration
        config = get_config()
        console.print("✓ Configuration loaded from .env")

        # Setup logging
        setup_logging(log_level=config.log_level, log_file=config.log_file)
        console.print(f"✓ Logging initialized (level={config.log_level})")

        # Initialize database
        init_database()
        console.print(f"✓ Database initialized at {config.database_url}")

        # Create required directories
        config.ensure_directories()
        console.print("✓ Required directories created")

        console.print(
            "[bold green]✓ Trading system initialized successfully![/bold green]"
        )

    except Exception as e:
        console.print(f"[bold red]✗ Initialization failed: {e}[/bold red]")
        raise typer.Exit(1)


@app.command(name="test")
def test_ibkr() -> None:
    """Test connection to Interactive Brokers."""
    try:
        console.print("[bold blue]Testing IBKR connection...[/bold blue]")

        config = get_config()

        # Attempt to connect
        client = IBKRClient(config.ibkr)

        with console.status("[bold yellow]Connecting to IBKR..."):
            client.connect()

        console.print(f"✓ Connected to IBKR at {config.ibkr.host}:{config.ibkr.port}")

        # Get account summary
        with console.status("[bold yellow]Fetching account summary..."):
            summary = client.get_account_summary()

        if summary:
            table = Table(title="Account Summary")
            table.add_column("Field", style="cyan")
            table.add_column("Value", style="green")

            for key, value in list(summary.items())[:10]:  # Show first 10 items
                table.add_row(key, str(value))

            console.print(table)
        else:
            console.print("[yellow]Could not fetch account summary[/yellow]")

        # Disconnect
        client.disconnect()
        console.print("[bold green]✓ IBKR connection test successful![/bold green]")

    except IBKRConnectionError as e:
        console.print(f"[bold red]✗ IBKR connection failed: {e}[/bold red]")
        console.print("\n[yellow]Troubleshooting tips:[/yellow]")
        console.print("1. Ensure TWS or IB Gateway is running")
        console.print("2. Check that paper trading mode is enabled")
        console.print("3. Verify API is enabled in settings")
        console.print("4. Confirm port 7497 is correct (7497=paper, 7496=live)")
        console.print("5. Check that 127.0.0.1 is whitelisted")
        raise typer.Exit(1)
    except Exception as e:
        console.print(f"[bold red]✗ Test failed: {e}[/bold red]")
        raise typer.Exit(1)


@app.command(name="status")
def status() -> None:
    """Show system status and statistics."""
    try:
        console.print("[bold blue]System Status[/bold blue]\n")

        # Configuration status
        config = get_config()
        console.print("[cyan]Configuration:[/cyan]")
        console.print(f"  Database: {config.database_url}")
        console.print(f"  Paper Trading: {config.paper_trading}")
        console.print(f"  Learning Enabled: {config.learning_enabled}")
        console.print(f"  Log Level: {config.log_level}\n")

        # Database statistics
        with get_db_session() as session:
            trade_repo = TradeRepository(session)
            all_trades = trade_repo.get_all()
            open_trades = trade_repo.get_open_trades()
            closed_trades = trade_repo.get_closed_trades()

            console.print("[cyan]Trade Statistics:[/cyan]")
            console.print(f"  Total Trades: {len(all_trades)}")
            console.print(f"  Open Trades: {len(open_trades)}")
            console.print(f"  Closed Trades: {len(closed_trades)}")

            if closed_trades:
                profitable = sum(
                    1 for t in closed_trades if t.profit_loss and t.profit_loss > 0
                )
                win_rate = (profitable / len(closed_trades)) * 100
                console.print(f"  Win Rate: {win_rate:.1f}%")

        console.print("\n[bold green]✓ System operational[/bold green]")

    except Exception as e:
        console.print(f"[bold red]✗ Status check failed: {e}[/bold red]")
        raise typer.Exit(1)


@app.command(name="db-reset")
def db_reset() -> None:
    """Reset the database (WARNING: Deletes all data!)."""
    from src.data.database import reset_database

    confirm = typer.confirm(
        "This will DELETE ALL DATA in the database. Are you sure?",
        abort=True,
    )

    if confirm:
        try:
            reset_database()
            console.print("[bold green]✓ Database reset successfully[/bold green]")
        except Exception as e:
            console.print(f"[bold red]✗ Database reset failed: {e}[/bold red]")
            raise typer.Exit(1)


@app.command(name="dashboard")
def dashboard(
    host: str = typer.Option("127.0.0.1", help="Host to bind to"),
    port: int = typer.Option(8080, help="Port to listen on"),
    config: Optional[str] = typer.Option(None, help="Path to phase5.yaml config"),
) -> None:
    """Launch the TAAD monitoring dashboard.

    Real-time web dashboard showing daemon status, open positions,
    staged trades, decisions, costs, and logs.

    Examples:
        # Start dashboard (default: http://127.0.0.1:8080)
        nakedtrader dashboard

        # Use custom port
        nakedtrader dashboard --port 9090

        # Bind to all interfaces
        nakedtrader dashboard --host 0.0.0.0
    """
    import os

    try:
        import uvicorn
    except ImportError:
        console.print("[red]uvicorn not installed. Run: pip install uvicorn[/red]")
        raise typer.Exit(1)

    if port < 1024 and os.geteuid() != 0:
        console.print(
            f"[bold red]✗ Port {port} requires root privileges.[/bold red]\n"
            f"[dim]Use a port >= 1024 (e.g. --port 8080) or run with sudo.[/dim]"
        )
        raise typer.Exit(1)

    from src.agentic.config import load_phase5_config
    from src.agentic.dashboard_api import create_dashboard_app

    init_database()

    cfg = load_phase5_config(config)
    auth_token = cfg.dashboard.auth_token
    # Use config values as defaults (CLI flags override)
    host = host if host != "127.0.0.1" else cfg.dashboard.host
    port = port if port != 8080 else cfg.dashboard.port

    dash_app = create_dashboard_app(auth_token=auth_token)

    console.print("[bold blue]🌐 Starting TAAD Dashboard[/bold blue]")
    console.print(f"[dim]Server: http://{host}:{port}[/dim]\n")
    if auth_token:
        console.print(f"[dim]Auth: token required (?token=...)[/dim]")
    else:
        console.print("[yellow]No auth token configured — dashboard is unauthenticated[/yellow]")
    console.print("Press CTRL+C to stop the server\n")

    try:
        uvicorn.run(dash_app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        console.print("\n[yellow]✓ Server stopped[/yellow]")
    except Exception as e:
        console.print(f"[bold red]✗ Server error: {e}[/bold red]")
        raise typer.Exit(1)


@app.command(name="version")
def version() -> None:
    """Show version information."""
    console.print("[bold]Trading Agent v0.2.0 (Phase 2 Complete)[/bold]")
    console.print("Self-Learning AI Trading System")
    console.print("Status: Autonomous Execution Engine Operational")


# ============================================================================
# Trading Commands
# ============================================================================


@app.command(name="execute-one")
def execute(
    symbol: str = typer.Argument(..., help="Stock symbol (e.g., AAPL)"),
    strike: float = typer.Argument(..., help="Strike price"),
    expiration: str = typer.Argument(..., help="Expiration date (YYYY-MM-DD)"),
    premium: float = typer.Option(0.50, help="Expected premium ($)"),
    contracts: int = typer.Option(1, help="Number of contracts"),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--live",
        help="Dry run mode (no real orders) or live execution",
    ),
) -> None:
    """Execute a single trade (place order via IBKR).

    Place a naked put order for the specified symbol, strike, and expiration.
    The order will be validated against risk limits before placement.

    Example:
        nakedtrader execute-one AAPL 180 2025-02-07 --premium 0.50 --contracts 1 --live
    """
    try:
        console.print("[bold blue]Executing Trade...[/bold blue]\n")

        config = get_config()

        # Initialize logging
        setup_logging(log_level=config.log_level, log_file=config.log_file)

        if dry_run:
            console.print(
                "[yellow]DRY RUN MODE - No real orders will be placed[/yellow]\n"
            )

        # Parse expiration date
        try:
            exp_date = datetime.strptime(expiration, "%Y-%m-%d")
        except ValueError:
            console.print(
                "[bold red]✗ Invalid expiration date format. Use YYYY-MM-DD[/bold red]"
            )
            raise typer.Exit(1)

        # Calculate DTE
        dte = (exp_date - datetime.now()).days

        # Connect to IBKR
        client = connect_to_ibkr_with_error_handling(config, console)
        console.print("✓ Connected to IBKR\n")

        # Get current stock price for OTM calculation
        stock_price = client.get_stock_price(symbol) or 0

        if stock_price <= 0:
            console.print(f"[bold red]✗ Could not get price for {symbol}[/bold red]")
            client.disconnect()
            raise typer.Exit(1)

        otm_pct = (stock_price - strike) / stock_price

        # Create trade opportunity
        opportunity = TradeOpportunity(
            symbol=symbol,
            strike=strike,
            expiration=exp_date,
            option_type="PUT",
            premium=premium,
            contracts=contracts,
            otm_pct=otm_pct,
            dte=dte,
            stock_price=stock_price,
            trend="manual",  # Manually specified
            confidence=0.50,  # Manual trades have lower confidence
            reasoning=f"Manual trade: {symbol} ${strike} PUT",
            margin_required=strike * 100 * contracts * 0.2,  # Estimate 20% margin
        )

        # Display trade details
        table = Table(title="Trade Details")
        table.add_column("Field", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Symbol", symbol)
        table.add_row("Strike", f"${strike:.2f}")
        table.add_row("Expiration", expiration)
        table.add_row("Premium", f"${premium:.2f}")
        table.add_row("Contracts", str(contracts))
        table.add_row("Stock Price", f"${stock_price:.2f}")
        table.add_row("OTM%", f"{otm_pct:.1%}")
        table.add_row("DTE", str(dte))
        table.add_row("Max Profit", f"${premium * contracts * 100:.2f}")
        table.add_row("Max Risk", f"${strike * contracts * 100:.2f}")

        console.print(table)
        console.print()

        # Initialize components
        strategy_config = BaselineStrategy.from_env()
        position_monitor = PositionMonitor(client, strategy_config)
        risk_governor = RiskGovernor(client, position_monitor, config)
        order_executor = OrderExecutor(client, config, dry_run=dry_run, risk_governor=risk_governor)
        entry_snapshot_service = EntrySnapshotService(client, timeout=10)

        # Step 1: Risk check
        with console.status("[bold yellow]Checking risk limits..."):
            risk_check = risk_governor.pre_trade_check(opportunity)

        if not risk_check.approved:
            console.print(f"[bold red]✗ Trade rejected by risk governor[/bold red]")
            console.print(f"[red]Reason: {risk_check.reason}[/red]")
            client.disconnect()
            raise typer.Exit(1)

        console.print("✓ Risk checks passed\n")

        # Step 2: Execute trade
        console.print("[bold yellow]Placing order...[/bold yellow]")
        result = order_executor.execute_trade(opportunity, order_type="LIMIT")

        if result.success:
            console.print("[bold green]✓ Trade executed successfully![/bold green]")
            if result.order_id:
                console.print(f"Order ID: {result.order_id}")
            console.print(f"Status: {result.status.value}")

            if dry_run:
                console.print(
                    "\n[yellow]This was a DRY RUN - no real order was placed[/yellow]"
                )
        else:
            console.print(
                f"[bold red]✗ Trade failed: {result.error_message}[/bold red]"
            )

        # Record trade in risk governor and database
        if result.success and not dry_run:
            risk_governor.record_trade(opportunity)

            # Save trade to database to get database trade_id
            if not dry_run:
                try:
                    from src.data.models import Trade
                    import uuid

                    with get_db_session() as session:
                        # Parse expiration date (handle both string and datetime objects)
                        if isinstance(opportunity.expiration, str):
                            exp_date = datetime.strptime(opportunity.expiration, "%Y-%m-%d").date()
                        elif isinstance(opportunity.expiration, datetime):
                            exp_date = opportunity.expiration.date()
                        else:
                            exp_date = opportunity.expiration

                        # Create trade record
                        acct = client.get_account_id() if client else None
                        source = "paper" if (not acct or acct.startswith("DU")) else "real"

                        trade = Trade(
                            trade_id=f"TRD-{uuid.uuid4().hex[:12]}",
                            symbol=opportunity.symbol,
                            strike=opportunity.strike,
                            expiration=exp_date,
                            option_type=opportunity.option_type,
                            entry_date=utc_now(),
                            entry_premium=opportunity.premium,
                            contracts=opportunity.contracts,
                            otm_pct=opportunity.otm_pct if hasattr(opportunity, "otm_pct") else None,
                            dte=opportunity.dte,
                            account_id=acct,
                            trade_source=source,
                        )
                        session.add(trade)
                        session.flush()  # Get the database-generated ID

                        db_trade_id = trade.id
                        console.print(f"\n[dim]✓ Trade saved to database (ID: {db_trade_id})[/dim]")

                        # Phase 2.6A: Capture entry snapshot with correct trade_id
                        try:
                            snapshot = entry_snapshot_service.capture_entry_snapshot(
                                trade_id=db_trade_id,  # Use database trade_id, not order_id
                                opportunity_id=None,
                                symbol=opportunity.symbol,
                                strike=opportunity.strike,
                                expiration=opportunity.expiration,
                                option_type=opportunity.option_type,
                                entry_premium=opportunity.premium,
                                contracts=opportunity.contracts,
                                stock_price=opportunity.stock_price,
                                dte=opportunity.dte,
                                source="manual",
                            )

                            entry_snapshot_service.save_snapshot(snapshot, session)

                            # Check if market is open
                            market_status = client.is_market_open()
                            market_is_open = market_status.get("is_open", False)

                            missing_critical = snapshot.get_missing_critical_fields()

                            if snapshot.data_quality_score >= 0.7:
                                console.print(
                                    f"[dim]✓ Entry snapshot captured (quality: {snapshot.data_quality_score:.1%})[/dim]"
                                )
                            elif not market_is_open:
                                console.print(
                                    f"[dim]✓ Entry snapshot captured (quality: {snapshot.data_quality_score:.1%})[/dim]"
                                )
                                console.print(
                                    f"[dim]  ℹ Market closed - Greeks/IV unavailable (will be populated at market open)[/dim]"
                                )
                            else:
                                console.print(
                                    f"[dim]✓ Entry snapshot captured (quality: {snapshot.data_quality_score:.1%})[/dim]"
                                )

                            if missing_critical and market_is_open:
                                console.print(
                                    f"[dim]⚠ Missing fields: {', '.join(missing_critical)}[/dim]"
                                )

                        except Exception as e:
                            # Entry snapshot failure is non-critical
                            logger.warning(f"Failed to capture entry snapshot: {e}")
                            console.print(f"[dim]⚠ Entry snapshot skipped (non-critical)[/dim]")

                        session.commit()

                except Exception as e:
                    console.print(f"\n[yellow]⚠ Failed to save trade to database: {e}[/yellow]")

        # Disconnect
        client.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Execution failed: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="trade")
def trade(
    auto: bool = False,
    max_trades: int = typer.Option(5, help="Maximum trades to place"),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--live",
        help="Dry run mode (no real orders) or live execution",
    ),
    manual_only: bool = typer.Option(False, help="Use only manual trades from database"),
    # VALIDATION OPTIONS
    validate: bool = typer.Option(True, "--validate/--no-validate", help="Validate opportunities with IBKR before executing (default: validate)"),
) -> None:
    """Run autonomous trading cycle with manual trade entries.

    This command executes the full autonomous trading workflow:
    1. Load opportunities from manual trades in the database
    2. Validate opportunities with IBKR real-time data
    3. Execute best opportunities within risk limits
    4. Capture entry snapshots for learning

    Examples:
        # Use manual trades only
        nakedtrader trade --manual-only

        # Dry run (test without placing orders)
        nakedtrader trade --manual-only --dry-run

        # Skip IBKR validation (not recommended)
        nakedtrader trade --manual-only --no-validate
    """
    try:
        console.print("[bold blue]Autonomous Trading Cycle[/bold blue]\n")

        if not manual_only:
            console.print("[bold red]✗ Error: No opportunity source specified[/bold red]\n")
            console.print("[yellow]You must specify:[/yellow]")
            console.print("  • --manual-only    (use manual trades from database)")
            console.print("\n[cyan]Example:[/cyan]")
            console.print("  nakedtrader trade --manual-only")
            raise typer.Exit(1)

        if dry_run:
            console.print(
                "[yellow]DRY RUN MODE - No real orders will be placed[/yellow]\n"
            )

        config = get_config()
        strategy_config = BaselineStrategy.from_env()

        # Setup logging (console: WARNING only for cleaner output, file: INFO for debugging)
        setup_logging(
            log_level="INFO", console_level="WARNING", log_file=config.log_file
        )

        # Connect to IBKR
        client = connect_to_ibkr_with_error_handling(config, console)
        console.print("✓ Connected to IBKR\n")

        # Display mode information
        if manual_only:
            console.print("[bold]Mode:[/bold] Manual Trades Only")
            if validate:
                console.print("[dim]Will validate manual trades with IBKR[/dim]\n")
            else:
                console.print("[dim]Will execute manual trades without validation[/dim]\n")

        # Display validation settings (applies to all modes)
        console.print("[bold]Risk Management:[/bold]")
        console.print(
            f"  • Profit target: {strategy_config.exit_rules.profit_target * 100:.0f}%"
        )
        console.print(
            f"  • Stop loss: {abs(strategy_config.exit_rules.stop_loss) * 100:.0f}%"
        )
        console.print(f"  • Position size: {strategy_config.position_size} contracts")
        console.print(f"  • Max concurrent: {strategy_config.max_positions} positions")
        console.print()

        # Initialize components
        strategy = NakedPutStrategy(strategy_config)
        position_monitor = PositionMonitor(client, strategy_config)
        risk_governor = RiskGovernor(client, position_monitor, config)
        order_executor = OrderExecutor(client, config, dry_run=dry_run, risk_governor=risk_governor)
        exit_manager = ExitManager(client, position_monitor, strategy_config)
        entry_snapshot_service = EntrySnapshotService(client, timeout=10)

        # STEP 2: Create validator for IBKR enrichment
        ibkr_validator = IBKRValidator(client) if validate else None

        # STEP 3: Gather opportunities from specified source
        console.print("[bold cyan]Step 1: Gathering opportunities...[/bold cyan]\n")

        all_opportunities = []

        # Load manual trades from database
        if manual_only:
            try:
                from src.data.manual_trade_importer import ManualTradeImporter

                console.print("[cyan]• Loading manual trades from database...[/cyan]")

                with get_db_session() as session:
                    importer = ManualTradeImporter(session)
                    manual_opps_db = importer.get_pending_manual_opportunities(limit=None)

                    if not manual_opps_db:
                        console.print("[yellow]  ⚠ No pending manual trades found[/yellow]")
                    else:
                        console.print(f"  [dim]✓ Found {len(manual_opps_db)} pending manual trades[/dim]")

                        enriched_count = 0
                        fallback_count = 0

                        if validate and ibkr_validator:
                            console.print("[cyan]• Enriching with live IBKR data...[/cyan]")

                        for opp in manual_opps_db:
                            # Create base opportunity dict
                            base_opp = {
                                "id": opp.id,
                                "symbol": opp.symbol,
                                "strike": opp.strike,
                                "expiration": opp.expiration,
                                "option_type": opp.option_type,
                            }

                            # Try to enrich with live IBKR data if validation enabled
                            enriched = None
                            if validate and ibkr_validator:
                                try:
                                    enriched = ibkr_validator.enrich_manual_opportunity(base_opp)
                                except Exception as e:
                                    console.print(
                                        f"  [red]✗ Error enriching {opp.symbol} ${opp.strike}: {e}[/red]"
                                    )

                            if enriched:
                                # Use enriched data (live market data)
                                enriched["id"] = opp.id  # Preserve database ID
                                enriched["confidence"] = 0.85  # Higher confidence for enriched data
                                enriched["reasoning"] = "Manual entry (validated with live IBKR data)"
                                enriched["source"] = "manual"
                                all_opportunities.append(enriched)
                                enriched_count += 1
                                console.print(
                                    f"  [green]✓ {opp.symbol} ${opp.strike}: premium=${enriched['premium']:.2f}, OTM={fmt_pct(enriched['otm_pct'])}[/green]"
                                )
                            else:
                                # Fall back to stored database values
                                if validate:
                                    console.print(
                                        f"  [yellow]⚠ {opp.symbol} ${opp.strike} - using stored data[/yellow]"
                                    )

                                # Calculate OTM if missing
                                otm_pct = opp.otm_pct or 0.0
                                if otm_pct == 0 and opp.stock_price and opp.strike:
                                    if opp.option_type == "PUT":
                                        otm_pct = (opp.stock_price - opp.strike) / opp.stock_price
                                    else:  # CALL
                                        otm_pct = (opp.strike - opp.stock_price) / opp.stock_price
                                    otm_pct = max(0, otm_pct)

                                all_opportunities.append({
                                    "id": opp.id,
                                    "symbol": opp.symbol,
                                    "strike": opp.strike,
                                    "expiration": opp.expiration.strftime("%Y-%m-%d"),
                                    "option_type": opp.option_type,
                                    "premium": opp.premium or 0.0,
                                    "otm_pct": otm_pct,
                                    "dte": opp.dte,
                                    "stock_price": opp.stock_price or 0.0,
                                    "trend": opp.trend or "unknown",
                                    "margin_required": opp.margin_required or 0.0,
                                    "confidence": 0.70,
                                    "reasoning": opp.entry_notes or "Manual entry (using stored data)",
                                    "source": "manual",
                                })
                                fallback_count += 1

                        if enriched_count > 0:
                            console.print(f"  [green]✓ {enriched_count} enriched with live data[/green]")
                        if fallback_count > 0:
                            console.print(f"  [yellow]⚠ {fallback_count} using stored data[/yellow]")

            except Exception as e:
                console.print(f"[red]✗ Failed to load manual trades: {e}[/red]")
                import traceback
                console.print(f"[dim]{traceback.format_exc()}[/dim]")
                client.disconnect()
                raise typer.Exit(1)

        # Check if we have any opportunities
        console.print(f"\n✓ Total opportunities collected: {len(all_opportunities)}\n")

        if not all_opportunities:
            console.print("[yellow]No opportunities found[/yellow]")
            console.print("[dim]Tip: Add manual trades via 'execute' command[/dim]")
            client.disconnect()
            return

        # Convert all_opportunities to the format expected by rest of code
        opportunities = all_opportunities

        # Create mapping from opportunity to database ID (for marking as executed later)
        opportunity_db_ids = {}
        for opp in opportunities:
            if opp.get("id"):  # Manual trades have database IDs
                key = (opp["symbol"], opp["strike"], opp["expiration"])
                opportunity_db_ids[key] = opp["id"]

        # Step 2: Convert to TradeOpportunity objects
        console.print("[bold cyan]Step 2: Evaluating opportunities...[/bold cyan]\n")

        qualified_opportunities = []
        rejected_opportunities = []

        for opp in opportunities:
            # Parse expiration date (handle multiple formats)
            expiration_str = opp["expiration"]
            if isinstance(expiration_str, str):
                if len(expiration_str) == 8:  # YYYYMMDD format
                    expiration_dt = datetime.strptime(expiration_str, "%Y%m%d")
                elif "-" in expiration_str:  # YYYY-MM-DD format
                    expiration_dt = datetime.strptime(expiration_str, "%Y-%m-%d")
                else:
                    console.print(
                        f"[yellow]⚠ Skipping {opp['symbol']}: invalid date format[/yellow]"
                    )
                    continue
            else:
                expiration_dt = expiration_str  # Already a datetime

            # Convert dict to TradeOpportunity
            trade_opp = TradeOpportunity(
                symbol=opp["symbol"],
                strike=opp["strike"],
                expiration=expiration_dt,
                option_type=opp["option_type"],
                premium=opp["premium"],
                contracts=1,  # Default to 1 contract
                otm_pct=opp["otm_pct"],
                dte=opp["dte"],
                stock_price=opp["stock_price"],
                trend=opp.get("trend", "uptrend"),
                confidence=opp.get("confidence", 0.75),
                reasoning=opp.get("reasoning", "Trade opportunity"),
                margin_required=opp["margin_required"],
            )

            # Validate with strategy config
            # Note: IBKR validation already happened in gathering step if validate=True
            # This validates against strategy rules (OTM range, DTE, premium, etc.)
            (is_valid, rejection_reason) = strategy_config.validate_opportunity_with_reason(opp)

            if is_valid:
                qualified_opportunities.append(trade_opp)
            else:
                rejected_opportunities.append((opp, rejection_reason))

        # Display results with rejection reasons
        if qualified_opportunities:
            console.print(
                f"[green]✓ {len(qualified_opportunities)} opportunities qualified[/green]"
            )

        if rejected_opportunities:
            console.print(
                f"[yellow]✗ {len(rejected_opportunities)} opportunities rejected:[/yellow]\n"
            )

            for opp, reason in rejected_opportunities:
                console.print(
                    f"  • {opp['symbol']} ${opp['strike']:.2f} - [red]{reason}[/red]"
                )

            console.print()

        if not qualified_opportunities and not rejected_opportunities:
            console.print("[dim]No opportunities to evaluate[/dim]\n")

        if not qualified_opportunities:
            console.print("[yellow]No qualified opportunities[/yellow]")
            client.disconnect()
            return

        # Sort by confidence score
        qualified_opportunities.sort(key=lambda x: x.confidence, reverse=True)

        # Limit to max_trades
        top_opportunities = qualified_opportunities[:max_trades]

        # Display top opportunities with enriched data
        table = Table(title=f"Top {len(top_opportunities)} Opportunities")
        table.add_column("#")
        table.add_column("Symbol", style="cyan bold")
        table.add_column("Strike", justify="right")
        table.add_column("Premium", justify="right")
        table.add_column("OTM%", justify="right")
        table.add_column("DTE", justify="right")
        table.add_column("Margin", justify="right")
        table.add_column("Confidence", justify="right")

        for i, opp in enumerate(top_opportunities, 1):
            table.add_row(
                str(i),
                opp.symbol,
                f"${opp.strike:.2f}",
                f"${opp.premium:.2f}",
                f"{opp.otm_pct:.1%}",
                str(opp.dte),
                f"${opp.margin_required:.0f}" if opp.margin_required > 0 else "N/A",
                f"{opp.confidence:.1%}",
            )

        console.print(table)
        console.print()

        # Step 3: Execute trades
        console.print("[bold cyan]Step 3: Executing trades...[/bold cyan]\n")

        trades_executed = 0
        trades_rejected = 0

        for i, opp in enumerate(top_opportunities, 1):
            console.print(
                f"[cyan]Trade {i}/{len(top_opportunities)}: {opp.symbol} ${opp.strike}[/cyan]"
            )

            # Risk check
            risk_check = risk_governor.pre_trade_check(opp)

            if not risk_check.approved:
                console.print(f"  [yellow]✗ Rejected: {risk_check.reason}[/yellow]")
                trades_rejected += 1
                continue

            # Confirm in interactive mode
            if not auto:
                confirm = typer.confirm(f"  Execute this trade?")
                if not confirm:
                    console.print("  [yellow]Skipped by user[/yellow]")
                    continue

            # Execute
            result = order_executor.execute_trade(opp, order_type="LIMIT")

            if result.success:
                console.print(
                    f"  [green]✓ Executed (Order ID: {result.order_id})[/green]"
                )
                trades_executed += 1

                if dry_run:
                    console.print(
                        f"  [dim]DRY RUN: Skipping DB writes (snapshot, opportunity marking, risk tracking)[/dim]"
                    )
                else:
                    risk_governor.record_trade(opp)

                    # Phase 2.6A: Capture entry snapshot for learning engine
                    try:
                        expiration_str = opp.expiration.strftime("%Y-%m-%d")
                        opp_key = (opp.symbol, opp.strike, expiration_str)

                        # Get opportunity_id if this came from a scan
                        opportunity_id = opportunity_db_ids.get(opp_key, None)

                        # Construct trade_id (will update with actual DB trade ID later)
                        trade_id_temp = result.order_id if result.order_id else 0

                        # Capture entry snapshot
                        snapshot = entry_snapshot_service.capture_entry_snapshot(
                            trade_id=trade_id_temp,  # Temporary, will be updated
                            opportunity_id=opportunity_id,
                            symbol=opp.symbol,
                            strike=opp.strike,
                            expiration=opp.expiration,
                            option_type=opp.option_type,
                            entry_premium=opp.premium,
                            contracts=opp.contracts,
                            stock_price=opp.stock_price,
                            dte=opp.dte,
                            source="scan" if opportunity_id else "manual",
                        )

                        # Save snapshot to database
                        with get_db_session() as session:
                            entry_snapshot_service.save_snapshot(snapshot, session)
                            console.print(
                                f"  [dim]✓ Entry snapshot captured (quality: {snapshot.data_quality_score:.1%})[/dim]"
                            )

                            # Log missing critical fields if any
                            missing_critical = snapshot.get_missing_critical_fields()
                            if missing_critical:
                                console.print(
                                    f"  [dim]⚠ Missing critical fields: {', '.join(missing_critical)}[/dim]"
                                )

                    except Exception as e:
                        console.print(
                            f"  [yellow]⚠ Failed to capture entry snapshot: {e}[/yellow]"
                        )

                    # Mark manual trade as executed in database
                    expiration_str = opp.expiration.strftime("%Y-%m-%d")
                    opp_key = (opp.symbol, opp.strike, expiration_str)

                    if opp_key in opportunity_db_ids:
                        try:
                            with get_db_session() as session:
                                scan_repo = ScanRepository(session)
                                db_id = opportunity_db_ids[opp_key]

                                # Construct trade_id from order ID
                                trade_id = (
                                    f"T{result.order_id}" if result.order_id else None
                                )

                                # Mark as executed
                                scan_repo.mark_opportunity_executed(db_id, trade_id)
                                console.print(
                                    f"  [dim]✓ Updated database (opportunity #{db_id} marked as executed with trade {trade_id})[/dim]"
                                )

                        except Exception as e:
                            console.print(
                                f"  [yellow]⚠ Failed to update database: {e}[/yellow]"
                            )

            else:
                console.print(f"  [red]✗ Failed: {result.error_message}[/red]")

            console.print()

        # Summary
        console.print("[bold cyan]Execution Summary:[/bold cyan]")
        console.print(f"  Executed: {trades_executed}")
        console.print(f"  Rejected: {trades_rejected}")
        console.print()

        # Step 4: Monitor positions
        console.print("[bold cyan]Step 4: Monitoring positions...[/bold cyan]\n")

        positions = position_monitor.get_all_positions()
        console.print(f"Current open positions: {len(positions)}")

        # Check for exits
        exit_decisions = exit_manager.evaluate_exits()

        exits_needed = sum(
            1 for decision in exit_decisions.values() if decision.should_exit
        )
        if exits_needed > 0:
            console.print(f"[yellow]⚠ {exits_needed} positions ready for exit[/yellow]")
        else:
            console.print("[green]✓ No exits needed at this time[/green]")

        console.print()
        console.print("[bold green]✓ Trading cycle complete[/bold green]")

        # Disconnect
        client.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Trading cycle failed: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="monitor")
def monitor() -> None:
    """Monitor current positions (P&L, Greeks, exit signals).

    Shows all open positions with real-time P&L, Greeks, and exit alerts.
    """
    try:
        console.print("[bold blue]Position Monitor[/bold blue]\n")

        config = get_config()
        strategy_config = BaselineStrategy.from_env()

        # Connect to IBKR
        client = connect_to_ibkr_with_error_handling(config, console)
        console.print("✓ Connected to IBKR\n")

        # Initialize components
        position_monitor = PositionMonitor(client, strategy_config)
        exit_manager = ExitManager(client, position_monitor, strategy_config)

        # Get all positions
        with console.status("[bold yellow]Fetching positions..."):
            positions = position_monitor.get_all_positions()

        if not positions:
            console.print("[yellow]No open positions[/yellow]")
            client.disconnect()
            return

        # Display positions table
        table = Table(title=f"Open Positions ({len(positions)})")
        table.add_column("Symbol", style="cyan bold")
        table.add_column("Strike", justify="right")
        table.add_column("Exp", justify="right")
        table.add_column("DTE", justify="right")
        table.add_column("Entry", justify="right")
        table.add_column("Current", justify="right")
        table.add_column("P&L", justify="right")
        table.add_column("P&L%", justify="right")
        table.add_column("Exit Signal", style="yellow")

        # Get exit decisions
        exit_decisions = exit_manager.evaluate_exits()

        total_pnl = 0.0

        for pos in positions:
            # Calculate P&L
            pnl = calc_pnl(pos.entry_premium, pos.current_premium, pos.contracts)
            pnl_pct = calc_pnl_pct(pnl, pos.entry_premium, pos.contracts)
            total_pnl += pnl

            # Get exit decision
            exit_decision = exit_decisions.get(pos.position_id)
            exit_signal = ""
            if exit_decision and exit_decision.should_exit:
                exit_signal = f"⚠ {exit_decision.reason}"

            # Color code P&L
            pnl_style = "green" if pnl > 0 else "red" if pnl < 0 else "white"
            pnl_pct_style = (
                "green" if pnl_pct > 0 else "red" if pnl_pct < 0 else "white"
            )

            # Calculate expiration date from DTE
            from datetime import timedelta

            expiration_date = datetime.now() + timedelta(days=pos.dte)

            table.add_row(
                pos.symbol,
                f"${pos.strike:.2f}",
                expiration_date.strftime("%m/%d"),
                str(pos.dte),
                f"${pos.entry_premium:.2f}",
                f"${pos.current_premium:.2f}",
                f"[{pnl_style}]${pnl:.2f}[/{pnl_style}]",
                f"[{pnl_pct_style}]{pnl_pct:+.1%}[/{pnl_pct_style}]",
                exit_signal,
            )

        console.print(table)

        # Summary
        total_style = "green" if total_pnl > 0 else "red" if total_pnl < 0 else "white"
        console.print(
            f"\n[bold]Total Unrealized P&L: [{total_style}]${total_pnl:.2f}[/{total_style}][/bold]"
        )

        # Exit alerts
        exits_needed = sum(
            1 for decision in exit_decisions.values() if decision.should_exit
        )
        if exits_needed > 0:
            console.print(
                f"\n[bold yellow]⚠ {exits_needed} positions ready for exit[/bold yellow]"
            )
            console.print(
                "\n[cyan]To close these positions, run:[/cyan]"
            )
            console.print(
                "  [white]nakedtrader halt --liquidate[/white]"
            )

        # Disconnect
        client.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Monitor failed: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="watch")
def auto_monitor(
    check_interval: int = typer.Option(60, help="Seconds between checks (default: 60)"),
    auto_exit: bool = typer.Option(
        False, help="Automatically execute exits when triggered"
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--live",
        help="Dry run mode (no real exits) or live execution",
    ),
    no_market_open_check: bool = typer.Option(
        False,
        "--no-market-open-check",
        help="Skip market-hours check (for testing outside market hours)",
    ),
) -> None:
    """Run autonomous monitoring loop - continuously monitor and exit positions.

    This runs continuously, checking positions at regular intervals and automatically
    exiting when profit target, stop loss, or time exit triggers.

    Examples:
        # Monitor only (show alerts but don't exit)
        nakedtrader watch

        # Monitor and auto-exit (DRY RUN)
        nakedtrader watch --auto-exit

        # Monitor and auto-exit (LIVE)
        nakedtrader watch --auto-exit --live

        # Check every 30 seconds
        nakedtrader watch --check-interval 30 --auto-exit
    """
    import time
    import signal
    import sys

    def signal_handler(sig, frame):
        """Handle Ctrl+C gracefully."""
        console.print("\n[yellow]⚠ Stopping autonomous monitor...[/yellow]")
        sys.exit(0)

    signal.signal(signal.SIGINT, signal_handler)

    try:
        console.print("[bold blue]🤖 Autonomous Position Monitor[/bold blue]\n")

        if auto_exit:
            if dry_run:
                console.print(
                    "[yellow]Mode: DRY-RUN (will show what would be exited)[/yellow]"
                )
            else:
                console.print(
                    "[bold red]Mode: LIVE - Will execute real exits![/bold red]"
                )
        else:
            console.print("[dim]Mode: Monitor only (alerts, no auto-exit)[/dim]")

        console.print(f"Check interval: {check_interval} seconds")
        console.print("Press Ctrl+C to stop\n")

        # Initialize logging and database (CRITICAL: Required for position queries)
        setup_logging()
        init_database()

        config = get_config()
        strategy_config = BaselineStrategy.from_env()
        console.print(
            f"Exit rules: profit_target={strategy_config.exit_rules.profit_target:.0%}, "
            f"stop_loss={strategy_config.exit_rules.stop_loss:.0%}, "
            f"time_exit_dte={strategy_config.exit_rules.time_exit_dte}"
        )

        # Check market hours BEFORE connecting to IBKR (avoid 3-min timeout
        # when TWS/Gateway isn't running yet outside market hours)
        from src.services.market_calendar import MarketCalendar
        cal = MarketCalendar()

        if not cal.is_market_open() and not no_market_open_check:
            session_type = cal.get_current_session()
            remaining = cal.time_until_open()
            hours, rem = divmod(int(remaining.total_seconds()), 3600)
            mins = rem // 60
            next_open = cal.next_market_open()
            console.print(
                f"[yellow]Market is {session_type.value}. "
                f"Next open: {next_open.strftime('%a %b %d %H:%M')} ET[/yellow]"
            )
            console.print(
                f"[dim]Waiting {hours}h {mins}m "
                f"until market opens... (Ctrl+C to cancel)[/dim]\n"
            )
            while not cal.is_market_open():
                time.sleep(30)
            console.print("[green]✓ Market is open — starting monitor[/green]\n")
        elif no_market_open_check and not cal.is_market_open():
            console.print(
                "[yellow]⚠ Market is closed but --no-market-open-check is set — proceeding anyway[/yellow]\n"
            )

        # Timezone for EOD reconciliation and market-hours guards in the loop
        et = cal.TZ

        # Connect to IBKR (use client_id=2 to allow execute to run simultaneously)
        client = connect_to_ibkr_with_error_handling(
            config, console, show_spinner=False, client_id_override=2
        )
        console.print("✓ Connected to IBKR (client_id=2)\n")

        # Track whether EOD reconciliation has run today
        last_eod_reconciliation_date = None

        # Initialize components
        position_monitor = PositionMonitor(client, strategy_config)
        exit_manager = ExitManager(client, position_monitor, strategy_config, dry_run=dry_run)

        cycle_count = 0

        while True:
            cycle_count += 1
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            console.print(
                f"[bold cyan]━━━ Cycle #{cycle_count} - {timestamp} ━━━[/bold cyan]"
            )

            # ── End-of-Day Reconciliation ──
            # Run once per trading day after 4:05 PM ET (5 min buffer for
            # final fills to settle). Syncs order statuses/fills/commissions,
            # reconciles positions, imports orphans, and captures daily
            # position snapshots for learning engine.
            now_et = datetime.now(et)
            today_date = now_et.date()
            eod_time = now_et.replace(hour=16, minute=5, second=0, microsecond=0)
            if (
                now_et >= eod_time
                and last_eod_reconciliation_date != today_date
                and cal.is_trading_day(now_et)
            ):
                dry_label = "[DRY RUN] " if dry_run else ""
                console.print(
                    f"\n[bold magenta]━━━ {dry_label}End-of-Day Reconciliation ━━━[/bold magenta]"
                )
                try:
                    import asyncio
                    from src.services.order_reconciliation import OrderReconciliation
                    from src.services.position_snapshot import PositionSnapshotService
                    from src.data.database import get_db_session

                    with get_db_session() as eod_session:
                        from src.data.repositories import TradeRepository
                        eod_trade_repo = TradeRepository(eod_session)
                        reconciler = OrderReconciliation(client, eod_trade_repo)

                        # 1. Order reconciliation (sync fill prices, statuses, commissions)
                        eod_report = asyncio.run(reconciler.sync_all_orders())
                        console.print(
                            f"  Orders: {eod_report.total_reconciled} reconciled, "
                            f"{eod_report.total_discrepancies} discrepancies, "
                            f"{len(eod_report.orphans)} orphans"
                        )

                        # 2. Position reconciliation (detect DB/IBKR mismatches)
                        pos_report = asyncio.run(reconciler.reconcile_positions())
                        mismatches = (
                            len(pos_report.in_ibkr_not_db)
                            + len(pos_report.in_db_not_ibkr)
                            + len(pos_report.quantity_mismatches)
                        )
                        if mismatches:
                            console.print(
                                f"  Positions: {len(pos_report.in_ibkr_not_db)} in IBKR not DB, "
                                f"{len(pos_report.in_db_not_ibkr)} in DB not IBKR, "
                                f"{len(pos_report.quantity_mismatches)} qty mismatches"
                            )
                        else:
                            console.print("  Positions: DB matches IBKR ✓")

                        # 3. Auto-import orphan positions from IBKR
                        if pos_report.in_ibkr_not_db:
                            imported = asyncio.run(
                                reconciler.import_orphan_positions(dry_run=dry_run)
                            )
                            if dry_run:
                                console.print(
                                    f"  [dim]DRY RUN: Would import {imported} "
                                    f"orphan positions from IBKR[/dim]"
                                )
                            else:
                                console.print(f"  Imported {imported} orphan positions from IBKR")

                        # 4. Position snapshots
                        snap_service = PositionSnapshotService(client, eod_session)
                        snapshots = snap_service.capture_all_open_positions()
                        console.print(
                            f"  Snapshots: {len(snapshots)} position snapshots captured"
                        )

                        if not dry_run:
                            eod_session.commit()
                        else:
                            eod_session.rollback()

                    last_eod_reconciliation_date = today_date
                    console.print(
                        f"[green]  {dry_label}EOD reconciliation complete[/green]\n"
                    )
                except Exception as eod_err:
                    logger.error(f"EOD reconciliation failed: {eod_err}", exc_info=True)
                    console.print(
                        f"[red]  EOD reconciliation failed: {eod_err}[/red]\n"
                    )

            # Check pending exit orders from previous cycles
            pending_statuses = exit_manager.check_pending_exits()
            if pending_statuses:
                for pid, pstatus in pending_statuses.items():
                    if "filled" in pstatus:
                        console.print(f"  [green]✓ Exit filled: {pid} — {pstatus}[/green]")
                    elif pstatus in ("cancelled", "inactive", "apicancelled", "order_not_found"):
                        console.print(
                            f"  [yellow]⚠ Exit order {pstatus}: {pid} — will re-evaluate[/yellow]"
                        )
                    else:
                        console.print(f"  [dim]Pending exit: {pid} — {pstatus}[/dim]")

            # Auto-close expired positions before evaluating
            expired = position_monitor.close_expired_positions(dry_run=dry_run)
            if expired:
                prefix = "[DRY RUN] Would auto-close" if dry_run else "Auto-closed"
                for ep in expired:
                    console.print(
                        f"[yellow]✓ {prefix} expired: {ep['symbol']} ${ep['strike']} "
                        f"exp {ep['expiration']} (P&L: ${ep['profit_loss']:.2f})[/yellow]"
                    )

            # Fetch positions (now uses database + IBKR)
            positions = position_monitor.get_all_positions()

            # Check for sync discrepancies between database and IBKR
            from src.data.database import get_db_session
            from src.data.models import Trade

            with get_db_session() as session:
                db_open_count = session.query(Trade).filter(
                    Trade.exit_date.is_(None)
                ).count()

            # Get IBKR position count
            try:
                ibkr_positions = client.get_positions()
                # Filter for options only
                ibkr_option_count = sum(
                    1 for p in ibkr_positions
                    if hasattr(p.contract, 'right') and hasattr(p.contract, 'strike')
                )
            except Exception as e:
                logger.warning(f"Could not get IBKR positions for reconciliation: {e}")
                ibkr_option_count = -1

            # Warn if discrepancy detected
            if ibkr_option_count >= 0 and db_open_count != ibkr_option_count:
                console.print(
                    f"[yellow]⚠ SYNC WARNING: Database has {db_open_count} open trades "
                    f"but IBKR shows {ibkr_option_count} option positions[/yellow]"
                )
                if db_open_count > ibkr_option_count:
                    console.print(
                        "[yellow]  → Monitor will still evaluate all database positions[/yellow]"
                    )
                    console.print(
                        "[yellow]  → Run 'nakedtrader reconcile' to investigate[/yellow]"
                    )

            if not positions:
                console.print("[dim]No open positions in database[/dim]")
            else:
                console.print(
                    f"[cyan]Monitoring {len(positions)} open positions...[/cyan]"
                )

                # Guard: skip exit evaluation outside market hours
                if not cal.is_market_open() and not no_market_open_check:
                    console.print(
                        "[yellow]Market is closed — skipping exit evaluation "
                        "(orders placed now may fill at bad prices)[/yellow]"
                    )
                    console.print(
                        f"[dim]Sleeping {check_interval}s until next check...[/dim]\n"
                    )
                    client.ib.sleep(check_interval)
                    continue

                # Evaluate exits
                exit_decisions = exit_manager.evaluate_exits()

                # Display position status
                stale_count = 0
                for pos in positions:
                    # Calculate P&L
                    pnl = calc_pnl(pos.entry_premium, pos.current_premium, pos.contracts)
                    pnl_pct = calc_pnl_pct(pnl, pos.entry_premium, pos.contracts)

                    # Get exit decision
                    decision = exit_decisions.get(pos.position_id)

                    # Status line
                    pnl_style = "green" if pnl > 0 else "red" if pnl < 0 else "white"

                    # Check for stale market data (stop loss inactive)
                    if pos.market_data_stale:
                        stale_count += 1
                        console.print(
                            f"  [bold red]⚠ {pos.symbol} ${pos.strike:.0f} — "
                            f"NO MARKET DATA — STOP LOSS INACTIVE[/bold red]"
                        )
                        continue

                    status = f"  {pos.symbol} ${pos.strike:.0f} - P&L: [{pnl_style}]${pnl:.2f} ({pnl_pct:+.1%})[/{pnl_style}]"

                    if decision and decision.should_exit:
                        console.print(
                            f"{status} - [yellow bold]⚠ EXIT: {decision.reason}[/yellow bold]"
                        )

                        # Auto-exit if enabled
                        if auto_exit:
                            if dry_run:
                                console.print(
                                    f"    [dim]DRY-RUN: Would execute {decision.exit_type} exit at ${decision.limit_price}[/dim]"
                                )
                            else:
                                console.print(f"    [bold]→ Executing exit...[/bold]")
                                result = exit_manager.execute_exit(
                                    pos.position_id, decision
                                )

                                if result.success:
                                    console.print(
                                        f"    [green]✓ Exit order placed (Order ID: {result.order_id})[/green]"
                                    )
                                else:
                                    # Check if it's a foreign exchange issue
                                    foreign_exchanges = ["TSE", "LSE", "HKEX"]
                                    is_foreign = any(
                                        ex in pos.position_id
                                        for ex in foreign_exchanges
                                    )

                                    if (
                                        "Failed to qualify contract"
                                        in result.error_message
                                        and is_foreign
                                    ):
                                        console.print(
                                            f"    [red]✗ Cannot access foreign exchange data ({pos.symbol})[/red]"
                                        )
                                        console.print(
                                            f"    [dim]→ Close this position manually via TWS/IB Gateway[/dim]"
                                        )
                                    else:
                                        console.print(
                                            f"    [red]✗ Exit failed: {result.error_message}[/red]"
                                        )
                    else:
                        console.print(f"{status} - [dim]Holding[/dim]")

                # Summary alert for stale data during market hours
                if stale_count > 0 and cal.is_market_open():
                    console.print(
                        f"\n[bold red]⚠ WARNING: {stale_count} position(s) have no live "
                        f"market data — stop losses are NOT active for these positions![/bold red]"
                    )

            # Sleep until next cycle (ib.sleep processes IB events during wait)
            console.print(
                f"[dim]Sleeping {check_interval}s until next check...[/dim]\n"
            )
            client.ib.sleep(check_interval)

    except KeyboardInterrupt:
        console.print("\n[yellow]⚠ Stopped by user[/yellow]")
        if "client" in locals():
            client.disconnect()
        raise typer.Exit(0)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Auto-monitor failed: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        if "client" in locals():
            client.disconnect()
        raise typer.Exit(1)


@app.command(name="analyse")
def analyse(
    days: int = typer.Option(30, help="Days to analyse"),
    show_trades: bool = False,
    ai: bool = typer.Option(False, "--ai", help="Run AI-powered analysis using Claude"),
    ask: Optional[str] = typer.Option(None, "--ask", help="Ask a specific question about your performance"),
    depth: str = typer.Option("standard", "--depth", help="Analysis depth (AI only): quick, standard, deep"),
    account: Optional[str] = typer.Option(None, "--account", "-a", help="Filter by IBKR account ID"),
) -> None:
    """Analyse trading performance and statistics.

    Shows win rate, ROI, Sharpe ratio, and performance breakdowns by various
    dimensions (sector, OTM range, DTE, etc.).

    Use --ai for AI-powered insights or --ask to ask a specific question.

    Examples:
        nakedtrader analyse --days 30
        nakedtrader analyse --ai
        nakedtrader analyse --ai --depth deep --days 180
        nakedtrader analyse --ask "Why are my Energy trades underperforming?"
        nakedtrader analyse --account KALA --ai
    """
    try:
        # Warn if --depth is used without --ai
        if depth != "standard" and not ai and not ask:
            console.print("[yellow]Note: --depth only applies to AI analysis. Use --ai to enable.[/yellow]\n")

        # If --ai or --ask is used, run AI analysis
        if ai or ask:
            from src.cli.commands.analysis_commands import run_ai_analysis

            console.print("[bold blue]AI Performance Analysis[/bold blue]\n")

            if depth not in ("quick", "standard", "deep"):
                console.print(f"[red]Invalid depth '{depth}'. Use: quick, standard, deep[/red]")
                raise typer.Exit(1)

            # Default to 90 days for AI analysis if user didn't explicitly set days
            ai_days = days if days != 30 or not ai else 90

            with get_db_session() as session:
                run_ai_analysis(
                    session=session,
                    days=ai_days,
                    depth=depth,
                    question=ask,
                    account_id=account,
                )
            return

        # Standard (non-AI) analysis
        console.print("[bold blue]Performance Analysis[/bold blue]\n")

        with get_db_session() as session:
            trade_repo = TradeRepository(session)

            # Get closed trades
            from datetime import timedelta

            all_closed = trade_repo.get_closed_trades(account_id=account)
            cutoff_date = datetime.now() - timedelta(days=days)
            closed_trades = [
                t for t in all_closed if t.exit_date and t.exit_date >= cutoff_date
            ]

            if not closed_trades:
                console.print(
                    f"[yellow]No closed trades in the last {days} days[/yellow]"
                )
                return

            # Calculate statistics
            total_trades = len(closed_trades)
            profitable = sum(
                1 for t in closed_trades if t.profit_loss and t.profit_loss > 0
            )
            win_rate = (profitable / total_trades) * 100

            total_profit = sum(t.profit_loss for t in closed_trades if t.profit_loss)
            avg_profit = total_profit / total_trades if total_trades > 0 else 0

            avg_roi = (
                sum(t.roi for t in closed_trades if t.roi) / total_trades
                if total_trades > 0
                else 0
            )

            # Summary table
            summary = Table(title=f"Performance Summary (Last {days} Days)")
            summary.add_column("Metric", style="cyan")
            summary.add_column("Value", style="green", justify="right")

            summary.add_row("Total Trades", str(total_trades))
            summary.add_row("Winning Trades", str(profitable))
            summary.add_row("Win Rate", f"{win_rate:.1f}%")
            summary.add_row("Total P&L", f"${total_profit:.2f}")
            summary.add_row("Avg P&L/Trade", f"${avg_profit:.2f}")
            summary.add_row("Avg ROI", f"{avg_roi:.2%}")

            console.print(summary)
            console.print()

            # Recent trades if requested
            if show_trades:
                recent = closed_trades[:10]  # Last 10

                trades_table = Table(title="Recent Closed Trades")
                trades_table.add_column("Date")
                trades_table.add_column("Symbol")
                trades_table.add_column("Strike", justify="right")
                trades_table.add_column("Entry", justify="right")
                trades_table.add_column("Exit", justify="right")
                trades_table.add_column("P&L", justify="right")
                trades_table.add_column("ROI", justify="right")
                trades_table.add_column("Days", justify="right")

                for trade in recent:
                    pnl_style = (
                        "green"
                        if trade.profit_loss and trade.profit_loss > 0
                        else "red"
                    )
                    roi_style = "green" if trade.roi and trade.roi > 0 else "red"

                    trades_table.add_row(
                        trade.entry_date.strftime("%m/%d"),
                        trade.symbol,
                        f"${trade.strike:.2f}",
                        f"${trade.entry_premium:.2f}",
                        f"${trade.exit_premium:.2f}" if trade.exit_premium else "N/A",
                        f"[{pnl_style}]${trade.profit_loss:.2f}[/{pnl_style}]"
                        if trade.profit_loss
                        else "N/A",
                        f"[{roi_style}]{trade.roi:+.1%}[/{roi_style}]"
                        if trade.roi
                        else "N/A",
                        str(trade.days_held) if trade.days_held else "N/A",
                    )

                console.print(trades_table)
                console.print()

            console.print("[bold green]✓ Analysis complete[/bold green]")

    except Exception as e:
        console.print(f"[bold red]✗ Analysis failed: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="cleanup")
def cleanup(
    force: bool = typer.Option(
        False,
        "--force",
        "-f",
        help="Force kill orphaned Python processes",
    ),
):
    """Clean up orphaned processes and connections.

    Use this if you interrupted execution and see background tasks still running.

    Example:
        nakedtrader cleanup
        nakedtrader cleanup --force  # Kill orphaned processes
    """
    console.print("\n[bold cyan]System Cleanup[/bold cyan]\n")

    try:
        # Check for orphaned Python processes
        import subprocess
        import os

        console.print("[dim]Checking for orphaned processes...[/dim]")
        try:
            result = subprocess.run(
                ["ps", "aux"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            lines = result.stdout.split("\n")
            trading_processes = [
                line for line in lines
                if "python" in line.lower()
                and ("nakedtrader" in line or "nkd" in line or "src.cli.main" in line)
                and str(os.getpid()) not in line  # Exclude current process
            ]

            if trading_processes:
                console.print(f"[yellow]Found {len(trading_processes)} orphaned process(es):[/yellow]")
                for proc in trading_processes:
                    # Extract PID (second column)
                    parts = proc.split()
                    if len(parts) > 1:
                        pid = parts[1]
                        console.print(f"  PID {pid}: {proc[:80]}...")

                if force:
                    console.print("\n[yellow]Killing orphaned processes...[/yellow]")
                    for proc in trading_processes:
                        parts = proc.split()
                        if len(parts) > 1:
                            pid = parts[1]
                            try:
                                subprocess.run(["kill", "-9", pid], timeout=5)
                                console.print(f"[green]✓ Killed process {pid}[/green]")
                            except Exception as e:
                                console.print(f"[red]✗ Failed to kill {pid}: {e}[/red]")
                else:
                    console.print("\n[yellow]Run with --force to kill these processes[/yellow]")
                    console.print("[dim]Example: nakedtrader cleanup --force[/dim]")
            else:
                console.print("[green]✓ No orphaned processes found[/green]")
        except Exception as e:
            console.print(f"[yellow]Process check failed: {e}[/yellow]")

        # Cancel any pending asyncio tasks
        import asyncio
        try:
            loop = asyncio.get_event_loop()
            pending = asyncio.all_tasks(loop)
            if pending:
                console.print(f"\n[yellow]Found {len(pending)} pending tasks, cancelling...[/yellow]")
                for task in pending:
                    task.cancel()
                console.print("[green]✓ Tasks cancelled[/green]")
            else:
                console.print("\n[green]✓ No pending async tasks[/green]")
        except RuntimeError:
            console.print("\n[green]✓ No event loop running[/green]")

        # Try to disconnect any IBKR connections
        console.print("\n[dim]Checking IBKR connections...[/dim]")
        try:
            config = get_config()
            from src.tools.ibkr_client import IBKRClient
            client = IBKRClient(config.ibkr, suppress_errors=True)
            try:
                if client.ib.isConnected():
                    client.disconnect()
                    console.print("[green]✓ Disconnected from IBKR[/green]")
                else:
                    console.print("[green]✓ No active IBKR connection[/green]")
            except Exception as e:
                # Try to connect first, then disconnect
                try:
                    client.connect()
                    client.disconnect()
                    console.print("[green]✓ Cleaned up IBKR connection[/green]")
                except:
                    console.print(f"[dim]IBKR: {e}[/dim]")
        except Exception as e:
            console.print(f"[dim]IBKR check: {e}[/dim]")

        console.print("\n[green]✓ Cleanup complete[/green]\n")

    except Exception as e:
        console.print(f"[red]✗ Cleanup error: {e}[/red]")
        import traceback
        console.print(f"[dim]{traceback.format_exc()}[/dim]")
        raise typer.Exit(1)


@app.command(name="halt")
def emergency_stop(
    liquidate: bool = False,
) -> None:
    """Emergency stop - halt all trading immediately.

    This command triggers the emergency stop mechanism, halting all new trades.
    Use --liquidate to also close all open positions (use with caution).
    """
    try:
        console.print("[bold red]EMERGENCY STOP INITIATED[/bold red]\n")

        config = get_config()

        # Connect to IBKR
        client = connect_to_ibkr_with_error_handling(config, console)

        strategy_config = BaselineStrategy.from_env()
        position_monitor = PositionMonitor(client, strategy_config)
        risk_governor = RiskGovernor(client, position_monitor, config)

        # Trigger emergency halt
        risk_governor.emergency_halt("User-initiated emergency stop via CLI")

        console.print(
            "[bold red]✓ Trading halted - all new trades blocked[/bold red]\n"
        )

        if liquidate:
            console.print("[bold yellow]Liquidating all positions...[/bold yellow]\n")

            positions = position_monitor.get_all_positions()

            if not positions:
                console.print("[yellow]No positions to liquidate[/yellow]")
            else:
                console.print(f"Found {len(positions)} positions to close\n")

                # Use ExitManager to properly close all positions
                exit_manager = ExitManager(client, position_monitor, strategy_config)
                results = exit_manager.emergency_exit_all()

                # Display results
                success_count = 0
                for result in results:
                    position = next(
                        (p for p in positions if p.position_id == result.position_id),
                        None,
                    )
                    if position:
                        if result.success:
                            console.print(
                                f"Closing {position.symbol} ${position.strike} {position.option_type}..."
                            )
                            console.print(
                                f"  [green]✓ Close order placed (Order ID: {result.order_id})[/green]"
                            )
                            success_count += 1
                        else:
                            console.print(
                                f"Closing {position.symbol} ${position.strike} {position.option_type}..."
                            )
                            console.print(
                                f"  [red]✗ Failed to close: {result.error_message}[/red]"
                            )

                console.print(
                    f"\n[bold yellow]Liquidation complete: {success_count}/{len(results)} positions closed.[/bold yellow]"
                )
                console.print(
                    "[yellow]Run 'nakedtrader monitor' to verify positions are closed.[/yellow]"
                )

        console.print("\n[bold red]EMERGENCY STOP COMPLETE[/bold red]")
        console.print(
            "To resume trading, restart the application or call risk_governor.resume_trading()"
        )

        # Disconnect
        client.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Emergency stop failed: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


# ============================================================================
# Manual Trade Entry Commands
# ============================================================================


@app.command(name="add")
def add_trade(
    symbol: Optional[str] = typer.Option(None, help="Stock symbol (e.g., AAPL)"),
    strike: Optional[float] = typer.Option(None, help="Strike price"),
    expiration: Optional[str] = typer.Option(None, help="Expiration date (YYYY-MM-DD)"),
    premium: Optional[float] = typer.Option(None, help="Expected premium"),
    notes: Optional[str] = typer.Option(None, help="Your reasoning/notes"),
    filename: Optional[str] = typer.Option(
        None, help="Custom filename (auto-generated if not provided)"
    ),
    create_template: bool = typer.Option(False, help="Create example template file"),
) -> None:
    """Add manual trade opportunities interactively or via command-line arguments.

    This command allows you to manually enter trading opportunities. Trades are saved
    to JSON files in data/manual_trades/pending/ and will be automatically
    imported when running the 'trade' command.

    Examples:
        # Interactive mode (guided prompts)
        nakedtrader add

        # Command-line mode (single trade)
        nakedtrader add \\
          --symbol AAPL \\
          --strike 180 \\
          --expiration 2025-02-14 \\
          --premium 0.45 \\
          --notes "Strong uptrend"

        # Create template file
        nakedtrader add --create-template
    """
    try:
        manager = ManualTradeManager()

        # Handle template creation
        if create_template:
            template_path = manager.create_template()
            console.print(
                f"[bold green]✓ Template created: {template_path}[/bold green]"
            )
            console.print(
                "\n[dim]Edit the template and save to data/manual_trades/pending/ to import[/dim]"
            )
            return

        console.print("[bold blue]Manual Trade Entry[/bold blue]\n")

        # Check if we have all required args for command-line mode
        if symbol and strike and expiration:
            # Command-line mode
            entry = _create_trade_from_args(symbol, strike, expiration, premium, notes)
            entries = [entry]
            batch_notes = None
        else:
            # Interactive mode
            console.print("[cyan]Enter trade details (press Ctrl+C to cancel)[/cyan]\n")
            entries = []

            # Ask if multiple trades
            multiple = typer.confirm(
                "Do you want to enter multiple trades?", default=False
            )

            if multiple:
                batch_notes = typer.prompt(
                    "Batch notes (optional, press Enter to skip)",
                    default="",
                    show_default=False,
                )
                if batch_notes.strip() == "":
                    batch_notes = None
            else:
                batch_notes = None

            # Collect trades
            trade_count = 0
            while True:
                trade_count += 1
                if multiple:
                    console.print(f"\n[bold]Trade #{trade_count}[/bold]")

                entry = _interactive_trade_entry()
                entries.append(entry)

                if not multiple:
                    break

                another = typer.confirm("\nAdd another trade?", default=True)
                if not another:
                    break

        # Display summary
        console.print(f"\n[bold]Summary: {len(entries)} trade(s) entered[/bold]\n")

        table = Table(title="Manual Trade Entries")
        table.add_column("#", style="dim")
        table.add_column("Symbol", style="cyan bold")
        table.add_column("Strike", justify="right")
        table.add_column("Expiry")
        table.add_column("Premium", justify="right")
        table.add_column("DTE", justify="right")
        table.add_column("Notes")

        for i, entry in enumerate(entries, 1):
            dte = entry.calculate_dte()
            notes_preview = (
                (entry.notes[:30] + "...")
                if entry.notes and len(entry.notes) > 30
                else (entry.notes or "")
            )

            table.add_row(
                str(i),
                entry.symbol,
                f"${entry.strike:.2f}",
                entry.expiration,
                f"${entry.premium:.2f}" if entry.premium else "N/A",
                str(dte),
                notes_preview,
            )

        console.print(table)
        console.print()

        # Confirm save
        if not typer.confirm("Save these trades?", default=True):
            console.print("[yellow]Cancelled - trades not saved[/yellow]")
            return

        # Save to file
        file_path = manager.save_trades(entries, batch_notes, filename)

        console.print(
            f"[bold green]✓ Saved {len(entries)} trade(s) to {file_path}[/bold green]"
        )
        console.print(
            f"\n[dim]Trades will be automatically imported when you run:[/dim]"
        )
        console.print("[dim]  nakedtrader trade[/dim]")

    except KeyboardInterrupt:
        console.print("\n[yellow]Cancelled[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


def _interactive_trade_entry() -> ManualTradeEntry:
    """Interactive prompts for a single trade entry."""

    # Required fields
    symbol = typer.prompt("Symbol").upper().strip()
    strike = typer.prompt("Strike price", type=float)
    expiration = typer.prompt("Expiration (YYYY-MM-DD)")

    # Optional but recommended
    console.print("\n[dim]Optional fields (press Enter to skip):[/dim]")

    premium = typer.prompt("Premium", default="", show_default=False)
    premium = float(premium) if premium.strip() else None

    bid = typer.prompt("Bid", default="", show_default=False)
    bid = float(bid) if bid.strip() else None

    ask = typer.prompt("Ask", default="", show_default=False)
    ask = float(ask) if ask.strip() else None

    stock_price = typer.prompt("Stock price", default="", show_default=False)
    stock_price = float(stock_price) if stock_price.strip() else None

    delta = typer.prompt("Delta", default="", show_default=False)
    delta = float(delta) if delta.strip() else None

    trend = typer.prompt(
        "Trend (uptrend/downtrend/sideways)", default="", show_default=False
    )
    trend = trend if trend.strip() else None

    notes = typer.prompt("Notes/Reasoning", default="", show_default=False)
    notes = notes if notes.strip() else None

    # Calculate OTM % if we have stock price
    otm_pct = None
    if stock_price and strike:
        otm_pct = (stock_price - strike) / stock_price

    return ManualTradeEntry(
        symbol=symbol,
        strike=strike,
        expiration=expiration,
        premium=premium,
        bid=bid,
        ask=ask,
        delta=delta,
        otm_pct=otm_pct,
        stock_price=stock_price,
        trend=trend,
        notes=notes,
    )


def _create_trade_from_args(
    symbol: str,
    strike: float,
    expiration: str,
    premium: Optional[float],
    notes: Optional[str],
) -> ManualTradeEntry:
    """Create trade entry from command-line arguments."""
    return ManualTradeEntry(
        symbol=symbol,
        strike=strike,
        expiration=expiration,
        premium=premium,
        notes=notes,
    )


@app.command(name="files")
def list_manual_trade_files(
    imported: bool = typer.Option(
        False, help="Show imported trades instead of pending"
    ),
) -> None:
    """List pending or imported manual trade JSON files.

    Shows all manual trade JSON files waiting to be imported (pending)
    or previously imported files (imported). Note: Web interface trades
    go directly to the database and won't appear here.

    Examples:
        # List pending trade files
        nakedtrader files

        # List imported history
        nakedtrader files --imported
    """
    try:
        manager = ManualTradeManager()

        if imported:
            console.print("[bold blue]Imported Manual Trades[/bold blue]\n")
            directory = manager.imported_dir
        else:
            console.print("[bold blue]Pending Manual Trades[/bold blue]\n")
            directory = manager.pending_dir

        json_files = list(directory.glob("*.json"))

        if not json_files:
            console.print(f"[yellow]No files in {directory}[/yellow]")
            return

        table = Table(title=f"{len(json_files)} file(s)")
        table.add_column("File", style="cyan")
        table.add_column("Modified", justify="right")
        table.add_column("Trades", justify="right")
        table.add_column("Notes")

        for json_file in sorted(
            json_files, key=lambda f: f.stat().st_mtime, reverse=True
        ):
            try:
                with open(json_file) as f:
                    data = json.load(f)

                trade_count = len(data.get("opportunities", []))
                notes = data.get("notes", "")
                notes_preview = (
                    (notes[:40] + "...") if notes and len(notes) > 40 else notes
                )

                modified = datetime.fromtimestamp(json_file.stat().st_mtime)

                table.add_row(
                    json_file.name,
                    modified.strftime("%Y-%m-%d %H:%M"),
                    str(trade_count),
                    notes_preview or "[dim]no notes[/dim]",
                )
            except Exception as e:
                table.add_row(json_file.name, "N/A", "N/A", f"[red]Error: {e}[/red]")

        console.print(table)
        console.print(f"\n[dim]Directory: {directory}[/dim]")

    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        raise typer.Exit(1)


@app.command(name="pending")
def show_pending_trades(
    all_sources: bool = typer.Option(
        False, help="Show all manual trades (web + CLI + JSON)"
    ),
    limit: int = typer.Option(50, help="Maximum trades to show"),
) -> None:
    """Show pending manual trades ready for execution.

    Displays all manual trades from the database that have been entered via
    web interface or CLI and are ready to be validated and executed by the
    trade command. This is the main command for viewing trades to be executed.

    Examples:
        # Show pending web trades
        nakedtrader pending

        # Show all manual trade sources
        nakedtrader pending --all-sources

        # Show more results
        nakedtrader pending --limit 100
    """
    try:
        console.print("[bold blue]Pending Manual Trades[/bold blue]\n")

        with get_db_session() as session:
            # Build query for manual trades
            query = (
                session.query(ScanOpportunity)
                .join(ScanResult)
                .filter(ScanOpportunity.executed == False)
                .order_by(ScanOpportunity.created_at.desc())
            )

            # Filter by source
            if all_sources:
                query = query.filter(
                    ScanOpportunity.source.in_(["manual_web", "manual"])
                )
            else:
                query = query.filter(ScanOpportunity.source == "manual_web")

            opportunities = query.limit(limit).all()

            if not opportunities:
                console.print("[yellow]No pending manual trades found[/yellow]\n")
                console.print("[dim]Add trades via:[/dim]")
                console.print("  • Web interface: nakedtrader dashboard")
                console.print("  • CLI: nakedtrader add")
                return

            # Display summary
            console.print(
                f"[green]Found {len(opportunities)} pending manual trades[/green]\n"
            )

            # Group by source
            web_count = sum(1 for opp in opportunities if opp.source == "manual_web")
            cli_count = sum(1 for opp in opportunities if opp.source == "manual")

            if web_count > 0:
                console.print(f"  • Web interface: {web_count} trades")
            if cli_count > 0:
                console.print(f"  • CLI/JSON: {cli_count} trades")

            console.print()

            # Display table
            table = Table(title="Pending Manual Trades")
            table.add_column("ID", style="dim", justify="right")
            table.add_column("Symbol", style="cyan bold")
            table.add_column("Type", style="yellow")
            table.add_column("Strike", justify="right")
            table.add_column("Expiration")
            table.add_column("Premium", justify="right")
            table.add_column("Delta", justify="right")
            table.add_column("DTE", justify="right")
            table.add_column("Source", style="dim")
            table.add_column("Notes")

            for opp in opportunities:
                notes_preview = ""
                if opp.entry_notes:
                    notes_preview = (
                        (opp.entry_notes[:30] + "...")
                        if len(opp.entry_notes) > 30
                        else opp.entry_notes
                    )

                table.add_row(
                    str(opp.id),
                    opp.symbol,
                    opp.option_type,
                    f"${opp.strike:.2f}",
                    opp.expiration.strftime("%Y-%m-%d"),
                    f"${opp.premium:.2f}" if opp.premium else "—",
                    f"{opp.delta:.2f}" if opp.delta else "—",
                    str(opp.dte) if opp.dte else "—",
                    "web" if opp.source == "manual_web" else "cli",
                    notes_preview or "[dim]—[/dim]",
                )

            console.print(table)

            # Next steps
            console.print(f"\n[bold]Next Steps:[/bold]")
            console.print(
                "  • Execute only these trades: nakedtrader trade --manual-only"
            )
            console.print(
                "  • Execute manual trades: nakedtrader trade --manual-only"
            )
            console.print(
                "  • Dry run first: nakedtrader trade --manual-only --dry-run"
            )

    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="history")
def scan_history(
    days: int = typer.Option(30, help="Number of days to look back"),
    source: Optional[str] = typer.Option(
        None, help="Filter by source (manual, manual_web, ibkr)"
    ),
    symbol: Optional[str] = typer.Option(None, help="Filter by symbol"),
    limit: int = typer.Option(50, help="Maximum number of scans to show"),
) -> None:
    """View historical scan results from database.

    Query and display past scans with optional filters. Shows scan metadata,
    candidate counts, and execution times.

    Examples:
        # Show last 30 days of scans
        nakedtrader history

        # Show only manual scans from last 7 days
        nakedtrader history --days 7 --source manual

        # Show scans containing AAPL opportunities
        nakedtrader history --symbol AAPL

        # Show last 100 scans
        nakedtrader history --limit 100
    """
    try:
        console.print("[bold blue]Scan History[/bold blue]\n")

        with get_db_session() as session:
            scan_repo = ScanRepository(session)

            # Get scans
            scans = scan_repo.get_recent_scans(days=days, source=source, limit=limit)

            if not scans:
                console.print("[yellow]No scans found matching criteria[/yellow]")
                return

            # If symbol filter, get opportunities and filter scans
            if symbol:
                scan_ids_with_symbol = set()
                for scan in scans:
                    opportunities = scan_repo.get_opportunities_by_scan(scan.id)
                    if any(
                        opp.symbol.upper() == symbol.upper() for opp in opportunities
                    ):
                        scan_ids_with_symbol.add(scan.id)

                scans = [s for s in scans if s.id in scan_ids_with_symbol]

                if not scans:
                    console.print(
                        f"[yellow]No scans found with {symbol} opportunities[/yellow]"
                    )
                    return

            # Display scans table
            table = Table(title=f"Scan History ({len(scans)} scans)")
            table.add_column("ID", style="dim", justify="right")
            table.add_column("Date", style="cyan")
            table.add_column("Source", style="yellow")
            table.add_column("Candidates", justify="right")
            table.add_column("Validated", justify="right")
            table.add_column("Exec Time", justify="right")
            table.add_column("Notes")

            for scan in scans:
                exec_time = (
                    f"{scan.execution_time_seconds:.1f}s"
                    if scan.execution_time_seconds
                    else "—"
                )
                notes_preview = ""
                if scan.notes:
                    notes_preview = (
                        (scan.notes[:30] + "...")
                        if len(scan.notes) > 30
                        else scan.notes
                    )

                table.add_row(
                    str(scan.id),
                    scan.scan_timestamp.strftime("%Y-%m-%d %H:%M"),
                    scan.source,
                    str(scan.total_candidates),
                    str(scan.validated_count) if scan.validated_count is not None else "—",
                    exec_time,
                    notes_preview or "[dim]—[/dim]",
                )

            console.print(table)

            # Display statistics
            console.print(f"\n[bold]Statistics:[/bold]")
            stats = scan_repo.get_scan_statistics(days=days)

            total_scans = stats.get("total_scans", 0)
            total_opps = stats.get("total_opportunities", 0)
            avg_per_scan = total_opps / total_scans if total_scans > 0 else 0

            console.print(f"  Total scans: {total_scans}")
            console.print(f"  Total opportunities: {total_opps}")
            console.print(f"  Avg opportunities/scan: {avg_per_scan:.1f}")

            if stats.get("by_source"):
                console.print(f"\n  [bold]By Source:[/bold]")
                for src, count in stats["by_source"].items():
                    console.print(f"    {src}: {count} scans")

            console.print(
                f"\n[dim]Tip: Use 'nakedtrader details <id>' to view full scan details[/dim]"
            )

    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="details")
def scan_details(
    scan_id: int = typer.Argument(..., help="Scan ID to view details for"),
    show_rejected: bool = typer.Option(False, help="Show rejected opportunities"),
) -> None:
    """View detailed information about a specific scan.

    Shows all opportunities from a scan, including validation status,
    pricing data, and rejection reasons.

    Examples:
        # View scan details
        nakedtrader details 123

        # Include rejected opportunities
        nakedtrader details 123 --show-rejected
    """
    try:
        with get_db_session() as session:
            scan_repo = ScanRepository(session)

            # Get scan
            scan = session.query(ScanResult).filter(ScanResult.id == scan_id).first()

            if not scan:
                console.print(f"[bold red]✗ Scan {scan_id} not found[/bold red]")
                raise typer.Exit(1)

            # Display scan metadata
            console.print(f"[bold blue]Scan #{scan.id} Details[/bold blue]\n")

            metadata = Table(show_header=False, box=None)
            metadata.add_column("Field", style="cyan", width=20)
            metadata.add_column("Value", style="white")

            metadata.add_row(
                "Timestamp", scan.scan_timestamp.strftime("%Y-%m-%d %H:%M:%S")
            )
            metadata.add_row("Source", scan.source)
            metadata.add_row("Total Candidates", str(scan.total_candidates))
            metadata.add_row("Validated Count", str(scan.validated_count or 0))
            metadata.add_row(
                "Execution Time",
                f"{scan.execution_time_seconds:.2f}s"
                if scan.execution_time_seconds
                else "—",
            )
            if scan.notes:
                metadata.add_row("Notes", scan.notes)

            console.print(metadata)
            console.print()

            # Get opportunities
            opportunities = scan_repo.get_opportunities_by_scan(scan_id)

            if not opportunities:
                console.print("[yellow]No opportunities in this scan[/yellow]")
                return

            # Filter if not showing rejected
            if not show_rejected:
                opportunities = [
                    opp for opp in opportunities if opp.validation_status != "rejected"
                ]

            # Display opportunities table
            table = Table(title=f"Opportunities ({len(opportunities)})")
            table.add_column("#", style="dim", justify="right")
            table.add_column("Symbol", style="cyan bold")
            table.add_column("Strike", justify="right")
            table.add_column("Expiration")
            table.add_column("Premium", justify="right")
            table.add_column("Delta", justify="right")
            table.add_column("OTM%", justify="right")
            table.add_column("Status", style="yellow")
            table.add_column("Executed")

            for i, opp in enumerate(opportunities, 1):
                table.add_row(
                    str(i),
                    opp.symbol,
                    f"${opp.strike:.2f}",
                    opp.expiration.strftime("%Y-%m-%d"),
                    f"${opp.premium:.2f}" if opp.premium else "—",
                    f"{opp.delta:.2f}" if opp.delta else "—",
                    f"{opp.otm_pct:.1%}" if opp.otm_pct else "—",
                    opp.validation_status or "—",
                    "✓" if opp.executed else "—",
                )

            console.print(table)

            # Show execution summary
            executed_count = sum(1 for opp in opportunities if opp.executed)
            if executed_count > 0:
                console.print(
                    f"\n[green]✓ {executed_count} opportunities were executed[/green]"
                )

            # Show rejected reasons if any
            rejected = [
                opp
                for opp in opportunities
                if opp.validation_status == "rejected" and opp.rejection_reason
            ]
            if rejected and not show_rejected:
                console.print(
                    f"\n[dim]{len(rejected)} opportunities rejected (use --show-rejected to see them)[/dim]"
                )

    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="quote")
def quote(
    symbol: str = typer.Argument(..., help="Stock symbol to quote"),
    option: bool = typer.Option(
        False, help="Get option chain quote (requires strike/exp)"
    ),
    strike: float = typer.Option(None, help="Strike price for option"),
    expiration: str = typer.Option(
        None, help="Expiration date (YYYY-MM-DD) for option"
    ),
) -> None:
    """Get real-time quote for a stock or option.

    Useful for testing if market data subscriptions are working.

    Examples:
        # Get stock quote
        nakedtrader quote AAPL

        # Get option quote
        nakedtrader quote AAPL --option --strike 150 --expiration 2026-02-21
    """
    try:
        from ib_insync import Stock, Option as IBOption

        console.print(f"[bold blue]Quote: {symbol}[/bold blue]\n")

        config = get_config()

        client = connect_to_ibkr_with_error_handling(config, console)
        console.print("✓ Connected to IBKR\n")

        if option:
            # Option quote
            if not strike or not expiration:
                console.print(
                    "[bold red]✗ Option quotes require --strike and --expiration[/bold red]"
                )
                client.disconnect()
                raise typer.Exit(1)

            # Parse expiration
            try:
                exp_date = datetime.strptime(expiration, "%Y-%m-%d")
                exp_str = exp_date.strftime("%Y%m%d")
            except ValueError:
                console.print(
                    "[bold red]✗ Invalid date format. Use YYYY-MM-DD[/bold red]"
                )
                client.disconnect()
                raise typer.Exit(1)

            # Create option contract
            contract = client.get_option_contract(
                symbol=symbol,
                expiration=exp_str,
                strike=strike,
                right="P",
            )

            qualified = client.qualify_contract(contract)
            if not qualified:
                console.print(
                    f"[bold red]✗ Could not find option: {symbol} ${strike} PUT {expiration}[/bold red]"
                )
                client.disconnect()
                raise typer.Exit(1)

            console.print(
                f"[cyan]Option: {symbol} ${strike:.2f} PUT expiring {expiration}[/cyan]\n"
            )

        else:
            # Stock quote
            contract = Stock(symbol, "SMART", "USD")
            qualified = client.ib.qualifyContracts(contract)

            if not qualified:
                console.print(f"[bold red]✗ Could not find stock: {symbol}[/bold red]")
                client.disconnect()
                raise typer.Exit(1)

            qualified = qualified[0]
            console.print(f"[cyan]Stock: {symbol}[/cyan]\n")

        # Request market data
        with console.status("[bold yellow]Fetching market data..."):
            ticker = client.ib.reqMktData(qualified, snapshot=True)
            client.ib.sleep(4)  # Wait longer for options data

        # Display results
        if ticker:
            from rich.table import Table
            import math

            table = Table(title="Market Data")
            table.add_column("Field", style="cyan")
            table.add_column("Value", justify="right", style="green")

            # Check if we have valid data (not None, not NaN, greater than 0)
            has_data = False

            def is_valid(value):
                """Check if value is valid (not None, not NaN, greater than 0)."""
                return value is not None and not math.isnan(value) and value > 0

            # Try various price fields (options may have different fields populated)
            if is_valid(ticker.last):
                table.add_row("Last", f"${ticker.last:.2f}")
                has_data = True

            if is_valid(ticker.bid):
                table.add_row("Bid", f"${ticker.bid:.2f}")
                has_data = True

            if is_valid(ticker.ask):
                table.add_row("Ask", f"${ticker.ask:.2f}")
                has_data = True

            if is_valid(ticker.close):
                table.add_row("Close", f"${ticker.close:.2f}")
                has_data = True

            # For options, also check model price
            if hasattr(ticker, "modelGreeks") and ticker.modelGreeks:
                if is_valid(getattr(ticker.modelGreeks, "optPrice", None)):
                    table.add_row("Model Price", f"${ticker.modelGreeks.optPrice:.2f}")
                    has_data = True

            # Check for lastPrice attribute (sometimes used for options)
            if hasattr(ticker, "lastPrice") and is_valid(ticker.lastPrice):
                table.add_row("Last Price", f"${ticker.lastPrice:.2f}")
                has_data = True

            # Volume (NaN-safe)
            if is_valid(ticker.volume):
                table.add_row("Volume", f"{ticker.volume:,.0f}")

            # High/Low
            if is_valid(ticker.high):
                table.add_row("High", f"${ticker.high:.2f}")

            if is_valid(ticker.low):
                table.add_row("Low", f"${ticker.low:.2f}")

            if has_data:
                console.print(table)
                console.print("\n[green]✓ Market data is available[/green]")
            else:
                console.print("[yellow]⚠ No market data available[/yellow]")
                console.print("[dim]Possible causes:[/dim]")
                console.print("  - Market is closed")
                console.print("  - No market data subscription for this asset")
                console.print("  - Symbol not found")
        else:
            console.print("[bold red]✗ No data returned[/bold red]")

        # Clean up market data subscription
        try:
            client.ib.cancelMktData(qualified)
        except:
            pass  # Ignore errors when canceling

        client.disconnect()

    except typer.Exit:
        # Re-raise typer.Exit cleanly without traceback
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error getting quote: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="chain")
def option_chain(
    symbol: str = typer.Argument(..., help="Stock symbol"),
    right: str = typer.Option("P", help="Option type (P for PUT, C for CALL)"),
    max_expirations: int = typer.Option(5, help="Max expirations to show"),
) -> None:
    """Browse available option chains for a symbol.

    Shows available strikes and expirations to help find valid contracts.

    Examples:
        # Show SLV put options
        nakedtrader chain SLV

        # Show AAPL call options
        nakedtrader chain AAPL --right C
    """
    try:
        from ib_insync import Stock
        from rich.table import Table

        console.print(f"[bold blue]Option Chain: {symbol}[/bold blue]\n")

        config = get_config()

        client = connect_to_ibkr_with_error_handling(config, console)
        console.print("✓ Connected to IBKR\n")

        # Get stock contract
        stock = Stock(symbol, "SMART", "USD")
        qualified_stock = client.ib.qualifyContracts(stock)

        if not qualified_stock:
            console.print(f"[bold red]✗ Could not find stock: {symbol}[/bold red]")
            client.disconnect()
            raise typer.Exit(1)

        qualified_stock = qualified_stock[0]

        # Get current stock price (NaN-safe)
        with console.status("[bold yellow]Getting stock price..."):
            stock_price = client.get_stock_price(symbol) or 0

        if stock_price > 0:
            console.print(f"[cyan]Current Price: ${stock_price:.2f}[/cyan]\n")

        # Request option chains
        with console.status("[bold yellow]Fetching option chain..."):
            chains = client.ib.reqSecDefOptParams(
                qualified_stock.symbol,
                "",
                qualified_stock.secType,
                qualified_stock.conId,
            )

        if not chains:
            console.print(f"[bold red]✗ No option chains found for {symbol}[/bold red]")
            client.disconnect()
            raise typer.Exit(1)

        # Find the main chain (usually first one)
        chain = chains[0]

        # Show available expirations
        console.print(
            f"[bold]Available Expirations:[/bold] (showing first {max_expirations})"
        )
        expirations = sorted(chain.expirations)[:max_expirations]

        exp_table = Table()
        exp_table.add_column("#", style="cyan")
        exp_table.add_column("Expiration", style="green")
        exp_table.add_column("DTE", justify="right")

        from src.utils.timezone import us_trading_date

        today = us_trading_date()

        for i, exp in enumerate(expirations, 1):
            exp_date = datetime.strptime(exp, "%Y%m%d").date()
            dte = (exp_date - today).days
            exp_table.add_row(str(i), exp, str(dte))

        console.print(exp_table)
        console.print()

        # Show available strikes near current price
        if stock_price > 0:
            strikes = sorted(chain.strikes)

            # Find strikes within 20% of current price
            lower_bound = stock_price * 0.80
            upper_bound = stock_price * 1.20

            nearby_strikes = [s for s in strikes if lower_bound <= s <= upper_bound]

            console.print(f"[bold]Available Strikes Near Current Price:[/bold] (±20%)")

            strike_table = Table()
            strike_table.add_column("Strike", justify="right", style="green")
            strike_table.add_column("% OTM", justify="right")
            strike_table.add_column("Type", style="cyan")

            # Show some strikes
            for strike in nearby_strikes[:15]:  # Show first 15
                otm_pct = ((stock_price - strike) / stock_price) * 100
                strike_type = (
                    "ITM"
                    if strike > stock_price
                    else "OTM"
                    if strike < stock_price
                    else "ATM"
                )
                strike_table.add_row(
                    f"${strike:.2f}", f"{abs(otm_pct):.1f}%", strike_type
                )

            console.print(strike_table)
            console.print()

        # Example quote command
        if expirations and strikes:
            example_strike = (
                nearby_strikes[len(nearby_strikes) // 2]
                if nearby_strikes
                else strikes[len(strikes) // 2]
            )
            example_exp = datetime.strptime(expirations[0], "%Y%m%d").strftime(
                "%Y-%m-%d"
            )

            console.print("[bold]Example Quote Command:[/bold]")
            console.print(
                f"  nakedtrader quote {symbol} --option --strike {example_strike} --expiration {example_exp}"
            )

        client.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error getting option chain: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="market")
def market_status(
    wait: bool = typer.Option(
        False, help="Wait for market to open if currently closed"
    ),
) -> None:
    """Check if the market is currently open for trading.

    Shows current market status and next open/close times.

    Examples:
        # Check market status
        nakedtrader market

        # Wait for market to open (useful for scripts)
        nakedtrader market --wait
    """
    try:
        from datetime import datetime
        import pytz

        console.print("[bold blue]Market Status Check[/bold blue]\n")

        config = get_config()

        client = connect_to_ibkr_with_error_handling(config, console)
        console.print("✓ Connected to IBKR\n")

        # Get market status
        with console.status("[bold yellow]Checking market hours..."):
            status = client.is_market_open()

        # Display status
        if status["is_open"]:
            console.print("[bold green]✓ Market is OPEN[/bold green]")
            console.print(f"  Closes at: {status['next_close']}")
        else:
            status_display = {
                "closed": "🔴 CLOSED",
                "closed_weekend": "🔴 CLOSED (Weekend)",
                "pre_market": "🟡 PRE-MARKET",
                "after_hours": "🟡 AFTER HOURS",
                "unknown": "❓ UNKNOWN",
                "error": "❌ ERROR",
            }
            console.print(
                f"[bold yellow]{status_display.get(status['status'], status['status'])}[/bold yellow]"
            )

            if status["next_open"]:
                console.print(f"  Opens at: {status['next_open']}")

        # Current time
        et_tz = pytz.timezone("America/New_York")
        now_et = datetime.now(et_tz)
        console.print(f"\n  Current time (ET): {now_et.strftime('%Y-%m-%d %H:%M:%S')}")

        # Wait if requested
        if wait and not status["is_open"]:
            console.print("\n[cyan]Waiting for market to open...[/cyan]")
            client.wait_for_market_open()

        client.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error checking market status: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


# ============================================================================
# Learning Engine Commands
# ============================================================================


@app.command(name="learn")
def learn(
    analyse: bool = typer.Option(False, "--analyse", help="Run weekly learning analysis"),
    patterns: bool = typer.Option(False, "--patterns", help="View detected patterns"),
    experiments: bool = typer.Option(False, "--experiments", help="View active experiments"),
    proposals: bool = typer.Option(False, "--proposals", help="View parameter proposals"),
    health: bool = typer.Option(False, "--health", help="Run alpha decay / strategy health check"),
    regimes: bool = typer.Option(False, "--regimes", help="Show regime-aware adaptation status"),
    report: bool = typer.Option(False, "--report", help="Generate learning report"),
    summary: bool = typer.Option(False, "--summary", help="Show learning summary"),
    days: int = typer.Option(30, help="Number of days for summary/report"),
) -> None:
    """Learning engine commands for pattern detection and optimization.

    The learning engine analyses trade outcomes to detect profitable patterns,
    runs A/B experiments, and proposes parameter optimisations.

    Examples:
        # Run weekly learning analysis
        nakedtrader learn --analyse

        # View detected patterns
        nakedtrader learn --patterns

        # View active experiments
        nakedtrader learn --experiments

        # View parameter proposals
        nakedtrader learn --proposals

        # Generate learning report
        nakedtrader learn --report

        # Show 30-day learning summary
        nakedtrader learn --summary --days 30
    """
    try:
        from rich.table import Table

        from src.data.database import get_db_session
        from src.data.models import Experiment, LearningHistory
        from src.data.models import Pattern as PatternModel
        from src.learning import LearningOrchestrator

        console.print("[bold blue]Learning Engine[/bold blue]\n")

        # If no flags specified, show help
        if not any([analyse, patterns, experiments, proposals, health, regimes, report, summary]):
            console.print("[yellow]No action specified. Use --help to see options.[/yellow]\n")
            console.print("[cyan]Common commands:[/cyan]")
            console.print("  nakedtrader learn --analyse    # Run weekly analysis")
            console.print("  nakedtrader learn --patterns   # View patterns")
            console.print("  nakedtrader learn --summary    # Show summary")
            return

        with get_db_session() as db:
            # Run weekly analysis
            if analyse:
                console.print("[bold cyan]Running weekly learning analysis...[/bold cyan]\n")

                orchestrator = LearningOrchestrator(db)
                learning_report = orchestrator.run_weekly_analysis()

                # Display results
                console.print("\n[bold green]✓ Learning Analysis Complete[/bold green]\n")

                results_table = Table(title="Learning Cycle Results")
                results_table.add_column("Metric", style="cyan")
                results_table.add_column("Value", justify="right")

                results_table.add_row("Trades Analysed", str(learning_report.total_trades_analyzed))
                results_table.add_row("Baseline Win Rate", f"{learning_report.baseline_win_rate:.1%}")
                results_table.add_row("Baseline Avg ROI", f"{learning_report.baseline_avg_roi:.2%}")
                results_table.add_row("Patterns Detected", str(learning_report.patterns_detected))
                results_table.add_row("Patterns Validated", str(learning_report.patterns_validated))
                results_table.add_row("Patterns Preliminary", str(learning_report.patterns_preliminary))
                results_table.add_row(
                    "Experiments Adopted", str(len(learning_report.experiments_adopted))
                )
                results_table.add_row(
                    "Experiments Rejected", str(len(learning_report.experiments_rejected))
                )
                results_table.add_row("Proposals Generated", str(len(learning_report.proposals)))
                results_table.add_row("Changes Auto-Applied", str(len(learning_report.changes_applied)))

                # Alpha decay health from the weekly cycle
                if learning_report.alpha_decay_health:
                    health_colors = {"HEALTHY": "green", "WATCH": "yellow", "WARNING": "red", "CRITICAL": "bold red"}
                    hc = health_colors.get(learning_report.alpha_decay_health, "dim")
                    results_table.add_row("Strategy Health", f"[{hc}]{learning_report.alpha_decay_health}[/{hc}]")

                console.print(results_table)

                # Show health reasons if not healthy
                if learning_report.alpha_decay_health and learning_report.alpha_decay_health != "HEALTHY":
                    console.print(f"\n[bold yellow]Health Concerns:[/bold yellow]")
                    for reason in learning_report.alpha_decay_reasons:
                        console.print(f"  • {reason}")

                # Show auto-applied changes
                if learning_report.changes_applied:
                    console.print("\n[bold green]Auto-Applied Changes:[/bold green]")
                    for change in learning_report.changes_applied:
                        console.print(
                            f"  • {change.parameter}: {change.current_value} → {change.proposed_value} "
                            f"(confidence={change.confidence:.1%})"
                        )

                # A4: Show top patterns with stats (even unvalidated)
                console.print("\n[bold cyan]Top Patterns by Sample Size[/bold cyan]\n")

                # Query the pattern_candidate records we just saved
                import json as _json

                candidates = (
                    db.query(LearningHistory)
                    .filter(LearningHistory.event_type == "pattern_candidate")
                    .order_by(LearningHistory.event_date.desc())
                    .limit(learning_report.patterns_detected)
                    .all()
                )

                if candidates:
                    # Sort by sample_size descending
                    def _get_sample(c):
                        return c.sample_size or 0

                    candidates_sorted = sorted(candidates, key=_get_sample, reverse=True)

                    top_table = Table(title="Top 15 Patterns (All Tiers)")
                    top_table.add_column("Pattern", style="cyan", max_width=30)
                    top_table.add_column("Status")
                    top_table.add_column("Samples", justify="right")
                    top_table.add_column("Win Rate", justify="right")
                    top_table.add_column("Avg ROI", justify="right")
                    top_table.add_column("p-value", justify="right")
                    top_table.add_column("Effect (d)", justify="right")
                    top_table.add_column("What's Needed", max_width=35)

                    for c in candidates_sorted[:15]:
                        try:
                            details = _json.loads(c.reasoning) if c.reasoning else {}
                        except (ValueError, TypeError):
                            details = {}

                        status = details.get("validation_status", "REJECTED")
                        style = {"VALIDATED": "bold green", "PRELIMINARY": "yellow", "REJECTED": "dim"}.get(status, "dim")
                        p_val = details.get("p_value")
                        effect = details.get("effect_size")
                        win_rate = details.get("win_rate")
                        avg_roi = details.get("avg_roi")

                        # What does this pattern need to pass?
                        needed = details.get("rejection_reason", "✓ Validated") if status != "VALIDATED" else "✓ Validated"

                        top_table.add_row(
                            c.pattern_name or "-",
                            f"[{style}]{status}[/{style}]",
                            str(c.sample_size or "-"),
                            f"{win_rate:.1%}" if win_rate is not None else "-",
                            f"{avg_roi:.2%}" if avg_roi is not None else "-",
                            f"{p_val:.4f}" if p_val is not None else "-",
                            f"{effect:.3f}" if effect is not None else "-",
                            needed,
                        )

                    console.print(top_table)
                else:
                    console.print("[dim]No pattern candidates recorded.[/dim]")

            # View patterns
            if patterns:
                console.print("[bold cyan]Detected Patterns[/bold cyan]\n")

                pattern_records = (
                    db.query(PatternModel)
                    .filter(PatternModel.status == "active")
                    .order_by(PatternModel.confidence.desc())
                    .all()
                )

                if not pattern_records:
                    console.print("[yellow]No patterns detected yet.[/yellow]")
                    console.print(
                        "[dim]Run --analyse to detect patterns from trade history.[/dim]"
                    )
                else:
                    patterns_table = Table(title=f"{len(pattern_records)} Active Patterns")
                    patterns_table.add_column("Pattern", style="cyan")
                    patterns_table.add_column("Type")
                    patterns_table.add_column("Value")
                    patterns_table.add_column("Samples", justify="right")
                    patterns_table.add_column("Win Rate", justify="right")
                    patterns_table.add_column("Avg ROI", justify="right")
                    patterns_table.add_column("Confidence", justify="right")

                    for p in pattern_records[:20]:  # Show top 20
                        patterns_table.add_row(
                            p.pattern_name,
                            p.pattern_type,
                            p.pattern_value or "-",
                            str(p.sample_size),
                            f"{p.win_rate:.1%}",
                            f"{p.avg_roi:.2%}",
                            f"{p.confidence:.1%}",
                        )

                    console.print(patterns_table)

            # View experiments
            if experiments:
                console.print("[bold cyan]Active Experiments[/bold cyan]\n")

                exp_records = (
                    db.query(Experiment)
                    .filter(Experiment.status == "active")
                    .order_by(Experiment.start_date.desc())
                    .all()
                )

                if not exp_records:
                    console.print("[yellow]No active experiments.[/yellow]")
                else:
                    exp_table = Table(title=f"{len(exp_records)} Active Experiments")
                    exp_table.add_column("Name", style="cyan")
                    exp_table.add_column("Parameter")
                    exp_table.add_column("Control")
                    exp_table.add_column("Test")
                    exp_table.add_column("Control Trades", justify="right")
                    exp_table.add_column("Test Trades", justify="right")
                    exp_table.add_column("Started")

                    for e in exp_records:
                        exp_table.add_row(
                            e.name,
                            e.parameter_name,
                            e.control_value,
                            e.test_value,
                            str(e.control_trades),
                            str(e.test_trades),
                            e.start_date.strftime("%Y-%m-%d"),
                        )

                    console.print(exp_table)

            # View proposals (A5: query both pending and applied proposals)
            if proposals:
                console.print("[bold cyan]Parameter Change Proposals[/bold cyan]\n")

                # Query both generated proposals and applied changes
                recent_proposals = (
                    db.query(LearningHistory)
                    .filter(
                        LearningHistory.event_type.in_(
                            ["proposal_generated", "parameter_adjusted"]
                        )
                    )
                    .order_by(LearningHistory.event_date.desc())
                    .limit(20)
                    .all()
                )

                if not recent_proposals:
                    console.print("[dim]No proposals found. Run --analyse to generate proposals.[/dim]")
                else:
                    proposals_table = Table(title=f"{len(recent_proposals)} Recent Proposals & Changes")
                    proposals_table.add_column("Date")
                    proposals_table.add_column("Status")
                    proposals_table.add_column("Parameter", style="cyan")
                    proposals_table.add_column("Old Value")
                    proposals_table.add_column("New Value")
                    proposals_table.add_column("Confidence", justify="right")
                    proposals_table.add_column("Source Pattern")

                    for p in recent_proposals:
                        status = "[green]APPLIED[/green]" if p.event_type == "parameter_adjusted" else "[yellow]PENDING[/yellow]"
                        proposals_table.add_row(
                            p.event_date.strftime("%Y-%m-%d"),
                            status,
                            p.parameter_changed or "-",
                            p.old_value or "-",
                            p.new_value or "-",
                            f"{p.confidence:.1%}" if p.confidence else "-",
                            p.pattern_name or "-",
                        )

                    console.print(proposals_table)

            # Strategy health / alpha decay check
            if health:
                from src.learning.alpha_decay_monitor import AlphaDecayMonitor

                console.print("[bold cyan]Strategy Health Check (Alpha Decay Monitor)[/bold cyan]\n")

                monitor = AlphaDecayMonitor(db)
                decay_report = monitor.run_analysis()

                # Health status banner
                health_colors = {
                    "HEALTHY": "bold green",
                    "WATCH": "bold yellow",
                    "WARNING": "bold red",
                    "CRITICAL": "bold white on red",
                    "INSUFFICIENT_DATA": "dim",
                }
                style = health_colors.get(decay_report.overall_health, "dim")
                console.print(f"[{style}]Overall Health: {decay_report.overall_health}[/{style}]\n")

                for reason in decay_report.health_reasons:
                    console.print(f"  • {reason}")

                # Rolling metrics table
                if decay_report.rolling_metrics:
                    console.print()
                    rolling_table = Table(title="Rolling Performance Metrics")
                    rolling_table.add_column("Window", style="cyan")
                    rolling_table.add_column("Trades", justify="right")
                    rolling_table.add_column("Win Rate", justify="right")
                    rolling_table.add_column("Avg ROI", justify="right")
                    rolling_table.add_column("Total P&L", justify="right")
                    rolling_table.add_column("Sharpe", justify="right")
                    rolling_table.add_column("Max DD", justify="right")
                    rolling_table.add_column("Loss Streak", justify="right")

                    for m in decay_report.rolling_metrics:
                        rolling_table.add_row(
                            f"{m.window_days}d",
                            str(m.trade_count),
                            f"{m.win_rate:.1%}" if m.trade_count else "-",
                            f"{m.avg_roi:.2%}" if m.trade_count else "-",
                            f"${m.total_pnl:,.0f}" if m.trade_count else "-",
                            f"{m.sharpe_ratio:.2f}" if m.trade_count else "-",
                            f"{m.max_drawdown:.1%}" if m.trade_count else "-",
                            str(m.loss_streak) if m.loss_streak > 0 else "-",
                        )

                    console.print(rolling_table)

                # Regime performance table
                if decay_report.regime_performance:
                    console.print()
                    regime_table = Table(title="Performance by VIX Regime")
                    regime_table.add_column("VIX Regime", style="cyan")
                    regime_table.add_column("Trades", justify="right")
                    regime_table.add_column("Win Rate", justify="right")
                    regime_table.add_column("Avg ROI", justify="right")
                    regime_table.add_column("Avg P&L", justify="right")
                    regime_table.add_column("Sharpe", justify="right")

                    for r in decay_report.regime_performance:
                        wr_style = "green" if r.win_rate >= 0.70 else ("yellow" if r.win_rate >= 0.50 else "red")
                        regime_table.add_row(
                            r.regime.title(),
                            str(r.trade_count),
                            f"[{wr_style}]{r.win_rate:.1%}[/{wr_style}]",
                            f"{r.avg_roi:.2%}",
                            f"${r.avg_pnl:,.0f}",
                            f"{r.sharpe_ratio:.2f}",
                        )

                    console.print(regime_table)

                # CUSUM alerts
                if decay_report.cusum_alerts:
                    console.print()
                    for alert in decay_report.cusum_alerts:
                        alert_style = "red" if alert.direction == "degradation" else "green"
                        console.print(
                            f"[{alert_style}]CUSUM {alert.direction.upper()}: "
                            f"value={alert.cusum_value:.1f} (threshold={alert.threshold:.1f}), "
                            f"{alert.consecutive_trades} consecutive trades[/{alert_style}]"
                        )

            # Regime-aware adaptation status
            if regimes:
                from src.learning.regime_adapter import RegimeAdapter, classify_vix_regime
                from src.learning.experiment_engine import ExperimentEngine

                console.print("[bold cyan]Regime-Aware Adaptation (Phase D)[/bold cyan]\n")

                exp_engine = ExperimentEngine(db)
                orchestrator = LearningOrchestrator(db)
                adapter = orchestrator.regime_adapter

                # Get latest VIX from recent trades
                from src.data.models import Trade as TradeModel
                latest_trade = (
                    db.query(TradeModel)
                    .filter(TradeModel.vix_at_entry.isnot(None))
                    .order_by(TradeModel.entry_date.desc())
                    .first()
                )
                current_vix = latest_trade.vix_at_entry if latest_trade else 20.0

                regime_report = adapter.analyse(current_vix)

                # Current regime banner
                regime_colors = {
                    "low": "cyan", "normal": "green", "elevated": "yellow",
                    "high": "red", "extreme": "bold white on red",
                }
                r_style = regime_colors.get(regime_report.current_regime, "dim")
                console.print(
                    f"[{r_style}]Current Regime: {regime_report.current_regime.upper()} "
                    f"(VIX={current_vix:.1f})[/{r_style}]"
                )

                # Term structure / VIX direction
                if regime_report.term_structure:
                    ts = regime_report.term_structure
                    signal_colors = {
                        "favorable": "green", "neutral": "yellow", "unfavorable": "red",
                    }
                    s_style = signal_colors.get(ts.entry_signal, "dim")
                    vix_change = f"{ts.vix_change_pct:+.1%}" if ts.vix_change_pct is not None else "N/A"
                    console.print(
                        f"VIX Direction: {ts.direction} ({vix_change} over 5d) "
                        f"→ Entry Signal: [{s_style}]{ts.entry_signal.upper()}[/{s_style}]"
                    )

                # Regime parameter table
                console.print()
                param_table = Table(title="Regime Parameter Table")
                param_table.add_column("Regime", style="cyan")
                param_table.add_column("VIX Range")
                param_table.add_column("Profit Target", justify="right")
                param_table.add_column("Stop Loss", justify="right")
                param_table.add_column("Max Pos", justify="right")
                param_table.add_column("DTE Range")
                param_table.add_column("Size %", justify="right")
                param_table.add_column("Entry Gate")
                param_table.add_column("Source")

                vix_ranges = {"low": "<15", "normal": "15-20", "elevated": "20-25", "high": "25-35", "extreme": ">35"}

                for rp in regime_report.all_regime_params:
                    is_active = rp.regime == regime_report.current_regime
                    name = f"[bold]{rp.regime.upper()}[/bold]" if is_active else rp.regime.title()
                    param_table.add_row(
                        name,
                        vix_ranges.get(rp.regime, "?"),
                        f"{rp.profit_target:.0%}",
                        f"{rp.stop_loss:.1f}x",
                        str(rp.max_positions),
                        f"{rp.min_dte}-{rp.max_dte}",
                        f"{rp.position_size_pct:.0%}",
                        rp.entry_gate,
                        rp.source,
                    )

                console.print(param_table)

                # Regime experiments
                if regime_report.regime_experiments:
                    console.print()
                    exp_table = Table(title="Regime Experiments")
                    exp_table.add_column("Name", style="cyan")
                    exp_table.add_column("Status")
                    exp_table.add_column("Control")
                    exp_table.add_column("Test")
                    exp_table.add_column("Trades (C/T)", justify="right")
                    exp_table.add_column("P-value", justify="right")

                    for re in regime_report.regime_experiments:
                        status_style = {"active": "green", "adopted": "bold green", "rejected": "red"}.get(re["status"], "dim")
                        exp_table.add_row(
                            re["name"],
                            f"[{status_style}]{re['status'].upper()}[/{status_style}]",
                            str(re["control"]),
                            str(re["test"]),
                            f"{re['control_trades']}/{re['test_trades']}",
                            f"{re['p_value']:.4f}" if re.get("p_value") else "-",
                        )

                    console.print(exp_table)
                else:
                    console.print("\n[dim]No regime experiments yet. Experiments are created automatically on regime transitions.[/dim]")

                # Recent transitions
                if regime_report.recent_transitions:
                    console.print()
                    console.print("[bold]Recent Regime Transitions (30d):[/bold]")
                    for t in regime_report.recent_transitions[:5]:
                        console.print(f"  {t.get('date', '?')}: {t.get('pattern', '?')}")

            # Generate report
            if report or summary:
                console.print(f"[bold cyan]Learning Summary (Last {days} Days)[/bold cyan]\n")

                orchestrator = LearningOrchestrator(db)
                summary_data = orchestrator.get_learning_summary(days=days)

                summary_table = Table(title=f"Learning Activity ({days} Days)")
                summary_table.add_column("Metric", style="cyan")
                summary_table.add_column("Count", justify="right")

                summary_table.add_row("Total Events", str(summary_data["total_events"]))
                summary_table.add_row("Patterns Detected", str(summary_data["patterns_detected"]))
                summary_table.add_row("Active Patterns", str(summary_data["active_patterns"]))
                summary_table.add_row("Parameter Changes", str(summary_data["parameter_changes"]))
                summary_table.add_row("Weekly Analyses", str(summary_data["weekly_analyses"]))

                console.print(summary_table)

        console.print("\n[dim]For detailed analysis, use: nakedtrader learn --analyse[/dim]")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback

        console.print(traceback.format_exc())
        raise typer.Exit(1)


# ============================================================================
# Phase 2.6: Data Collection Commands
# ============================================================================


@app.command(name="snapshot")
def snapshot_positions():
    """Capture daily snapshots for all open positions.

    Phase 2.6D - Position Monitoring

    This command captures comprehensive position data for all open trades:
    - Current P&L and premium
    - Greeks (delta, theta, gamma, vega, IV)
    - Distance to strike
    - Market context (VIX, SPY)

    Should be run daily at market close (4:00 PM ET) for path analysis.

    Schedule with cron:
        0 16 * * 1-5 cd /path/to/trading_agent && nakedtrader snapshot

    Example:
        nakedtrader snapshot
    """
    console.print("[bold cyan]Capturing Daily Position Snapshots[/bold cyan]\n")

    try:
        # Setup
        setup_logging()

        # Connect to IBKR
        console.print("[dim]Connecting to IBKR...[/dim]")
        ibkr = IBKRClient()
        ibkr.connect()

        if not ibkr.is_connected():
            console.print("[bold red]✗ Failed to connect to IBKR[/bold red]")
            raise typer.Exit(1)

        console.print("[green]✓[/green] Connected to IBKR\n")

        # Get database session
        with get_db_session() as db:
            from src.services.position_snapshot import PositionSnapshotService

            # Create service
            service = PositionSnapshotService(ibkr, db)

            # Capture snapshots
            console.print("[dim]Capturing snapshots for open positions...[/dim]")
            snapshots = service.capture_all_open_positions()

            if not snapshots:
                console.print("[yellow]No open positions to snapshot[/yellow]")
            else:
                # Display results
                table = Table(title=f"Position Snapshots Captured ({len(snapshots)})")
                table.add_column("Symbol", style="cyan")
                table.add_column("P&L", justify="right")
                table.add_column("P&L %", justify="right")
                table.add_column("DTE", justify="right")
                table.add_column("Distance", justify="right")

                for snapshot in snapshots:
                    pnl_color = "green" if snapshot.current_pnl and snapshot.current_pnl > 0 else "red"

                    table.add_row(
                        f"{snapshot.trade.symbol}" if hasattr(snapshot, 'trade') else "N/A",
                        f"[{pnl_color}]${snapshot.current_pnl:.2f}[/{pnl_color}]" if snapshot.current_pnl else "N/A",
                        f"[{pnl_color}]{snapshot.current_pnl_pct:.1%}[/{pnl_color}]" if snapshot.current_pnl_pct else "N/A",
                        str(snapshot.dte_remaining) if snapshot.dte_remaining else "N/A",
                        f"{snapshot.distance_to_strike_pct:.1%}" if snapshot.distance_to_strike_pct else "N/A",
                    )

                console.print(table)
                console.print(f"\n[green]✓[/green] Captured {len(snapshots)} position snapshots")

        # Disconnect
        ibkr.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="export")
def export_learning_data(
    output: Path = typer.Option(
        "data/learning_data.csv",
        "--output",
        "-o",
        help="Output CSV file path"
    ),
    min_quality: float = typer.Option(
        0.7,
        "--min-quality",
        "-q",
        help="Minimum data quality score (0.0-1.0)"
    ),
    show_stats: bool = typer.Option(
        False,
        "--stats",
        "-s",
        help="Show feature statistics"
    ),
):
    """Export learning data for analysis.

    Phase 2.6E - Learning Data Export

    Exports complete trade data including:
    - Entry features (98 fields)
    - Exit outcomes (24 fields)
    - Path analysis from position snapshots

    The data is ready for ML consumption with predictors and targets clearly separated.

    Examples:
        # Export with default quality threshold (70%)
        nakedtrader export

        # Export only high-quality data (90%)
        nakedtrader export --min-quality 0.9

        # Show feature coverage statistics
        nakedtrader export --stats
    """
    console.print("[bold cyan]Exporting Learning Data[/bold cyan]\n")

    try:
        setup_logging()

        with get_db_session() as db:
            from src.learning.data_export import LearningDataExporter

            exporter = LearningDataExporter(db)

            # Export to CSV
            console.print(f"[dim]Exporting to {output}...[/dim]")
            count = exporter.export_to_csv(output, min_quality=min_quality)

            console.print(f"[green]✓[/green] Exported {count} trades to {output}\n")

            # Get and display summary statistics
            summary = exporter.get_summary_statistics()

            if "error" not in summary:
                summary_table = Table(title="Learning Data Summary")
                summary_table.add_column("Metric", style="cyan")
                summary_table.add_column("Value", justify="right")

                summary_table.add_row("Total Trades", str(summary["total_trades"]))
                summary_table.add_row("Win Rate", f"{summary['win_rate']:.1%}" if summary['win_rate'] else "N/A")
                summary_table.add_row("Avg ROI", f"{summary['avg_roi']:.1%}" if summary['avg_roi'] else "N/A")
                summary_table.add_row("Median ROI", f"{summary['median_roi']:.1%}" if summary['median_roi'] else "N/A")
                summary_table.add_row("Avg Quality", f"{summary['avg_quality_score']:.2f}" if summary['avg_quality_score'] else "N/A")
                summary_table.add_row("Avg Days Held", f"{summary['avg_days_held']:.1f}" if summary['avg_days_held'] else "N/A")

                console.print(summary_table)

                # Show top sectors
                if summary.get("sectors"):
                    console.print("\n[bold]Top Sectors:[/bold]")
                    for sector, count in list(summary["sectors"].items())[:5]:
                        console.print(f"  • {sector}: {count} trades")

            # Show feature statistics if requested
            if show_stats:
                console.print("\n[bold cyan]Feature Coverage Statistics[/bold cyan]\n")

                report = exporter.get_data_quality_report()

                console.print(f"Overall Avg Coverage: {report['overall_avg_coverage']:.1%}\n")

                # Critical fields
                console.print("[bold]Critical Fields (80% Predictive Power):[/bold]")
                for field, coverage in report['critical_fields_coverage'].items():
                    color = "green" if coverage >= 0.8 else "yellow" if coverage >= 0.5 else "red"
                    console.print(f"  • [{color}]{field}: {coverage:.1%}[/{color}]")

                # Coverage summary
                console.print(f"\n[green]High coverage (≥90%):[/green] {report['high_coverage_fields']['count']} fields")
                console.print(f"[yellow]Medium coverage (50-90%):[/yellow] {report['medium_coverage_fields']['count']} fields")
                console.print(f"[red]Low coverage (<50%):[/red] {report['low_coverage_fields']['count']} fields")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="stats")
def learning_stats():
    """Show learning data statistics and quality report.

    Phase 2.6E - Data Quality Monitoring

    Displays:
    - Trade count and outcomes
    - Feature coverage statistics
    - Critical fields status
    - Data quality breakdown

    Example:
        nakedtrader stats
    """
    console.print("[bold cyan]Learning Data Statistics[/bold cyan]\n")

    try:
        setup_logging()

        with get_db_session() as db:
            from src.learning.data_export import LearningDataExporter

            exporter = LearningDataExporter(db)

            # Get summary
            summary = exporter.get_summary_statistics()

            if "error" in summary:
                console.print("[yellow]No learning data available yet[/yellow]")
                console.print("[dim]Trades must have both entry and exit snapshots to appear in learning data.[/dim]")
                return

            # Summary statistics
            summary_table = Table(title="Trade Outcomes")
            summary_table.add_column("Metric", style="cyan")
            summary_table.add_column("Value", justify="right")

            summary_table.add_row("Total Trades", str(summary["total_trades"]))
            summary_table.add_row("Win Rate", f"{summary['win_rate']:.1%}" if summary['win_rate'] else "N/A")
            summary_table.add_row("Avg ROI", f"{summary['avg_roi']:.1%}" if summary['avg_roi'] else "N/A")
            summary_table.add_row("Median ROI", f"{summary['median_roi']:.1%}" if summary['median_roi'] else "N/A")
            summary_table.add_row("Avg Quality Score", f"{summary['avg_quality_score']:.2f}" if summary['avg_quality_score'] else "N/A")
            summary_table.add_row("Avg Days Held", f"{summary['avg_days_held']:.1f}" if summary['avg_days_held'] else "N/A")

            console.print(summary_table)

            # Data quality report
            console.print("\n[bold cyan]Data Quality Report[/bold cyan]\n")

            report = exporter.get_data_quality_report()

            console.print(f"[bold]Overall Average Coverage:[/bold] {report['overall_avg_coverage']:.1%}\n")

            # Critical fields
            console.print("[bold]Critical Fields (80% Predictive Power):[/bold]")
            critical = report['critical_fields_coverage']
            for field, coverage in critical.items():
                color = "green" if coverage >= 0.8 else "yellow" if coverage >= 0.5 else "red"
                status = "✓" if coverage >= 0.8 else "⚠" if coverage >= 0.5 else "✗"
                console.print(f"  [{color}]{status}[/{color}] {field}: {coverage:.1%}")

            # Coverage breakdown
            console.print(f"\n[bold]Coverage Breakdown:[/bold]")
            console.print(f"  [green]High (≥90%):[/green] {report['high_coverage_fields']['count']} fields")
            console.print(f"  [yellow]Medium (50-90%):[/yellow] {report['medium_coverage_fields']['count']} fields")
            console.print(f"  [red]Low (<50%):[/red] {report['low_coverage_fields']['count']} fields")

            # Show low coverage fields if any
            if report['low_coverage_fields']['count'] > 0:
                console.print(f"\n[dim]Low coverage fields: {', '.join(report['low_coverage_fields']['fields'][:10])}[/dim]")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)


# ============================================================================
# Phase 4: Sunday-Monday Workflow Commands
# ============================================================================


@app.command(name="staged")
def show_staged() -> None:
    """Display currently staged trades.

    Phase 4.5 - Trade Staging

    Shows all trades that have been staged for Monday execution,
    including their parameters, margin requirements, and expected premium.

    Example:
        nakedtrader staged
    """
    try:
        setup_logging()

        with get_db_session() as db:
            scan_repo = ScanRepository(db)

            # Get staged opportunities
            opportunities = scan_repo.get_opportunities_by_state(OpportunityState.STAGED)

            if not opportunities:
                console.print("[yellow]No staged trades found.[/yellow]")
                console.print("\n[dim]Run 'nakedtrader stage' to stage trades for Monday.[/dim]")
                return

            # Convert to StagedOpportunity format
            from src.services.premarket_validator import StagedOpportunity

            staged = [
                StagedOpportunity(
                    id=opp.id,
                    symbol=opp.symbol,
                    strike=opp.strike,
                    expiration=opp.expiration.isoformat() if opp.expiration else "",
                    staged_stock_price=opp.stock_price or 0.0,
                    staged_limit_price=opp.staged_limit_price or 0.0,
                    staged_contracts=opp.staged_contracts or 0,
                    staged_margin=opp.staged_margin or 0.0,
                    otm_pct=opp.otm_pct or 0.0,
                    state=opp.state or "STAGED",
                )
                for opp in opportunities
            ]

            # Get session identifier
            session = opportunities[0].execution_session if opportunities else None

            # Display staged trades
            run_show_staged(staged, session=session, console=console)

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="unstage")
def cancel_staged(
    confirm: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Cancel all staged/in-progress trades.

    Phase 4.5 - Trade Staging

    Removes all pre-execution trades (STAGED, VALIDATING, READY,
    ADJUSTING, CONFIRMED), marking them as expired.
    This is useful if you want to restart a session
    or decide not to execute the planned trades.

    Example:
        nakedtrader unstage
        nakedtrader unstage --yes  # Skip confirmation
    """
    try:
        setup_logging()

        with get_db_session() as db:
            # Query all pre-execution states, not just STAGED
            pre_exec_states = [
                OpportunityState.STAGED.name,
                OpportunityState.VALIDATING.name,
                OpportunityState.READY.name,
                OpportunityState.ADJUSTING.name,
                OpportunityState.CONFIRMED.name,
            ]
            from src.data.models import ScanOpportunity as ScanOpp

            opportunities = (
                db.query(ScanOpp)
                .filter(
                    ScanOpp.state.in_(pre_exec_states),
                    ScanOpp.executed == False,  # noqa: E712
                )
                .all()
            )

            if not opportunities:
                console.print("[yellow]No staged trades found.[/yellow]")
                return

            console.print(f"[yellow]Found {len(opportunities)} staged trades[/yellow]")

            # Confirm cancellation
            if not confirm:
                response = console.input("\n[bold]Cancel all staged trades?[/bold] [y/N]: ").strip().lower()
                if response not in ("y", "yes"):
                    console.print("[dim]Cancelled[/dim]")
                    return

            lifecycle = OpportunityLifecycleManager(db)

            for opp in opportunities:
                lifecycle.transition(
                    opp.id,
                    OpportunityState.EXPIRED,
                    reason="Cancelled by user via unstage command",
                    actor="user",
                )

            db.commit()

            console.print(f"[green]✓ Cancelled {len(opportunities)} staged trades[/green]")

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)


@app.command(name="validate")
def validate_staged(
    at_open: bool = typer.Option(
        False,
        "--at-open",
        help="Run market-open validation (Stage 2) instead of pre-market (Stage 1)",
    ),
) -> None:
    """Validate staged trades before execution.

    Phase 4.4 - Two-Stage Validation

    Two validation stages:
    - Stage 1 (9:15 AM): Pre-market stock price check (default)
    - Stage 2 (9:30 AM): Market-open premium check (use --at-open)

    Stage 1 checks if stock prices have moved significantly since Sunday.
    Stage 2 checks if option premiums match expectations at market open.

    Example:
        nakedtrader validate           # Pre-market check
        nakedtrader validate --at-open # Market-open check
    """
    try:
        setup_logging()
        base_config = get_config()

        # Connect to IBKR
        console.print("[dim]Connecting to IBKR...[/dim]")
        client = connect_to_ibkr_with_error_handling(base_config, console, show_spinner=False)

        try:
            with get_db_session() as db:
                scan_repo = ScanRepository(db)

                # Get staged opportunities
                opportunities = scan_repo.get_opportunities_by_state(OpportunityState.STAGED)

                if not opportunities:
                    console.print("[yellow]No staged trades found.[/yellow]")
                    return

                console.print(f"[cyan]Found {len(opportunities)} staged trades[/cyan]\n")

                # Convert to StagedOpportunity format
                from src.services.premarket_validator import StagedOpportunity

                staged = [
                    StagedOpportunity(
                        id=opp.id,
                        symbol=opp.symbol,
                        strike=opp.strike,
                        expiration=opp.expiration.isoformat() if opp.expiration else "",
                        staged_stock_price=opp.stock_price or 0.0,
                        staged_limit_price=opp.staged_limit_price or 0.0,
                        staged_contracts=opp.staged_contracts or 0,
                        staged_margin=opp.staged_margin or 0.0,
                        otm_pct=opp.otm_pct or 0.0,
                        state=opp.state or "STAGED",
                    )
                    for opp in opportunities
                ]

                # Create validator
                validation_config = ValidationConfig.from_env()
                validator = PremarketValidator(
                    ibkr_client=client,
                    config=validation_config,
                )

                if at_open:
                    # Stage 2: Market-open validation
                    console.print("[bold]STAGE 2: MARKET-OPEN VALIDATION (9:30 AM)[/bold]\n")
                    run_open_validation(
                        opportunities=staged,
                        validator=validator,
                        console=console,
                    )
                else:
                    # Stage 1: Pre-market validation
                    console.print("[bold]STAGE 1: PRE-MARKET VALIDATION (9:15 AM)[/bold]\n")
                    run_premarket_validation(
                        opportunities=staged,
                        validator=validator,
                        console=console,
                    )

        finally:
            client.disconnect()

    except typer.Exit:
        raise
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)


# ============================================================================
# Phase D: Two-Tier Execution with Progressive Automation
# ============================================================================


@app.command(name="execute")
def execute_two_tier(
    mode: str = typer.Option(
        "hybrid",
        "--mode",
        "-m",
        help="Automation mode: hybrid | supervised | autonomous",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--live",
        help="Dry run mode (no real orders) or live execution",
    ),
    skip_confirmation: bool = typer.Option(
        False,
        "--yes",
        "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Execute staged trades with two-tier execution (Phase D).

    Two-Tier Execution Strategy:
      Tier 1 (9:30 AM): Submit all orders while pre-market research still valid
      Tier 2 (9:45-10:30): Retry unfilled when VIX low + spreads tight

    Progressive Automation Modes:
      hybrid:      Automated prep, manual execution trigger (testing)
      supervised:  Automated execution, manual report review (validation)
      autonomous:  Fully automated, alerts only on errors (production)

    Example:
        # Test with manual trigger (hybrid mode)
        nakedtrader execute --mode=hybrid --dry-run

        # Supervised mode (auto-execute, review after)
        nakedtrader execute --mode=supervised --live

        # Fully autonomous (production)
        nakedtrader execute --mode=autonomous --live --yes
    """
    from src.services.two_tier_execution_scheduler import (
        AutomationMode,
        TwoTierExecutionScheduler,
    )
    from src.services.adaptive_order_executor import AdaptiveOrderExecutor
    from src.services.rapid_fire_executor import RapidFireExecutor
    from src.services.market_conditions import MarketConditionMonitor
    from src.services.order_reconciliation import OrderReconciliation

    console.print("[bold cyan]TWO-TIER EXECUTION (Phase D)[/bold cyan]")

    # Validate mode
    try:
        automation_mode = AutomationMode(mode.lower())
    except ValueError:
        console.print(f"[red]✗ Invalid mode: {mode}[/red]")
        console.print("  Valid modes: hybrid, supervised, autonomous")
        raise typer.Exit(1)

    console.print(f"[cyan]Mode: {automation_mode.value.upper()}[/cyan]")

    if dry_run:
        console.print("[yellow]⚠ DRY-RUN MODE - No real orders will be placed[/yellow]\n")
    else:
        console.print("[bold red]⚠ LIVE MODE - Real orders will be placed![/bold red]\n")

    if automation_mode == AutomationMode.AUTONOMOUS and not skip_confirmation:
        console.print("[yellow]Warning: Autonomous mode will execute without manual review.[/yellow]")
        if not typer.confirm("Are you sure you want to continue?"):
            console.print("Cancelled.")
            raise typer.Exit(0)

    try:
        setup_logging()
        base_config = get_config()

        # Connect to IBKR
        console.print("[dim]Connecting to IBKR...[/dim]")
        client = connect_to_ibkr_with_error_handling(base_config, console, show_spinner=False)

        try:
            with get_db_session() as db:
                scan_repo = ScanRepository(db)

                # Get staged opportunities
                opportunities = scan_repo.get_opportunities_by_state(OpportunityState.STAGED)

                if not opportunities:
                    console.print("[yellow]No staged trades found.[/yellow]")
                    console.print("\n[dim]Run 'nakedtrader stage' to stage trades first.[/dim]")
                    return

                console.print(f"[cyan]Found {len(opportunities)} staged trades[/cyan]\n")

                # Convert to StagedOpportunity format
                from src.services.premarket_validator import StagedOpportunity

                staged = [
                    StagedOpportunity(
                        id=opp.id,
                        symbol=opp.symbol,
                        strike=opp.strike,
                        expiration=opp.expiration.isoformat() if opp.expiration else "",
                        staged_stock_price=opp.stock_price or 0.0,
                        staged_limit_price=opp.staged_limit_price or 0.0,
                        staged_contracts=opp.staged_contracts or 0,
                        staged_margin=opp.staged_margin or 0.0,
                        otm_pct=opp.otm_pct or 0.0,
                        state=opp.state or "STAGED",
                    )
                    for opp in opportunities
                ]

                # Initialize components
                validation_config = ValidationConfig.from_env()
                validator = PremarketValidator(
                    ibkr_client=client,
                    config=validation_config,
                )

                limit_calculator = LimitPriceCalculator()

                adaptive_executor = AdaptiveOrderExecutor(
                    ibkr_client=client,
                    limit_calc=limit_calculator,
                )

                # Create risk governor for post-trade margin verification
                position_monitor = PositionMonitor(client, base_config)
                risk_governor = RiskGovernor(client, position_monitor, base_config)

                rapid_fire = RapidFireExecutor(
                    ibkr_client=client,
                    adaptive_executor=adaptive_executor,
                    risk_governor=risk_governor,
                )

                condition_monitor = MarketConditionMonitor(client)

                # Create adaptive strike selector and fill manager
                from src.services.live_strike_selector import LiveStrikeSelector, StrikeSelectionConfig
                from src.services.fill_manager import FillManager, FillManagerConfig

                strike_sel_config = StrikeSelectionConfig.from_env()
                strike_selector = LiveStrikeSelector(
                    ibkr_client=client,
                    config=strike_sel_config,
                    limit_calculator=limit_calculator,
                ) if strike_sel_config.enabled else None

                fill_manager = FillManager(
                    ibkr_client=client,
                    limit_calculator=limit_calculator,
                    config=FillManagerConfig.from_env(),
                )

                # Create two-tier scheduler
                scheduler = TwoTierExecutionScheduler(
                    ibkr_client=client,
                    premarket_validator=validator,
                    rapid_fire_executor=rapid_fire,
                    condition_monitor=condition_monitor,
                    strike_selector=strike_selector,
                    fill_manager=fill_manager,
                    automation_mode=automation_mode,
                    tier2_enabled=True,
                )

                # Run execution
                import asyncio
                import signal

                # Register SIGINT handler to force clean shutdown.
                # ib_insync holds the event loop open, so asyncio.run()
                # can hang on Ctrl+C unless we disconnect IBKR first.
                _original_sigint = signal.getsignal(signal.SIGINT)

                def _sigint_handler(signum, frame):
                    console.print("\n[yellow]⚠ Ctrl+C received — shutting down...[/yellow]")
                    try:
                        client.disconnect()
                    except Exception:
                        pass
                    # Restore original handler so a second Ctrl+C forces exit
                    signal.signal(signal.SIGINT, _original_sigint)
                    raise KeyboardInterrupt

                signal.signal(signal.SIGINT, _sigint_handler)

                try:
                    report = asyncio.run(
                        scheduler.run_monday_morning(staged, dry_run=dry_run)
                    )

                    if report:
                        console.print(f"\n[bold green]✓ Execution complete[/bold green]")
                        console.print(
                            f"[dim]Filled: {report.filled_count} | "
                            f"Working: {report.working_count} | "
                            f"Failed: {report.failed_count}[/dim]"
                        )

                except KeyboardInterrupt:
                    console.print("\n[yellow]⚠ Execution interrupted by user[/yellow]")
                    raise typer.Exit(0)
                except asyncio.CancelledError:
                    console.print("\n[yellow]⚠ Async tasks cancelled[/yellow]")
                    raise typer.Exit(0)
                finally:
                    signal.signal(signal.SIGINT, _original_sigint)

        finally:
            # Ensure IBKR disconnection
            console.print("[dim]Disconnecting from IBKR...[/dim]")
            try:
                client.disconnect()
            except Exception as e:
                console.print(f"[dim]Note: {e}[/dim]")

            # Cancel any pending asyncio tasks
            try:
                import asyncio
                pending = asyncio.all_tasks()
                for task in pending:
                    task.cancel()
            except Exception:
                pass  # No event loop running, that's fine

    except typer.Exit:
        raise
    except KeyboardInterrupt:
        console.print("\n[yellow]Execution cancelled by user[/yellow]")
        raise typer.Exit(0)
    except Exception as e:
        console.print(f"[bold red]✗ Error: {e}[/bold red]")
        import traceback
        console.print(traceback.format_exc())
        raise typer.Exit(1)


# ============================================================================
# Phase C: Order Reconciliation Commands
# ============================================================================


@app.command(name="sync")
def sync_orders(
    date_str: str = typer.Option(
        None, "--date", "-d", help="Date to sync (YYYY-MM-DD), default today"
    ),
    include_filled: bool = typer.Option(
        True, "--include-filled", help="Include filled orders in sync"
    ),
    import_orphans: bool = typer.Option(
        False, "--import-orphans", help="Import orphan orders from IBKR into database"
    ),
):
    """Sync order status between database and TWS.

    Queries TWS for all orders, trades, executions, and fills.
    Updates database with actual fill prices, status, and commissions.
    Optionally imports orphan orders (in IBKR but not in database).

    Example:
        nakedtrader sync
        nakedtrader sync --date 2026-02-03
        nakedtrader sync --import-orphans
    """
    console.print("\n[bold cyan]Order Reconciliation[/bold cyan]\n")

    # Setup
    setup_logging()
    config = get_config()
    init_database()

    # Parse date
    from datetime import datetime
    if date_str:
        try:
            sync_date = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            console.print(f"[red]✗ Invalid date format: {date_str}[/red]")
            console.print("  Use format: YYYY-MM-DD")
            raise typer.Exit(code=1)
    else:
        from src.utils.timezone import us_trading_date
        sync_date = us_trading_date()

    console.print(f"Syncing orders for: [cyan]{sync_date}[/cyan]\n")

    # Connect to IBKR
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Connecting to IBKR...", total=None)

            ibkr_config = config.ibkr
            ibkr_client = IBKRClient(ibkr_config, suppress_errors=True)
            ibkr_client.connect()

    except IBKRConnectionError as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise typer.Exit(code=1)

    try:
        # Get trade repository
        with get_db_session() as session:
            trade_repo = TradeRepository(session)

            # Create reconciliation service
            from src.services.order_reconciliation import OrderReconciliation
            import asyncio

            reconciler = OrderReconciliation(ibkr_client, trade_repo)

            # Run reconciliation
            console.print("[yellow]⏳ Fetching orders from TWS...[/yellow]")
            report = asyncio.run(reconciler.sync_all_orders(sync_date, include_filled))

            # Display report
            _display_reconciliation_report(report, console)

            # Import orphans if requested
            if import_orphans and report.orphans:
                console.print(f"\n[yellow]⏳ Importing {len(report.orphans)} orphan orders...[/yellow]")
                imported_count = asyncio.run(reconciler.import_orphan_orders(report.orphans, dry_run=False))

                if imported_count > 0:
                    session.commit()  # Commit the imported trades
                    console.print(f"[green]✓ Successfully imported {imported_count} orphan orders[/green]\n")
                else:
                    console.print("[yellow]⚠ No orphan orders were imported (already exist or invalid)[/yellow]\n")
            elif import_orphans and not report.orphans:
                console.print("[green]✓ No orphan orders to import[/green]\n")

    finally:
        ibkr_client.disconnect()


@app.command(name="reconcile")
def reconcile_positions(
    dry_run: bool = typer.Option(
        True, "--dry-run/--live", help="Dry run (preview) or live mode (apply fixes)"
    ),
    yes: bool = typer.Option(
        False, "--yes", "-y", help="Skip confirmation prompt (for unattended/cron use)"
    ),
):
    """Reconcile and sync positions between database and IBKR.

    This command analyzes discrepancies and optionally fixes them:
    - Imports positions from IBKR not in database
    - Closes positions in database not in IBKR
    - Updates quantity mismatches to match IBKR

    Default mode is --dry-run (preview only, no changes).
    Use --live to actually apply the fixes.

    Examples:
        # Preview what would be fixed (safe, read-only)
        nakedtrader reconcile

        # Actually apply the fixes
        nakedtrader reconcile --live
    """
    from datetime import datetime
    from src.data.models import Trade
    from src.services.order_reconciliation import OrderReconciliation
    from src.utils.timezone import us_eastern_now
    import asyncio

    console.print("\n[bold cyan]Position Reconciliation & Sync[/bold cyan]\n")

    mode_text = "[yellow][DRY RUN - Preview Only][/yellow]" if dry_run else "[red][LIVE MODE - Will Apply Fixes][/red]"
    console.print(f"{mode_text}\n")

    if not dry_run:
        console.print("[yellow]⚠️  This will modify your database to match IBKR![/yellow]")
        console.print("[yellow]   - Import missing positions from IBKR[/yellow]")
        console.print("[yellow]   - Close positions not in IBKR[/yellow]")
        console.print("[yellow]   - Update quantity mismatches[/yellow]\n")

        if not yes and not typer.confirm("Are you sure you want to proceed?"):
            console.print("Cancelled.")
            raise typer.Exit(0)

    # Setup
    setup_logging()
    config = get_config()
    init_database()

    # Connect to IBKR
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Connecting to IBKR...", total=None)

            ibkr_config = config.ibkr
            ibkr_client = IBKRClient(ibkr_config, suppress_errors=True)
            ibkr_client.connect()

    except IBKRConnectionError as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise typer.Exit(code=1)

    try:
        with get_db_session() as session:
            trade_repo = TradeRepository(session)
            reconciler = OrderReconciliation(ibkr_client, trade_repo)

            # Step 1: Get reconciliation report
            console.print("[cyan]Analyzing discrepancies...[/cyan]\n")
            report = asyncio.run(reconciler.reconcile_positions())

            # Display report
            _display_position_reconciliation(report, console)

            # Check for entry premium issues in closed trades (always run, even if no position discrepancies)
            console.print("\n[cyan]Checking historical entry premiums for closed trades...[/cyan]\n")

            closed_trades_with_issues = []
            closed_trades = (
                session.query(Trade)
                .filter(Trade.exit_date.isnot(None))
                .filter(Trade.trade_source != "ibkr_import")
                .all()
            )

            if closed_trades:
                # Fetch historical executions to verify entry premiums
                try:
                    ib_executions = ibkr_client.get_historical_executions(days_back=30)

                    # Build entry orders lookup (SLD = Sell to Open)
                    entry_orders = {}
                    for fill in ib_executions:
                        try:
                            contract = fill.contract
                            execution = fill.execution if hasattr(fill, 'execution') else fill

                            if hasattr(contract, 'symbol') and hasattr(contract, 'strike'):
                                key = f"{contract.symbol}_{contract.strike}_{contract.lastTradeDateOrContractMonth}"

                                if execution.side == "SLD":  # Sell = opening short position
                                    if key not in entry_orders or fill.time < entry_orders[key]['time']:
                                        entry_orders[key] = {
                                            'time': fill.time,
                                            'price': execution.avgPrice,
                                        }
                        except Exception as e:
                            logger.debug(f"Skipping fill: {e}")
                            continue

                    # Check each closed trade for entry premium issues
                    for trade in closed_trades:
                        exp_str = trade.expiration.strftime("%Y%m%d")
                        lookup_key = f"{trade.symbol}_{trade.strike}_{exp_str}"

                        if lookup_key in entry_orders:
                            actual_entry_price = entry_orders[lookup_key]['price']
                            current_entry = trade.entry_premium or 0
                            price_diff_pct = abs(actual_entry_price - current_entry) / actual_entry_price if actual_entry_price > 0 else 0

                            if price_diff_pct > 0.05:  # More than 5% difference
                                closed_trades_with_issues.append({
                                    'trade': trade,
                                    'current_entry': current_entry,
                                    'actual_entry': actual_entry_price,
                                    'actual_time': entry_orders[lookup_key]['time'],
                                    'diff_pct': price_diff_pct
                                })

                    if closed_trades_with_issues:
                        console.print(f"  [yellow]Found {len(closed_trades_with_issues)} entry premium mismatches:[/yellow]")
                        for issue in closed_trades_with_issues:
                            t = issue['trade']
                            console.print(
                                f"    [yellow]⚠  {t.symbol} ${t.strike}: "
                                f"DB ${issue['current_entry']:.2f} vs IBKR ${issue['actual_entry']:.2f} "
                                f"({issue['diff_pct']*100:.1f}% diff)[/yellow]"
                            )
                    else:
                        console.print(f"  [green]✓ Entry premiums verified ({len(closed_trades)} closed trades checked)[/green]")

                except Exception as e:
                    logger.error(f"Error checking entry premiums: {e}", exc_info=True)
                    console.print(f"  [yellow]⚠  Could not verify entry premiums: {e}[/yellow]")

            # Determine if we have ANY issues to fix
            has_issues = report.has_discrepancies or len(closed_trades_with_issues) > 0

            if not has_issues:
                console.print("\n[green]✓ No discrepancies found - database is in sync with IBKR![/green]\n")
                return

            # If dry-run, just show what would be fixed and exit
            if dry_run:
                console.print("\n" + "=" * 60)
                console.print("[yellow]DRY RUN COMPLETE - No changes made[/yellow]")
                console.print("[yellow]Run with --live to apply these fixes:[/yellow]")
                console.print("[yellow]  nakedtrader reconcile --live[/yellow]")
                console.print("=" * 60 + "\n")
                return

            # LIVE MODE - Apply fixes
            console.print("\n[bold red]Applying fixes...[/bold red]\n")

            # Step 0: Close assigned positions
            if report.assignments:
                console.print(
                    f"[cyan]Closing {len(report.assignments)} assigned positions...[/cyan]\n"
                )
                assigned_trade_keys = set()
                for event in report.assignments:
                    if not event.matched_trade_id:
                        console.print(
                            f"  [yellow]? {event.symbol} x{event.shares} shares — "
                            f"no matched trade, skipping[/yellow]"
                        )
                        continue

                    trade = (
                        session.query(Trade)
                        .filter(Trade.trade_id == event.matched_trade_id)
                        .first()
                    )
                    if not trade:
                        console.print(
                            f"  [yellow]? {event.symbol} — trade {event.matched_trade_id} "
                            f"not found in DB[/yellow]"
                        )
                        continue
                    if trade.exit_date is not None:
                        console.print(
                            f"  [dim]{event.symbol} ${trade.strike}P — already closed, skipping[/dim]"
                        )
                        assigned_trade_keys.add(
                            f"{trade.symbol}_{float(trade.strike)}_"
                            f"{trade.expiration.strftime('%Y%m%d') if hasattr(trade.expiration, 'strftime') else trade.expiration}_"
                            f"{'P' if str(trade.option_type).upper() in ('PUT', 'P') else trade.option_type}"
                        )
                        continue

                    # Calculate exit at intrinsic value
                    stock_price = event.avg_cost
                    intrinsic = max(trade.strike - stock_price, 0)

                    now = us_eastern_now()
                    trade.exit_date = now
                    trade.exit_premium = intrinsic
                    trade.exit_reason = "assignment"
                    trade.profit_loss = calc_pnl(trade.entry_premium, intrinsic, trade.contracts)
                    trade.profit_pct = calc_pnl_pct(trade.profit_loss, trade.entry_premium, trade.contracts)
                    trade.days_held = (
                        (now.date() - trade.entry_date.date()).days
                        if trade.entry_date
                        else 0
                    )

                    # Determine assignment_status (full vs partial)
                    contracts_assigned = event.contracts_assigned
                    if contracts_assigned >= trade.contracts:
                        trade.assignment_status = "full"
                    else:
                        trade.assignment_status = "partial"

                    # Build position key for filtering from in_db_not_ibkr
                    from src.utils.position_key import position_key_from_trade
                    assigned_trade_keys.add(position_key_from_trade(trade))

                    pl_color = "green" if trade.profit_loss >= 0 else "red"
                    pl_val = trade.profit_loss or 0
                    pl_text = f"${pl_val:,.2f}" if pl_val >= 0 else f"-${abs(pl_val):,.2f}"

                    console.print(
                        f"  [{pl_color}]✓ {trade.symbol} ${trade.strike}P assigned — "
                        f"exit intrinsic=${intrinsic:.2f}, "
                        f"P/L: {pl_text} ({trade.assignment_status})[/{pl_color}]"
                    )

                    # Create StockPosition record for the resulting stock holding
                    try:
                        from src.services.stock_position_service import StockPositionService
                        stock_svc = StockPositionService(session)
                        sp = stock_svc.create_from_assignment(event)
                        if sp:
                            console.print(
                                f"    [cyan]→ StockPosition created: {sp.symbol} "
                                f"x{sp.shares} shares, cost basis=${sp.cost_basis_per_share:.2f}[/cyan]"
                            )
                    except Exception as sp_err:
                        console.print(
                            f"    [yellow]⚠  StockPosition creation failed: {sp_err}[/yellow]"
                        )

                session.commit()
                console.print(
                    f"\n  [green]✓ Closed {len(report.assignments)} assigned positions[/green]\n"
                )

                # Remove assigned trades from in_db_not_ibkr so they don't
                # trigger the "no exit order found" warning
                if assigned_trade_keys and report.in_db_not_ibkr:
                    original_count = len(report.in_db_not_ibkr)
                    report.in_db_not_ibkr = [
                        (key, t) for key, t in report.in_db_not_ibkr
                        if key not in assigned_trade_keys
                    ]
                    filtered = original_count - len(report.in_db_not_ibkr)
                    if filtered:
                        logger.info(
                            f"Removed {filtered} assigned trades from in_db_not_ibkr"
                        )

            # Step 1: Fix entry premiums for closed trades
            if closed_trades_with_issues:
                console.print(f"[cyan]Step 1:[/cyan] Fixing {len(closed_trades_with_issues)} entry premiums...\n")

                for issue in closed_trades_with_issues:
                    trade = issue['trade']
                    old_entry = issue['current_entry']
                    new_entry = issue['actual_entry']
                    new_time = issue['actual_time']

                    old_pl = trade.profit_loss or 0

                    # Update entry premium and date
                    trade.entry_premium = new_entry
                    trade.entry_date = new_time

                    # Recalculate P/L
                    if trade.exit_premium is not None:
                        trade.profit_loss = calc_pnl(trade.entry_premium, trade.exit_premium, trade.contracts)
                        trade.profit_pct = calc_pnl_pct(trade.profit_loss, trade.entry_premium, trade.contracts)

                    # Update reasoning
                    if trade.ai_reasoning and "IMPORTED" in trade.ai_reasoning:
                        trade.ai_reasoning += f"\nEntry premium corrected from ${old_entry:.2f} to ${new_entry:.2f} using IBKR STO execution."

                    pl_color = "green" if trade.profit_loss >= 0 else "red"
                    pl_text = f"${trade.profit_loss:,.2f}" if trade.profit_loss >= 0 else f"-${abs(trade.profit_loss):,.2f}"

                    console.print(
                        f"  [{pl_color}]✓ {trade.symbol} ${trade.strike}: "
                        f"Entry ${old_entry:.2f} → ${new_entry:.2f}, "
                        f"P/L ${old_pl:,.2f} → {pl_text}[/{pl_color}]"
                    )

                session.commit()
                console.print(f"\n  [green]✓ Fixed {len(closed_trades_with_issues)} entry premiums[/green]\n")

            # Step 2: Import orphan positions (in IBKR but not in DB)
            if report.in_ibkr_not_db:
                console.print(f"[cyan]Step 1:[/cyan] Importing {len(report.in_ibkr_not_db)} orphan positions from IBKR...\n")
                imported_count = asyncio.run(reconciler.import_orphan_positions(dry_run=False))
                session.commit()
                console.print(f"  [green]✓ Imported {imported_count} positions[/green]\n")

            # Step 3: Handle positions in DB but not in IBKR
            if report.in_db_not_ibkr:
                console.print(f"\n[cyan]Step 2:[/cyan] Found {len(report.in_db_not_ibkr)} positions in DB but not in IBKR\n")

                console.print("[yellow]⚠️  These positions were closed in IBKR but database doesn't have exit data.[/yellow]")
                console.print("[yellow]   We need to fetch actual exit prices from IBKR to calculate correct P/L.[/yellow]\n")

                # Try to fetch exit orders from IBKR
                console.print("  Fetching exit order data from IBKR...")

                try:
                    # Get historical executions from IBKR (last 7 days)
                    console.print("  [dim]Requesting historical executions from IBKR (last 7 days)...[/dim]")
                    ib_executions = ibkr_client.get_historical_executions(days_back=7)
                    console.print(f"  [dim]Found {len(ib_executions)} executions[/dim]\n")

                    # Build lookup by symbol/strike/expiration for both entries and exits
                    # Note: reqExecutions returns Fill objects, not Execution objects
                    entry_orders = {}  # SLD (Sell to Open) - for entry premiums
                    exit_orders = {}   # BOT (Buy to Close) - for exit premiums

                    for fill in ib_executions:
                        try:
                            # Fill object has: contract, execution, commissionReport, time
                            contract = fill.contract
                            execution = fill.execution if hasattr(fill, 'execution') else fill

                            if hasattr(contract, 'symbol') and hasattr(contract, 'strike'):
                                key = f"{contract.symbol}_{contract.strike}_{contract.lastTradeDateOrContractMonth}"

                                # SLD = Sell (opening a short position) - ENTRY
                                if execution.side == "SLD":
                                    if key not in entry_orders or fill.time < entry_orders[key]['time']:
                                        # Use earliest SLD for this contract (first entry)
                                        entry_orders[key] = {
                                            'time': fill.time,
                                            'price': execution.avgPrice,
                                            'fill': fill,
                                            'execution': execution
                                        }

                                # BOT = Buy (closing a short position) - EXIT
                                elif execution.side == "BOT":
                                    if key not in exit_orders or fill.time > exit_orders[key]['time']:
                                        # Use latest BOT for this contract (final exit)
                                        exit_orders[key] = {
                                            'time': fill.time,
                                            'price': execution.avgPrice,
                                            'fill': fill,
                                            'execution': execution
                                        }
                        except Exception as e:
                            logger.debug(f"Skipping fill: {e}")
                            continue

                    closed_count = 0
                    needs_manual_review = []

                    for contract_key, db_trade in report.in_db_not_ibkr:
                        trade = session.query(Trade).filter(
                            Trade.id == db_trade.id
                        ).first()

                        if not trade:
                            continue

                        # Look for exit order in IBKR data
                        exp_str = trade.expiration.strftime("%Y%m%d")
                        lookup_key = f"{trade.symbol}_{trade.strike}_{exp_str}"

                        if lookup_key in exit_orders:
                            # Found exit order - use actual exit price
                            exit_data = exit_orders[lookup_key]
                            exit_price = exit_data['price']
                            exit_time = exit_data['time']

                            trade.exit_date = exit_time
                            trade.exit_premium = exit_price
                            trade.exit_reason = "reconciliation_ibkr_data"
                            trade.profit_loss = calc_pnl(trade.entry_premium, exit_price, trade.contracts)
                            trade.profit_pct = calc_pnl_pct(trade.profit_loss, trade.entry_premium, trade.contracts)

                            pl_color = "green" if trade.profit_loss >= 0 else "red"
                            pl_text = f"${trade.profit_loss:,.2f}" if trade.profit_loss >= 0 else f"-${abs(trade.profit_loss):,.2f}"
                            console.print(
                                f"  [{pl_color}]✓ Closed: {trade.symbol} ${trade.strike} "
                                f"exp {trade.expiration} - Exit: ${exit_price:.2f}, P/L: {pl_text}[/{pl_color}]"
                            )
                            closed_count += 1
                        else:
                            # No exit order found - needs manual review
                            needs_manual_review.append(trade)
                            console.print(
                                f"  [yellow]⚠ {trade.symbol} ${trade.strike} - No exit order found in IBKR[/yellow]"
                            )

                    session.commit()

                    if closed_count > 0:
                        console.print(f"\n  [green]✓ Closed {closed_count} positions with actual exit data[/green]")

                    if needs_manual_review:
                        console.print(f"\n  [yellow]⚠️  {len(needs_manual_review)} positions need manual review:[/yellow]")
                        console.print("[yellow]   These were closed in IBKR but exit orders not found.[/yellow]")
                        console.print("[yellow]   Options:[/yellow]")
                        console.print("[yellow]   1. Run: nakedtrader sync --include-filled[/yellow]")
                        console.print("[yellow]   2. Manually update exit prices in database[/yellow]")
                        console.print("[yellow]   3. Check TWS/IBKR for actual exit prices[/yellow]\n")

                except Exception as e:
                    console.print(f"  [red]✗ Error fetching IBKR exit data: {e}[/red]")
                    console.print(f"  [yellow]Please run: nakedtrader sync --include-filled[/yellow]\n")

            # Step 4: Fix quantity mismatches
            if report.quantity_mismatches:
                console.print(f"[cyan]Step 3:[/cyan] Fixing {len(report.quantity_mismatches)} quantity mismatches...\n")
                fixed_count = 0

                for mismatch in report.quantity_mismatches:
                    # mismatch is a PositionMismatch object with contract_key, db_quantity, ibkr_quantity
                    contract_key = mismatch.contract_key
                    db_qty = mismatch.db_quantity
                    ibkr_qty = mismatch.ibkr_quantity

                    # Parse position key: SYMBOL_STRIKE_YYYYMMDD_P/C
                    parts = contract_key.split('_')
                    if len(parts) == 4:
                        symbol, strike, exp_str, right_char = parts

                        # Canonical key uses single-char P/C, but DB stores PUT/CALL
                        option_type_variants = [right_char]
                        if right_char == "P":
                            option_type_variants.append("PUT")
                        elif right_char == "C":
                            option_type_variants.append("CALL")

                        # Find trade in database
                        exp_date = datetime.strptime(exp_str, "%Y%m%d").date()
                        trade = session.query(Trade).filter(
                            Trade.symbol == symbol,
                            Trade.strike == float(strike),
                            Trade.expiration == exp_date,
                            Trade.option_type.in_(option_type_variants),
                            Trade.exit_date.is_(None)  # Only open trades
                        ).first()

                        if trade:
                            trade.contracts = ibkr_qty
                            console.print(
                                f"  [green]✓ Updated: {symbol} ${strike} "
                                f"from {db_qty} → {ibkr_qty} contracts[/green]"
                            )
                            fixed_count += 1

                session.commit()
                console.print(f"\n  [green]✓ Fixed {fixed_count} quantity mismatches[/green]\n")

            # Final summary
            console.print("=" * 60)
            console.print("[green]✓ RECONCILIATION COMPLETE - Database now matches IBKR![/green]")
            console.print("=" * 60 + "\n")

    finally:
        ibkr_client.disconnect()


@app.command(name="import")
def import_positions(
    dry_run: bool = typer.Option(
        True, "--dry-run/--live", help="Dry run (don't actually import)"
    ),
):
    """Import orphan positions from IBKR into database.

    This command finds positions that exist in IBKR but not in your database
    and imports them as Trade records. This is safer than sync-orders for
    positions without order history.

    Example:
        nakedtrader import --dry-run
        nakedtrader import --live
    """
    console.print("\n[bold cyan]Import Orphan Positions[/bold cyan]\n")

    # Setup
    setup_logging()
    config = get_config()
    init_database()

    # Connect to IBKR
    try:
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            progress.add_task("Connecting to IBKR...", total=None)

            ibkr_config = config.ibkr
            ibkr_client = IBKRClient(ibkr_config, suppress_errors=True)
            ibkr_client.connect()

    except IBKRConnectionError as e:
        console.print(f"[red]✗ Connection failed: {e}[/red]")
        raise typer.Exit(code=1)

    try:
        # Get trade repository
        with get_db_session() as session:
            trade_repo = TradeRepository(session)

            # Create reconciliation service
            from src.services.order_reconciliation import OrderReconciliation
            import asyncio

            reconciler = OrderReconciliation(ibkr_client, trade_repo)

            # Import orphan positions
            mode_text = "[yellow][DRY RUN][/yellow]" if dry_run else "[green][LIVE MODE][/green]"
            console.print(f"{mode_text} Fetching and importing orphan positions...\n")

            imported_count = asyncio.run(reconciler.import_orphan_positions(dry_run=dry_run))

            if dry_run:
                console.print(f"\n[yellow]✓ Would import {imported_count} orphan positions[/yellow]")
                console.print("  Run with --live to actually import them\n")
            else:
                if imported_count > 0:
                    session.commit()  # Commit the imported trades
                    console.print(f"\n[green]✓ Successfully imported {imported_count} orphan positions[/green]\n")
                else:
                    console.print("\n[yellow]⚠ No orphan positions found to import[/yellow]\n")

    finally:
        ibkr_client.disconnect()


@app.command(name="trades")
def list_trades(
    open_only: bool = typer.Option(False, "--open-only", help="Show only open trades"),
    closed_only: bool = typer.Option(False, "--closed-only", help="Show only closed trades"),
    days: int = typer.Option(None, "--days", "-d", help="Show trades from last N days"),
    symbol: str = typer.Option(None, "--symbol", "-s", help="Filter by symbol"),
    limit: int = typer.Option(50, "--limit", "-l", help="Maximum number of trades to show"),
):
    """List trades from the database.

    Example:
        nakedtrader trades
        nakedtrader trades --open-only
        nakedtrader trades --days 7
        nakedtrader trades --symbol AAPL
    """
    console.print("\n[bold cyan]Trading History[/bold cyan]\n")

    # Setup
    setup_logging()
    init_database()

    try:
        with get_db_session() as session:
            trade_repo = TradeRepository(session)

            # Get trades based on filters
            if open_only:
                trades = trade_repo.get_open_trades()
            elif closed_only:
                trades = trade_repo.get_closed_trades(limit=limit)
            elif days:
                trades = trade_repo.get_recent_trades(days=days)
            else:
                trades = trade_repo.get_all(limit=limit)

            # Filter by symbol if specified
            if symbol:
                trades = [t for t in trades if t.symbol == symbol.upper()]

            if not trades:
                console.print("[yellow]No trades found matching criteria[/yellow]\n")
                return

            # Create table
            table = Table(title=f"Trades ({len(trades)} total)")
            table.add_column("Symbol", style="cyan")
            table.add_column("Strike", justify="right")
            table.add_column("Expiration")
            table.add_column("Contracts", justify="right")
            table.add_column("Entry Premium", justify="right")
            table.add_column("Entry Date")
            table.add_column("Status", justify="center")
            table.add_column("P/L", justify="right")

            for trade in trades:
                # Format status
                if trade.exit_date:
                    status = "[green]Closed[/green]" if trade.profit_loss and trade.profit_loss > 0 else "[red]Closed[/red]"
                else:
                    status = "[yellow]Open[/yellow]"

                # Format P/L
                if trade.profit_loss is not None:
                    pl_color = "green" if trade.profit_loss > 0 else "red"
                    pl_text = f"[{pl_color}]${trade.profit_loss:,.2f}[/{pl_color}]"
                else:
                    pl_text = "-"

                # Format entry date
                entry_date_str = trade.entry_date.strftime("%Y-%m-%d") if trade.entry_date else "-"

                table.add_row(
                    trade.symbol,
                    f"${trade.strike:.2f}",
                    str(trade.expiration),
                    str(trade.contracts),
                    f"${trade.entry_premium:.2f}",
                    entry_date_str,
                    status,
                    pl_text,
                )

            console.print(table)

            # Summary statistics
            open_count = len([t for t in trades if not t.exit_date])
            closed_count = len([t for t in trades if t.exit_date])
            total_pl = sum(t.profit_loss for t in trades if t.profit_loss is not None)

            console.print(f"\n[bold]Summary:[/bold]")
            console.print(f"  Open positions: [yellow]{open_count}[/yellow]")
            console.print(f"  Closed trades: [cyan]{closed_count}[/cyan]")
            if closed_count > 0:
                pl_color = "green" if total_pl > 0 else "red"
                console.print(f"  Total P/L: [{pl_color}]${total_pl:,.2f}[/{pl_color}]")
            console.print()

    except Exception as e:
        from rich.markup import escape
        console.print(f"[red]✗ Error: {escape(str(e))}[/red]")
        raise typer.Exit(code=1)


def _display_reconciliation_report(report, console):
    """Display reconciliation report in rich table format."""
    console.print(f"\n[bold]Order Sync Report - {report.date}[/bold]\n")

    # Summary
    console.print(f"Total synced: [cyan]{len(report.reconciled)}[/cyan]")
    console.print(
        f"Discrepancies found: [yellow]{report.total_discrepancies}[/yellow]"
    )
    console.print(f"Discrepancies resolved: [green]{report.total_resolved}[/green]")
    console.print(f"Orphan orders (in TWS, not DB): [red]{len(report.orphans)}[/red]")
    if report.missing_in_tws:
        console.print(
            f"Not in TWS (no order history, no position): "
            f"[yellow]{len(report.missing_in_tws)}[/yellow]"
        )
    console.print()

    if report.reconciled:
        table = Table(title="Reconciled Orders")
        table.add_column("Symbol")
        table.add_column("Order ID")
        table.add_column("DB Status")
        table.add_column("TWS Status")
        table.add_column("Fill Price")
        table.add_column("Commission")
        table.add_column("Discrepancy")

        for item in report.reconciled:
            discrepancy_text = "✓ Match"
            if item.discrepancy:
                discrepancy_text = f"[yellow]{item.discrepancy.type}[/yellow]"

            table.add_row(
                item.symbol,
                str(item.order_id),
                item.db_status,
                item.tws_status,
                f"${item.fill_price:.2f}" if item.fill_price else "-",
                f"${item.commission:.2f}" if item.commission else "-",
                discrepancy_text,
            )

        console.print(table)

    if report.orphans:
        console.print(f"\n[yellow]⚠ Orphan Orders (in TWS, not in database):[/yellow]")
        for orphan in report.orphans:
            console.print(
                f"  - Order {orphan.order.orderId}: "
                f"{orphan.contract.symbol if hasattr(orphan, 'contract') else 'Unknown'}"
            )

    if report.missing_in_tws:
        console.print(f"\n[yellow]⚠ Not in TWS (no order history and no matching position):[/yellow]")
        for missing in report.missing_in_tws:
            exp_str = ""
            if hasattr(missing, 'expiration') and missing.expiration:
                try:
                    from datetime import date as _date, datetime as _dt
                    exp = missing.expiration
                    if isinstance(exp, str):
                        exp = _date.fromisoformat(exp)
                    elif isinstance(exp, _dt):
                        exp = exp.date()
                    exp_str = f" {exp.strftime('%b%d')}'{exp.strftime('%y')}"
                except Exception:
                    exp_str = f" {missing.expiration}"
            strike_str = f" {missing.strike}" if hasattr(missing, 'strike') and missing.strike else ""
            opt_type = f" {missing.option_type[0]}" if hasattr(missing, 'option_type') and missing.option_type else ""
            sym = missing.symbol if hasattr(missing, 'symbol') else 'Unknown'
            console.print(
                f"  - Order {missing.order_id}: {sym}{exp_str}{strike_str}{opt_type}"
            )

    console.print(f"\n[green]✓ Sync complete[/green]\n")


def _display_position_reconciliation(report, console):
    """Display position reconciliation report."""
    console.print(f"\n[bold]Position Reconciliation Report[/bold]\n")

    if not report.has_discrepancies:
        console.print("[green]✓ All positions match! No discrepancies found.[/green]\n")
        return

    console.print("[yellow]⚠ Discrepancies detected:[/yellow]\n")

    if report.quantity_mismatches:
        table = Table(title="Quantity Mismatches")
        table.add_column("Contract")
        table.add_column("DB Quantity")
        table.add_column("IBKR Quantity")
        table.add_column("Difference")

        for mismatch in report.quantity_mismatches:
            diff = int(mismatch.difference) if isinstance(mismatch.difference, (int, float)) else mismatch.difference
            table.add_row(
                mismatch.contract_key,
                str(mismatch.db_quantity),
                str(mismatch.ibkr_quantity),
                f"[{'green' if diff > 0 else 'red'}]{diff:+d}[/]",
            )

        console.print(table)

    if report.in_ibkr_not_db:
        console.print(f"\n[yellow]In IBKR but not in database:[/yellow]")
        for contract_key, _ in report.in_ibkr_not_db:
            console.print(f"  - {contract_key}")

    if report.in_db_not_ibkr:
        console.print(f"\n[red]In database but not in IBKR:[/red]")
        for contract_key, _ in report.in_db_not_ibkr:
            console.print(f"  - {contract_key}")

    if report.assignments:
        console.print(f"\n[bold red]Assignments Detected ({len(report.assignments)}):[/bold red]")
        assign_table = Table(title="Detected Assignments")
        assign_table.add_column("Symbol", style="cyan")
        assign_table.add_column("Shares", justify="right")
        assign_table.add_column("Avg Cost", justify="right")
        assign_table.add_column("Matched Trade")
        assign_table.add_column("Strike", justify="right")
        assign_table.add_column("Expiration")

        for event in report.assignments:
            assign_table.add_row(
                event.symbol,
                str(event.shares),
                f"${event.avg_cost:.2f}",
                event.matched_trade_id or "—",
                f"${event.matched_strike:.2f}" if event.matched_strike else "—",
                event.matched_expiration or "—",
            )
        console.print(assign_table)

    console.print("\n")


# ============================================================================
# TAAD Commands (Trade Archaeology & Alpha Discovery)
# ============================================================================


@app.command(name="taad-import")
def taad_import(
    account: str = typer.Option(
        None, "--account", "-a",
        help="IBKR account ID (e.g., YOUR_ACCOUNT).",
    ),
    xml_file: str = typer.Option(
        None, "--xml-file", "-f",
        help="Import from a local XML file instead of calling Flex Query API.",
    ),
    no_match: bool = typer.Option(
        False, "--no-match",
        help="Skip trade matching after import.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Parse and display records without saving to database.",
    ),
    query: str = typer.Option(
        "daily", "--query", "-q",
        help="Query type: daily, last_month, last_quarter, last_year.",
    ),
) -> None:
    """Import trades from IBKR Flex Query into the TAAD database."""
    from src.cli.commands.taad_commands import run_taad_import

    run_taad_import(account=account, xml_file=xml_file, no_match=no_match, dry_run=dry_run, query=query)


@app.command(name="taad-status")
def taad_status(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Filter by account ID.",
    ),
    limit: int = typer.Option(
        10, "--limit", "-n",
        help="Number of recent sessions to show.",
    ),
) -> None:
    """Show recent TAAD import sessions and statistics."""
    from src.cli.commands.taad_commands import run_taad_status

    run_taad_status(account=account, limit=limit)


@app.command(name="taad-report")
def taad_report(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Filter by account ID.",
    ),
    symbol: str = typer.Option(
        None, "--symbol", "-s",
        help="Filter by underlying symbol (e.g., AAPL).",
    ),
    show_unmatched: bool = typer.Option(
        False, "--unmatched", "-u",
        help="Show unmatched records.",
    ),
    show_raw: bool = typer.Option(
        False, "--raw",
        help="Show all raw import records.",
    ),
    sort_by: str = typer.Option(
        "date", "--sort",
        help="Sort by: date, symbol, pnl.",
    ),
) -> None:
    """Display matched trade lifecycles with P&L for verification."""
    from src.cli.commands.taad_commands import run_taad_report

    run_taad_report(
        account=account, symbol=symbol,
        show_unmatched=show_unmatched, show_raw=show_raw, sort_by=sort_by,
    )


@app.command(name="taad-gaps")
def taad_gaps(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Filter by account ID.",
    ),
) -> None:
    """Identify gaps and issues in imported TAAD data."""
    from src.cli.commands.taad_commands import run_taad_gaps

    run_taad_gaps(account=account)


@app.command(name="taad-enrich")
def taad_enrich(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Filter by account ID.",
    ),
    symbol: str = typer.Option(
        None, "--symbol", "-s",
        help="Filter by underlying symbol (e.g., AAPL).",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Re-enrich already-enriched trades.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would be enriched without making changes.",
    ),
    with_ibkr: bool = typer.Option(
        False, "--with-ibkr",
        help="Also use IBKR historical data (requires TWS connection).",
    ),
    with_scrape: bool = typer.Option(
        False, "--with-scrape",
        help="Include Barchart Premier scraping for option data (2023+, slower).",
    ),
    limit: int = typer.Option(
        0, "--limit", "-n",
        help="Max trades to enrich (0 = all).",
    ),
) -> None:
    """Enrich historical trades with market context, technicals, and B-S IV."""
    from src.cli.commands.taad_commands import run_taad_enrich

    run_taad_enrich(
        account=account, symbol=symbol, force=force,
        dry_run=dry_run, with_ibkr=with_ibkr, with_scrape=with_scrape, limit=limit,
    )


@app.command(name="taad-promote")
def taad_promote(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Filter by IBKR account ID.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would be promoted without making changes.",
    ),
) -> None:
    """Promote matched trade lifecycles into public.trades for enrichment.

    Converts TradeMatchingLog rows (matched STO+BTC pairs from taad-import)
    into Trade records with trade_source='ibkr_import'. Idempotent — safe
    to re-run; already-promoted matches are skipped.

    After promotion, run `taad-enrich` to populate entry/exit snapshots.
    """
    from src.cli.commands.taad_commands import run_taad_promote

    run_taad_promote(account=account, dry_run=dry_run)


# ============================================================================
# Data Maintenance Commands
# ============================================================================


@app.command(name="backfill-sectors")
def backfill_sectors(
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would be updated without making changes.",
    ),
) -> None:
    """Backfill missing sector labels on existing trades.

    Queries all trades where sector IS NULL, looks up sectors via the
    static map + yfinance fallback, and batch-updates Trade.sector and
    TradeEntrySnapshot.sector.

    Examples:
        nakedtrader backfill-sectors --dry-run
        nakedtrader backfill-sectors
    """
    from src.data.models import Trade, TradeEntrySnapshot
    from src.data.sector_map import get_sector

    console.print("[bold blue]Backfill Missing Sectors[/bold blue]\n")

    with get_db_session() as session:
        # Find distinct symbols with NULL sector on trades
        null_sector_trades = (
            session.query(Trade)
            .filter(Trade.sector.is_(None))
            .all()
        )

        if not null_sector_trades:
            console.print("[green]All trades already have sector labels.[/green]")
            return

        # Group by symbol
        symbols: dict[str, list[Trade]] = {}
        for trade in null_sector_trades:
            symbols.setdefault(trade.symbol, []).append(trade)

        console.print(f"Found [bold]{len(null_sector_trades)}[/bold] trades across "
                       f"[bold]{len(symbols)}[/bold] symbols with missing sectors.\n")

        resolved = 0
        unresolved = 0
        trades_updated = 0

        for symbol in sorted(symbols.keys()):
            sector = get_sector(symbol)
            trade_count = len(symbols[symbol])

            if sector == "Unknown":
                console.print(f"  [dim]{symbol}: Unknown (skipped, {trade_count} trades)[/dim]")
                unresolved += 1
                continue

            console.print(f"  {symbol}: [cyan]{sector}[/cyan] ({trade_count} trades)")
            resolved += 1

            if not dry_run:
                for trade in symbols[symbol]:
                    trade.sector = sector

                # Also update matching TradeEntrySnapshots
                snapshots = (
                    session.query(TradeEntrySnapshot)
                    .filter(
                        TradeEntrySnapshot.trade_id.in_(
                            [t.id for t in symbols[symbol]]
                        ),
                        TradeEntrySnapshot.sector.is_(None),
                    )
                    .all()
                )
                for snap in snapshots:
                    snap.sector = sector

                trades_updated += trade_count

        console.print()
        if dry_run:
            console.print(f"[yellow]DRY RUN:[/yellow] Would resolve {resolved} symbols, "
                           f"update ~{sum(len(symbols[s]) for s in sorted(symbols) if get_sector(s) != 'Unknown')} trades. "
                           f"{unresolved} symbols unresolved.")
        else:
            session.commit()
            console.print(f"[green]Updated {trades_updated} trades across {resolved} symbols.[/green]")
            if unresolved:
                console.print(f"[dim]{unresolved} symbols could not be resolved.[/dim]")


# ============================================================================
# NakedTrader Commands
# ============================================================================


@app.command(name="sell")
def nakedtrader_trade(
    symbol: str = typer.Argument(
        ...,
        help="Underlying symbol: XSP, SPX, SPY (US) or XJO, BHP, CBA, etc. (ASX)",
    ),
    contracts: Optional[int] = typer.Option(
        None,
        "--contracts", "-c",
        help="Number of contracts (overrides config)",
    ),
    config_path: str = typer.Option(
        "config/daily_spx_options.yaml",
        "--config",
        help="Path to YAML config file",
    ),
    exchange: Optional[str] = typer.Option(
        None,
        "--exchange", "-x",
        help="Override exchange: US or ASX (overrides YAML setting)",
    ),
    dry_run: bool = typer.Option(
        True,
        "--dry-run/--live",
        help="Dry run (default) or live paper trading",
    ),
    no_wait: bool = typer.Option(
        False,
        "--no-wait",
        help="Skip waiting for market open",
    ),
    stop_loss: Optional[bool] = typer.Option(
        None,
        "--stop-loss/--no-stop",
        help="Override stop-loss setting from config",
    ),
    delta: Optional[float] = typer.Option(
        None,
        "--delta", "-d",
        help="Override target delta (e.g. 0.08)",
    ),
    dte: Optional[int] = typer.Option(
        None,
        "--dte",
        help="Override maximum DTE",
    ),
    skip_confirm: bool = typer.Option(
        False,
        "--yes", "-y",
        help="Skip confirmation prompt",
    ),
) -> None:
    """Sell a naked put option.

    Mechanical delta-targeted put selling with bracket orders.
    Supports US (SPX/XSP/SPY) and ASX (XJO/BHP/CBA) exchanges.

    Examples:
        nakedtrader sell XSP --dry-run
        nakedtrader sell XSP --contracts 2 --live --yes
        nakedtrader sell SPX --delta 0.08 --dry-run
        nakedtrader sell XJO --exchange ASX --config config/daily_asx_options.yaml --dry-run
        nakedtrader sell BHP --exchange ASX --dry-run
    """
    from src.cli.commands.nakedtrader_commands import run_nt

    setup_logging()
    config = get_config()
    client = connect_to_ibkr_with_error_handling(config, console, client_id_override=5)

    try:
        success = run_nt(
            symbol=symbol.upper(),
            client=client,
            console=console,
            config_path=config_path,
            contracts=contracts,
            dry_run=dry_run,
            no_wait=no_wait,
            stop_loss=stop_loss,
            delta=delta,
            dte=dte,
            skip_confirm=skip_confirm,
            exchange=exchange,
        )
        if not success:
            raise typer.Exit(1)
    except Exception as e:
        if not isinstance(e, (typer.Exit, SystemExit)):
            logger.error(f"NakedTrader error: {e}", exc_info=True)
            console.print(f"\n[bold red]Error: {e}[/bold red]")
            raise typer.Exit(1)
        raise
    finally:
        client.disconnect()


@app.command(name="sell-watch")
def nakedtrader_watch(
    interval: Optional[int] = typer.Option(
        None,
        "--interval", "-i",
        help="Refresh interval in seconds (overrides config)",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Check once and exit (don't loop)",
    ),
    wait: bool = typer.Option(
        False,
        "--wait", "-w",
        help="Wait for market open instead of exiting when closed",
    ),
    config_path: str = typer.Option(
        "config/daily_spx_options.yaml",
        "--config",
        help="Path to YAML config file",
    ),
) -> None:
    """Monitor open naked put positions.

    Shows live P&L, Greeks, and bracket order status for all open
    positions. Refreshes periodically until Ctrl+C.

    By default, exits when the market is closed. Use --wait to wait
    for market open instead.

    Examples:
        nakedtrader sell-watch --once
        nakedtrader sell-watch --interval 60
        nakedtrader sell-watch --wait
    """
    from src.cli.commands.nakedtrader_commands import run_nt_watch

    setup_logging()
    config = get_config()
    client = connect_to_ibkr_with_error_handling(
        config, console, client_id_override=4
    )

    try:
        run_nt_watch(
            client=client,
            console=console,
            config_path=config_path,
            interval=interval,
            once=once,
            wait=wait,
        )
    except Exception as e:
        if not isinstance(e, (typer.Exit, SystemExit, KeyboardInterrupt)):
            logger.error(f"NakedTrader watch error: {e}", exc_info=True)
            console.print(f"\n[bold red]Error: {e}[/bold red]")
            raise typer.Exit(1)
        raise
    finally:
        client.disconnect()


@app.command(name="sell-status")
def nakedtrader_status(
    history: int = typer.Option(
        20,
        "--history", "-n",
        help="Number of recent trades to show",
    ),
) -> None:
    """Show naked put trade history and performance.

    Offline command - no IBKR connection required. Shows win rate,
    total P&L, average premium, and recent trade history.

    Examples:
        nakedtrader sell-status
        nakedtrader sell-status --history 50
    """
    from src.cli.commands.nakedtrader_commands import run_nt_status

    setup_logging()
    run_nt_status(console=console, history=history)


@app.command(name="stocks")
def nakedtrader_stocks(
    history: int = typer.Option(
        20,
        "--history", "-n",
        help="Number of positions to show",
    ),
) -> None:
    """Show stock positions from option assignments.

    Offline command - no IBKR connection required. Shows open and closed
    stock positions with combined option + stock P&L.

    Examples:
        nakedtrader stocks
        nakedtrader stocks --history 50
    """
    from src.cli.commands.nakedtrader_commands import run_nt_stocks

    setup_logging()
    run_nt_stocks(console=console, history=history)


# ============================================================================
# Main Entry Point
# ============================================================================


def main() -> None:
    """Main entry point."""
    # Add the project root to Python path
    project_root = Path(__file__).parent.parent.parent
    sys.path.insert(0, str(project_root))

    app()


if __name__ == "__main__":
    main()
