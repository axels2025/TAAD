"""CLI commands for TAAD (Trade Archaeology & Alpha Discovery).

Provides:
- taad-import: Import trades from IBKR Flex Query
- taad-status: Show import session status
- taad-report: Inspect matched trade lifecycles with P&L
- taad-gaps: Identify gaps in imported data
- taad-enrich: Enrich historical trades with market context
"""

from datetime import date, datetime
from typing import Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text

from src.data.database import get_db_session
from src.utils.timezone import us_trading_date
from src.taad.importer import ImportResult, run_import
from src.taad.models import IBKRRawImport, ImportSession, TradeMatchingLog

console = Console()


def run_taad_import(
    account: str = typer.Option(
        None,
        "--account",
        "-a",
        help="IBKR account ID (e.g., YOUR_ACCOUNT). Uses first configured if omitted.",
    ),
    xml_file: str = typer.Option(
        None,
        "--xml-file",
        "-f",
        help="Import from a local XML file instead of calling the Flex Query API.",
    ),
    no_match: bool = typer.Option(
        False,
        "--no-match",
        help="Skip trade matching after import.",
    ),
    dry_run: bool = typer.Option(
        False,
        "--dry-run",
        help="Parse and display records without saving to database.",
    ),
    query: str = typer.Option(
        "daily",
        "--query",
        "-q",
        help="Query type: daily (trade confirmations), last_month, last_quarter, last_year (activity queries).",
    ),
) -> None:
    """Import trades from IBKR Flex Query into the TAAD database.

    Fetches the Flex Query report, parses EXECUTION-level records,
    stores them as raw imports, and matches STO/BTC pairs.

    Use --query to select the query type:
      daily        Trade Confirmation (today's trades, default)
      last_month   Activity Flex Query (~30 days)
      last_quarter Activity Flex Query (~90 days)
      last_year    Activity Flex Query (~365 days)
    """
    from src.config.base import get_config

    valid_queries = ("daily", "last_month", "last_quarter", "last_year")
    if query not in valid_queries:
        console.print(f"[red]Invalid query type: {query!r}[/red]")
        console.print(f"Valid options: {', '.join(valid_queries)}")
        raise typer.Exit(1)

    config = get_config()

    # Resolve account
    if not account:
        accounts = config.list_flex_accounts()
        if not accounts:
            console.print(
                "[red]No Flex Query accounts configured in .env[/red]\n"
                "Set IBKR_FLEX_TOKEN_1, IBKR_FLEX_QUERY_ID_1, IBKR_FLEX_ACCOUNT_1"
            )
            raise typer.Exit(1)
        account = accounts[0]
        console.print(f"Using first configured account: [cyan]{account}[/cyan]")

    creds = config.get_flex_credentials_for_query(query, account)
    if not creds and not xml_file:
        console.print(
            f"[red]No Flex Query credentials for query={query!r}, account={account}[/red]"
        )
        raise typer.Exit(1)

    query_label = "Trade Confirmation" if query == "daily" else f"Activity ({query})"
    console.print(f"\n[bold]TAAD Import — Account {account} — {query_label}[/bold]")
    console.print("─" * 50)

    # Load XML from file if specified
    xml_text = None
    if xml_file:
        console.print(f"Reading XML from: {xml_file}")
        with open(xml_file) as f:
            xml_text = f.read()
        console.print(f"  Loaded {len(xml_text):,} bytes")

    if dry_run:
        # Parse only, don't persist
        from src.taad.flex_parser import parse_flex_xml
        from src.taad.flex_query_client import FlexQueryClient

        if xml_text is None:
            console.print("Fetching Flex Query report...")
            client = FlexQueryClient(token=creds["token"], query_id=creds["query_id"])
            xml_text = client.fetch_report()

        executions = parse_flex_xml(xml_text)
        console.print(f"\n[green]Parsed {len(executions)} EXECUTION records[/green]\n")

        _display_executions_table(executions)
        return

    # Full import
    with get_db_session() as session:
        try:
            result = run_import(
                session=session,
                account_id=account,
                xml_text=xml_text,
                run_matching=not no_match,
                query=query,
            )
            session.commit()
        except Exception as e:
            console.print(f"\n[red]Import failed: {e}[/red]")
            raise typer.Exit(1) from e

    # Display results
    _display_import_result(result)


def run_taad_status(
    account: str = typer.Option(
        None,
        "--account",
        "-a",
        help="Filter by account ID.",
    ),
    limit: int = typer.Option(
        10,
        "--limit",
        "-n",
        help="Number of recent sessions to show.",
    ),
) -> None:
    """Show recent TAAD import sessions and statistics."""
    with get_db_session() as session:
        query = session.query(ImportSession).order_by(ImportSession.id.desc())
        if account:
            query = query.filter(ImportSession.account_id == account)
        sessions = query.limit(limit).all()

        if not sessions:
            console.print("[yellow]No import sessions found[/yellow]")
            return

        table = Table(title="Recent TAAD Import Sessions")
        table.add_column("ID", style="dim")
        table.add_column("Account")
        table.add_column("Status")
        table.add_column("Source")
        table.add_column("Date Range")
        table.add_column("Total", justify="right")
        table.add_column("Imported", justify="right")
        table.add_column("Dupes", justify="right")
        table.add_column("Errors", justify="right")
        table.add_column("Started")

        for s in sessions:
            status_style = {
                "completed": "green",
                "running": "yellow",
                "failed": "red",
            }.get(s.status, "white")

            date_range = ""
            if s.date_range_start and s.date_range_end:
                date_range = f"{s.date_range_start} → {s.date_range_end}"

            table.add_row(
                str(s.id),
                s.account_id or "",
                f"[{status_style}]{s.status}[/{status_style}]",
                s.source_type or "",
                date_range,
                str(s.total_records or 0),
                str(s.imported_records or 0),
                str(s.skipped_duplicates or 0),
                str(s.error_count or 0),
                s.started_at.strftime("%Y-%m-%d %H:%M") if s.started_at else "",
            )

        console.print(table)

        # Summary stats
        total_raw = session.query(IBKRRawImport).count()
        total_matched = session.query(IBKRRawImport).filter(
            IBKRRawImport.matched == True  # noqa: E712
        ).count()
        total_matches = session.query(TradeMatchingLog).count()

        console.print(f"\n[bold]Database totals:[/bold]")
        console.print(f"  Raw imports: {total_raw:,}")
        console.print(f"  Matched imports: {total_matched:,}")
        console.print(f"  Trade matches: {total_matches:,}")


def _display_import_result(result: ImportResult) -> None:
    """Display import result summary."""
    console.print(f"\n[bold green]Import Complete[/bold green]")
    console.print("─" * 40)
    console.print(f"  Session ID:         {result.session_id}")
    console.print(f"  XML records parsed: {result.total_xml_records}")
    console.print(f"  Imported:           [green]{result.imported}[/green]")
    console.print(f"  Skipped (dupes):    [yellow]{result.skipped_duplicates}[/yellow]")
    console.print(f"  Errors:             [red]{result.errors}[/red]")
    console.print(f"  Trades matched:     [cyan]{result.matched_trades}[/cyan]")

    if result.error_messages:
        console.print(f"\n[red]Errors:[/red]")
        for msg in result.error_messages[:10]:
            console.print(f"  - {msg}")


def _display_executions_table(executions: list) -> None:
    """Display parsed executions in a table (for dry-run mode)."""
    table = Table(title=f"Parsed Executions ({len(executions)} records)")
    table.add_column("Date")
    table.add_column("Symbol")
    table.add_column("Strike", justify="right")
    table.add_column("P/C")
    table.add_column("Expiry")
    table.add_column("Action")
    table.add_column("Qty", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("ExecID", style="dim")

    for ex in executions[:50]:
        action = f"{ex.buy_sell}/{ex.open_close}" if ex.open_close else ex.buy_sell
        action_style = "green" if ex.open_close == "O" and ex.buy_sell == "SELL" else "red"

        table.add_row(
            str(ex.trade_date),
            ex.underlying_symbol,
            f"{ex.strike:.2f}" if ex.strike else "",
            ex.put_call,
            str(ex.expiry) if ex.expiry else "",
            f"[{action_style}]{action}[/{action_style}]",
            str(ex.quantity),
            f"{ex.price:.4f}",
            ex.exec_id[:20] if ex.exec_id else "",
        )

    console.print(table)
    if len(executions) > 50:
        console.print(f"\n  ... and {len(executions) - 50} more records")


def run_taad_report(
    account: str = typer.Option(
        None,
        "--account",
        "-a",
        help="Filter by account ID.",
    ),
    symbol: str = typer.Option(
        None,
        "--symbol",
        "-s",
        help="Filter by underlying symbol (e.g., AAPL).",
    ),
    show_unmatched: bool = typer.Option(
        False,
        "--unmatched",
        "-u",
        help="Show unmatched records.",
    ),
    show_raw: bool = typer.Option(
        False,
        "--raw",
        help="Show all raw import records.",
    ),
    sort_by: str = typer.Option(
        "date",
        "--sort",
        help="Sort by: date, symbol, pnl.",
    ),
) -> None:
    """Display matched trade lifecycles with P&L for verification.

    Shows entry/exit details for each matched trade so you can
    cross-check against your IBKR statements.
    """
    from sqlalchemy import func as sa_func
    from sqlalchemy.orm import aliased

    with get_db_session() as session:
        # --- Matched Trades Report ---
        OpenImport = aliased(IBKRRawImport, name="open_imp")
        CloseImport = aliased(IBKRRawImport, name="close_imp")

        query = (
            session.query(TradeMatchingLog, OpenImport, CloseImport)
            .join(OpenImport, TradeMatchingLog.raw_import_id_open == OpenImport.id)
            .outerjoin(CloseImport, TradeMatchingLog.raw_import_id_close == CloseImport.id)
        )

        if account:
            query = query.filter(OpenImport.account_id == account)
        if symbol:
            query = query.filter(OpenImport.underlying_symbol == symbol.upper())

        rows = query.order_by(OpenImport.trade_date, OpenImport.underlying_symbol).all()

        if not rows and not show_unmatched and not show_raw:
            console.print("[yellow]No matched trades found[/yellow]")
            return

        if rows:
            _display_matched_trades(rows, sort_by)

        # --- Unmatched Records ---
        if show_unmatched:
            _display_unmatched_records(session, account, symbol)

        # --- Raw Imports ---
        if show_raw:
            _display_raw_imports(session, account, symbol)


def _display_matched_trades(rows: list, sort_by: str) -> None:
    """Display matched trade lifecycles grouped by symbol with P&L."""
    # Build trade data
    trades = []
    for match_log, open_imp, close_imp in rows:
        entry_premium = abs(open_imp.price)
        entry_qty = abs(open_imp.quantity)
        exit_premium = abs(close_imp.price) if close_imp else 0.0
        multiplier = open_imp.multiplier or 100

        # P&L: premium received (STO) minus premium paid (BTC)
        gross_pnl = (entry_premium - exit_premium) * entry_qty * multiplier

        # Include commissions if available
        entry_commission = abs(open_imp.commission) if open_imp.commission else 0.0
        exit_commission = abs(close_imp.commission) if close_imp and close_imp.commission else 0.0
        net_pnl = gross_pnl - entry_commission - exit_commission

        exit_type = match_log.match_type.split("+")[1] if "+" in match_log.match_type else match_log.match_type

        # Days held
        exit_date = close_imp.trade_date if close_imp else open_imp.expiry
        days_held = (exit_date - open_imp.trade_date).days if exit_date else None

        trades.append({
            "symbol": open_imp.underlying_symbol,
            "strike": open_imp.strike,
            "put_call": open_imp.put_call,
            "expiry": open_imp.expiry,
            "entry_date": open_imp.trade_date,
            "exit_date": exit_date,
            "entry_qty": entry_qty,
            "entry_premium": entry_premium,
            "exit_premium": exit_premium,
            "exit_type": exit_type,
            "gross_pnl": gross_pnl,
            "net_pnl": net_pnl,
            "commission": entry_commission + exit_commission,
            "days_held": days_held,
            "confidence": match_log.confidence_score,
        })

    # Sort
    if sort_by == "pnl":
        trades.sort(key=lambda t: t["net_pnl"])
    elif sort_by == "symbol":
        trades.sort(key=lambda t: (t["symbol"], t["entry_date"]))
    # default is already date order from the query

    # Summary stats
    total_gross = sum(t["gross_pnl"] for t in trades)
    total_net = sum(t["net_pnl"] for t in trades)
    total_commission = sum(t["commission"] for t in trades)
    winners = sum(1 for t in trades if t["net_pnl"] > 0)
    losers = sum(1 for t in trades if t["net_pnl"] < 0)
    breakeven = sum(1 for t in trades if t["net_pnl"] == 0)

    # Build table
    table = Table(title=f"Matched Trade Lifecycles ({len(trades)} trades)")
    table.add_column("Entry Date", style="dim")
    table.add_column("Symbol")
    table.add_column("Strike", justify="right")
    table.add_column("P/C")
    table.add_column("Expiry")
    table.add_column("Qty", justify="right")
    table.add_column("Entry $", justify="right")
    table.add_column("Exit $", justify="right")
    table.add_column("Exit Type")
    table.add_column("Days", justify="right")
    table.add_column("Gross P&L", justify="right")
    table.add_column("Net P&L", justify="right")

    # Group by symbol for subtotals
    current_symbol = None
    symbol_pnl = 0.0
    symbol_count = 0

    for t in trades:
        if sort_by == "symbol" and t["symbol"] != current_symbol:
            if current_symbol is not None:
                _add_symbol_subtotal(table, current_symbol, symbol_count, symbol_pnl)
            current_symbol = t["symbol"]
            symbol_pnl = 0.0
            symbol_count = 0

        pnl_style = "green" if t["net_pnl"] > 0 else "red" if t["net_pnl"] < 0 else "white"
        exit_style = "dim green" if t["exit_type"] == "expiration" else "yellow"

        table.add_row(
            str(t["entry_date"]),
            t["symbol"],
            f"{t['strike']:.1f}",
            t["put_call"] or "",
            str(t["expiry"]) if t["expiry"] else "",
            str(t["entry_qty"]),
            f"{t['entry_premium']:.4f}",
            f"{t['exit_premium']:.4f}" if t["exit_type"] != "expiration" else "—",
            f"[{exit_style}]{t['exit_type']}[/{exit_style}]",
            str(t["days_held"]) if t["days_held"] is not None else "",
            f"[{pnl_style}]{t['gross_pnl']:>+.2f}[/{pnl_style}]",
            f"[{pnl_style}]{t['net_pnl']:>+.2f}[/{pnl_style}]",
        )

        if sort_by == "symbol":
            symbol_pnl += t["net_pnl"]
            symbol_count += 1

    # Final symbol subtotal
    if sort_by == "symbol" and current_symbol is not None:
        _add_symbol_subtotal(table, current_symbol, symbol_count, symbol_pnl)

    console.print(table)

    # Summary panel
    avg_days = sum(t["days_held"] for t in trades if t["days_held"] is not None) / max(
        sum(1 for t in trades if t["days_held"] is not None), 1
    )
    win_rate = winners / len(trades) * 100 if trades else 0

    net_style = "green" if total_net >= 0 else "red"
    summary = (
        f"  Total trades:   {len(trades)}\n"
        f"  Winners:        [green]{winners}[/green]  |  Losers: [red]{losers}[/red]  |  Breakeven: {breakeven}\n"
        f"  Win rate:       {win_rate:.1f}%\n"
        f"  Avg days held:  {avg_days:.1f}\n"
        f"  Gross P&L:      [{net_style}]${total_gross:>+,.2f}[/{net_style}]\n"
        f"  Commissions:    [red]-${total_commission:,.2f}[/red]\n"
        f"  Net P&L:        [{net_style}]${total_net:>+,.2f}[/{net_style}]"
    )
    console.print(Panel(summary, title="Summary", border_style="blue"))

    # Per-symbol breakdown
    symbols = sorted(set(t["symbol"] for t in trades))
    if len(symbols) > 1:
        sym_table = Table(title="Per-Symbol Breakdown")
        sym_table.add_column("Symbol")
        sym_table.add_column("Trades", justify="right")
        sym_table.add_column("Win Rate", justify="right")
        sym_table.add_column("Gross P&L", justify="right")
        sym_table.add_column("Net P&L", justify="right")

        for sym in symbols:
            sym_trades = [t for t in trades if t["symbol"] == sym]
            sym_wins = sum(1 for t in sym_trades if t["net_pnl"] > 0)
            sym_gross = sum(t["gross_pnl"] for t in sym_trades)
            sym_net = sum(t["net_pnl"] for t in sym_trades)
            sym_wr = sym_wins / len(sym_trades) * 100 if sym_trades else 0
            pnl_style = "green" if sym_net >= 0 else "red"

            sym_table.add_row(
                sym,
                str(len(sym_trades)),
                f"{sym_wr:.0f}%",
                f"[{pnl_style}]{sym_gross:>+,.2f}[/{pnl_style}]",
                f"[{pnl_style}]{sym_net:>+,.2f}[/{pnl_style}]",
            )

        console.print(sym_table)


def _add_symbol_subtotal(table: Table, symbol: str, count: int, pnl: float) -> None:
    """Add a subtotal row for a symbol group."""
    style = "green" if pnl >= 0 else "red"
    table.add_row(
        "", "", "", "", "", "", "", "", "",
        f"[bold]{symbol}[/bold]",
        f"[bold]({count})[/bold]",
        f"[bold {style}]{pnl:>+,.2f}[/bold {style}]",
        style="dim",
    )


def _display_unmatched_records(
    session, account: str | None, symbol: str | None
) -> None:
    """Display raw imports that haven't been matched."""
    query = session.query(IBKRRawImport).filter(
        IBKRRawImport.matched == False,  # noqa: E712
    )
    if account:
        query = query.filter(IBKRRawImport.account_id == account)
    if symbol:
        query = query.filter(IBKRRawImport.underlying_symbol == symbol.upper())

    unmatched = query.order_by(IBKRRawImport.trade_date).all()

    if not unmatched:
        console.print("\n[green]No unmatched records[/green]")
        return

    table = Table(title=f"Unmatched Records ({len(unmatched)})")
    table.add_column("ID", style="dim")
    table.add_column("Date")
    table.add_column("Symbol")
    table.add_column("Strike", justify="right")
    table.add_column("P/C")
    table.add_column("Expiry")
    table.add_column("Action")
    table.add_column("Qty", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Category")
    table.add_column("ExecID", style="dim")

    for r in unmatched:
        action = f"{r.buy_sell}/{r.open_close}" if r.open_close else r.buy_sell
        table.add_row(
            str(r.id),
            str(r.trade_date),
            r.underlying_symbol,
            f"{r.strike:.1f}" if r.strike else "",
            r.put_call or "",
            str(r.expiry) if r.expiry else "",
            action,
            str(r.quantity),
            f"{r.price:.4f}",
            r.asset_category,
            r.ibkr_exec_id[:25] if r.ibkr_exec_id else "",
        )

    console.print(table)


def _display_raw_imports(
    session, account: str | None, symbol: str | None
) -> None:
    """Display all raw import records."""
    query = session.query(IBKRRawImport)
    if account:
        query = query.filter(IBKRRawImport.account_id == account)
    if symbol:
        query = query.filter(IBKRRawImport.underlying_symbol == symbol.upper())

    records = query.order_by(IBKRRawImport.trade_date, IBKRRawImport.id).all()

    if not records:
        console.print("\n[yellow]No raw import records found[/yellow]")
        return

    table = Table(title=f"All Raw Imports ({len(records)})")
    table.add_column("ID", style="dim")
    table.add_column("Date")
    table.add_column("Symbol")
    table.add_column("Strike", justify="right")
    table.add_column("P/C")
    table.add_column("Expiry")
    table.add_column("Action")
    table.add_column("Qty", justify="right")
    table.add_column("Price", justify="right")
    table.add_column("Net Cash", justify="right")
    table.add_column("Matched")
    table.add_column("Category")

    for r in records:
        action = f"{r.buy_sell}/{r.open_close}" if r.open_close else r.buy_sell
        action_style = "green" if r.is_sell_to_open() else "red" if r.is_buy_to_close() else "white"
        matched_str = "[green]Yes[/green]" if r.matched else "[red]No[/red]"

        table.add_row(
            str(r.id),
            str(r.trade_date),
            r.underlying_symbol,
            f"{r.strike:.1f}" if r.strike else "",
            r.put_call or "",
            str(r.expiry) if r.expiry else "",
            f"[{action_style}]{action}[/{action_style}]",
            str(r.quantity),
            f"{r.price:.4f}",
            f"{r.net_cash:.2f}" if r.net_cash else "",
            matched_str,
            r.asset_category,
        )

    console.print(table)


def run_taad_gaps(
    account: str = typer.Option(
        None,
        "--account",
        "-a",
        help="Filter by account ID.",
    ),
) -> None:
    """Identify gaps in imported trade data.

    Shows:
    - Date coverage of import sessions
    - Calendar gaps between sessions (potential missing data)
    - Unmatched/orphan records that need attention
    - Open positions with no closing trade yet
    """
    from sqlalchemy import func as sa_func

    with get_db_session() as session:
        # --- Import Coverage ---
        imp_query = session.query(ImportSession).filter(
            ImportSession.status == "completed"
        ).order_by(ImportSession.date_range_start)
        if account:
            imp_query = imp_query.filter(ImportSession.account_id == account)
        import_sessions = imp_query.all()

        if not import_sessions:
            console.print("[yellow]No completed import sessions found[/yellow]")
            return

        console.print("\n[bold]Import Coverage[/bold]")
        console.print("─" * 60)

        # Find coverage gaps between sessions
        gaps = []
        for i, s in enumerate(import_sessions):
            start = s.date_range_start
            end = s.date_range_end
            if start and end:
                console.print(
                    f"  Session {s.id}: {start} → {end}  "
                    f"({(end - start).days + 1} days, "
                    f"{s.imported_records} records)"
                )
                if i > 0:
                    prev_end = import_sessions[i - 1].date_range_end
                    if prev_end and start and (start - prev_end).days > 1:
                        gap_days = (start - prev_end).days - 1
                        gaps.append((prev_end, start, gap_days))

        if gaps:
            console.print(f"\n[yellow]Coverage Gaps Found ({len(gaps)}):[/yellow]")
            for prev_end, next_start, gap_days in gaps:
                console.print(
                    f"  [red]GAP: {prev_end} → {next_start} ({gap_days} days missing)[/red]"
                )
        else:
            console.print("\n[green]No coverage gaps detected[/green]")

        # Overall date range
        all_starts = [s.date_range_start for s in import_sessions if s.date_range_start]
        all_ends = [s.date_range_end for s in import_sessions if s.date_range_end]
        if all_starts and all_ends:
            overall_start = min(all_starts)
            overall_end = max(all_ends)
            total_days = (overall_end - overall_start).days + 1
            console.print(f"\n  Overall range: {overall_start} → {overall_end} ({total_days} days)")

        # --- Unmatched Records ---
        unmatched_query = session.query(IBKRRawImport).filter(
            IBKRRawImport.matched == False,  # noqa: E712
            IBKRRawImport.asset_category == "OPT",
        )
        if account:
            unmatched_query = unmatched_query.filter(IBKRRawImport.account_id == account)

        unmatched_opts = unmatched_query.all()

        console.print(f"\n[bold]Unmatched Option Records: {len(unmatched_opts)}[/bold]")
        if unmatched_opts:
            console.print("─" * 60)
            sto_unmatched = [r for r in unmatched_opts if r.is_sell_to_open()]
            btc_unmatched = [r for r in unmatched_opts if r.is_buy_to_close()]
            other_unmatched = [
                r for r in unmatched_opts
                if not r.is_sell_to_open() and not r.is_buy_to_close()
            ]

            if sto_unmatched:
                console.print(f"  [yellow]Open STOs (no close found): {len(sto_unmatched)}[/yellow]")
                for r in sto_unmatched:
                    status = ""
                    if r.expiry and r.expiry >= us_trading_date():
                        status = " [cyan](still open)[/cyan]"
                    elif r.expiry and r.expiry < us_trading_date():
                        status = " [red](past expiry — may need rematch)[/red]"
                    console.print(
                        f"    {r.trade_date} {r.underlying_symbol} "
                        f"{r.strike:.1f}{r.put_call} exp={r.expiry} "
                        f"x{abs(r.quantity)} @{r.price:.4f}{status}"
                    )

            if btc_unmatched:
                console.print(f"  [red]Orphan BTCs (no matching STO): {len(btc_unmatched)}[/red]")
                for r in btc_unmatched:
                    console.print(
                        f"    {r.trade_date} {r.underlying_symbol} "
                        f"{r.strike:.1f}{r.put_call} exp={r.expiry} "
                        f"x{abs(r.quantity)} @{r.price:.4f}"
                    )

            if other_unmatched:
                console.print(f"  [dim]Other unmatched: {len(other_unmatched)}[/dim]")
                for r in other_unmatched:
                    console.print(
                        f"    {r.trade_date} {r.underlying_symbol} "
                        f"{r.buy_sell}/{r.open_close} "
                        f"x{abs(r.quantity)} @{r.price:.4f}"
                    )
        else:
            console.print("  [green]All option records matched[/green]")

        # --- Non-option unmatched (CASH, STK, etc.) ---
        non_opt_query = session.query(IBKRRawImport).filter(
            IBKRRawImport.matched == False,  # noqa: E712
            IBKRRawImport.asset_category != "OPT",
        )
        if account:
            non_opt_query = non_opt_query.filter(IBKRRawImport.account_id == account)

        non_opt = non_opt_query.all()
        if non_opt:
            console.print(f"\n[bold]Non-Option Unmatched: {len(non_opt)}[/bold]")
            for r in non_opt:
                console.print(
                    f"  {r.trade_date} [{r.asset_category}] {r.underlying_symbol} "
                    f"{r.buy_sell} x{abs(r.quantity)} @{r.price:.4f}"
                )

        # --- Match quality summary ---
        total_matches = session.query(TradeMatchingLog).count()
        if total_matches > 0:
            match_types = (
                session.query(
                    TradeMatchingLog.match_type,
                    sa_func.count(TradeMatchingLog.id),
                )
                .group_by(TradeMatchingLog.match_type)
                .all()
            )
            avg_confidence = session.query(
                sa_func.avg(TradeMatchingLog.confidence_score)
            ).scalar()

            console.print(f"\n[bold]Match Quality[/bold]")
            console.print("─" * 60)
            for match_type, count in match_types:
                console.print(f"  {match_type}: {count}")
            console.print(f"  Average confidence: {avg_confidence:.2f}")
            console.print(f"  Total matched lifecycles: {total_matches}")


def run_taad_enrich(
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
    """Enrich historical trades with market context data.

    Populates TradeEntrySnapshot and TradeExitSnapshot records with
    stock prices, technical indicators, VIX, sector data, FOMC calendar,
    and Black-Scholes IV approximations from yfinance.
    """
    from src.data.models import Trade
    from src.taad.enrichment.providers import (
        YFinanceProvider,
        IBKRHistoricalProvider,
        FallbackChainProvider,
    )
    from src.taad.enrichment.engine import HistoricalEnrichmentEngine

    console.print("\n[bold]TAAD Enrichment — Historical Trade Context Reconstruction[/bold]")
    console.print("─" * 60)

    with get_db_session() as session:
        # Build query for trades to enrich
        query = session.query(Trade)

        if account:
            query = query.filter(Trade.account_id == account)
        if symbol:
            query = query.filter(Trade.symbol == symbol.upper())
        # Note: no enrichment_status filter — the engine handles merge/skip logic.
        # All trades are passed through; already-enriched trades get gap-filled.

        query = query.order_by(Trade.entry_date)
        if limit > 0:
            query = query.limit(limit)

        trades = query.all()

        if not trades:
            console.print("[green]No trades need enrichment[/green]")
            return

        console.print(f"  Trades to enrich: [cyan]{len(trades)}[/cyan]")
        if account:
            console.print(f"  Account filter:   {account}")
        if symbol:
            console.print(f"  Symbol filter:    {symbol}")
        console.print(f"  Force re-enrich:  {'Yes' if force else 'No'}")
        console.print(f"  IBKR data:        {'Yes' if with_ibkr else 'No'}")
        console.print(f"  Barchart scrape:  {'Yes' if with_scrape else 'No'}")

        # Dry run — just show what would be enriched
        if dry_run:
            console.print(f"\n[yellow]DRY RUN — no changes will be made[/yellow]\n")

            table = Table(title=f"Trades to Enrich ({len(trades)})")
            table.add_column("ID", style="dim")
            table.add_column("Date")
            table.add_column("Symbol")
            table.add_column("Strike", justify="right")
            table.add_column("DTE", justify="right")
            table.add_column("Premium", justify="right")
            table.add_column("Status")

            for t in trades[:50]:
                status = t.enrichment_status or "pending"
                status_style = {
                    "complete": "green",
                    "partial": "yellow",
                    "pending": "dim",
                }.get(status, "white")

                table.add_row(
                    str(t.id),
                    str(t.entry_date.date() if hasattr(t.entry_date, "date") else t.entry_date),
                    t.symbol,
                    f"{t.strike:.1f}",
                    str(t.dte),
                    f"{t.entry_premium:.4f}",
                    f"[{status_style}]{status}[/{status_style}]",
                )

            console.print(table)
            if len(trades) > 50:
                console.print(f"\n  ... and {len(trades) - 50} more trades")
            return

        # Build provider chain
        providers = [YFinanceProvider()]
        if with_scrape:
            # Try Playwright scraper first (free, wider date range — 2017+)
            try:
                from src.taad.enrichment.barchart_playwright import PlaywrightBarchartProvider
                pw_provider = PlaywrightBarchartProvider()
                if pw_provider.has_valid_session():
                    providers.append(pw_provider)
                    console.print(
                        f"  [green]Barchart Playwright scraper enabled "
                        f"(cache: {pw_provider.cache.count()} entries)[/green]"
                    )
                else:
                    console.print(
                        "  [yellow]Barchart Playwright skipped: no saved session. "
                        "Run `nakedtrader taad-barchart-login` first.[/yellow]"
                    )
            except ImportError:
                console.print(
                    "  [yellow]Playwright not installed. "
                    "Install with: pip install playwright && playwright install chromium[/yellow]"
                )
            except Exception as e:
                console.print(f"  [yellow]Playwright scraper not available: {e}[/yellow]")

            # Also add API scraper as fallback (if API key is set)
            try:
                from src.taad.enrichment.barchart_scraper import BarchartScraperProvider
                barchart = BarchartScraperProvider()
                if barchart.api_key:
                    providers.append(barchart)
                    console.print(
                        f"  [green]Barchart API scraper enabled "
                        f"(cache: {barchart.cache.count()} entries)[/green]"
                    )
            except Exception as e:
                console.print(f"  [yellow]Barchart API not available: {e}[/yellow]")
        if with_ibkr:
            try:
                from src.tools.ibkr_client import IBKRClient
                ibkr = IBKRClient()
                ibkr.connect()
                providers.append(IBKRHistoricalProvider(ibkr))
                console.print("  [green]IBKR connected[/green]")
            except Exception as e:
                console.print(f"  [yellow]IBKR not available: {e}[/yellow]")

        provider = FallbackChainProvider(providers) if len(providers) > 1 else providers[0]

        # Run enrichment
        console.print(f"\n  Starting enrichment...\n")
        engine = HistoricalEnrichmentEngine(provider=provider, session=session)
        batch_result = engine.enrich_batch(trades, force=force)

        # Commit
        session.commit()

        # Clean up Playwright browser if it was used
        for p in providers:
            if hasattr(p, "close"):
                try:
                    p.close()
                except Exception:
                    pass

        # Display results
        console.print(f"\n[bold green]Enrichment Complete[/bold green]")
        console.print("─" * 40)
        console.print(f"  Total:       {batch_result.total}")
        console.print(f"  Enriched:    [green]{batch_result.enriched}[/green]")
        console.print(f"  Merged:      [cyan]{batch_result.merged}[/cyan]")
        console.print(f"  Skipped:     [yellow]{batch_result.skipped}[/yellow]")
        console.print(f"  Failed:      [red]{batch_result.failed}[/red]")
        console.print(f"  Avg quality: {batch_result.avg_quality:.3f}")

        # Show any errors
        errors = [r for r in batch_result.results if r.errors]
        if errors:
            console.print(f"\n[yellow]Trades with issues ({len(errors)}):[/yellow]")
            for r in errors[:10]:
                console.print(f"  {r.symbol} (trade {r.trade_id}): {', '.join(r.errors[:3])}")
            if len(errors) > 10:
                console.print(f"  ... and {len(errors) - 10} more")


def run_taad_promote(
    account: str = typer.Option(
        None, "--account", "-a",
        help="Filter by account ID.",
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run",
        help="Show what would be promoted without making changes.",
    ),
) -> None:
    """Promote matched trade lifecycles into public.trades for enrichment.

    Converts TradeMatchingLog rows (matched STO+BTC pairs) into Trade
    records with trade_source='ibkr_import'. Idempotent — safe to re-run.
    """
    from src.taad.trade_promoter import promote_matches_to_trades

    console.print("\n[bold]TAAD Promote — Matched Trades → public.trades[/bold]")
    console.print("─" * 55)

    if dry_run:
        console.print("[yellow]DRY RUN — no changes will be made[/yellow]\n")

    with get_db_session() as session:
        result = promote_matches_to_trades(
            session=session,
            account_id=account,
            dry_run=dry_run,
        )

        if not dry_run:
            session.commit()

    # Display results
    console.print(f"\n[bold green]Promotion {'Preview' if dry_run else 'Complete'}[/bold green]")
    console.print("─" * 40)
    console.print(f"  Promoted:          [green]{result.promoted}[/green]")
    console.print(f"  Already promoted:  [yellow]{result.skipped_already_promoted}[/yellow]")
    console.print(f"  Skipped (no exit): [dim]{result.skipped_no_exit_date}[/dim]")
    console.print(f"  Errors:            [red]{result.errors}[/red]")
    console.print(f"  Total processed:   {result.total_processed}")

    if result.error_messages:
        console.print(f"\n[red]Errors:[/red]")
        for msg in result.error_messages[:10]:
            console.print(f"  - {msg}")
        if len(result.error_messages) > 10:
            console.print(f"  ... and {len(result.error_messages) - 10} more")

    if not dry_run and result.promoted > 0:
        console.print(
            f"\n  [dim]Run [bold]nakedtrader taad-enrich[/bold] to enrich the promoted trades.[/dim]"
        )


def run_taad_barchart_login() -> None:
    """Interactive Barchart login via Playwright.

    Opens a visible browser for the user to log in to Barchart.
    Saves session cookies for use by the Playwright scraper.
    """
    try:
        from src.taad.enrichment.barchart_playwright import PlaywrightBarchartProvider
    except ImportError:
        console.print(
            "[red]Playwright not installed.[/red]\n"
            "Install with: pip install playwright && playwright install chromium"
        )
        return

    provider = PlaywrightBarchartProvider()

    console.print("\n[bold]Barchart Premier Login[/bold]")
    console.print("─" * 40)
    console.print("A browser window will open.")
    console.print("Log in to your Barchart account with your Premier subscription.")
    console.print("Press Enter in this terminal when login is complete.\n")

    success = provider.login_interactive()
    if success:
        console.print(f"\n[green]Session saved successfully![/green]")
        console.print(f"  Storage: {provider.storage_state_path}")
        console.print(
            "\nYou can now use [bold]--with-scrape[/bold] with "
            "[bold]nakedtrader taad-enrich[/bold] to scrape option data."
        )
    else:
        console.print("[red]Login failed or was cancelled.[/red]")
