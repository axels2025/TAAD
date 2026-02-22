"""CLI commands for trade execution.

This module provides the CLI interface for Monday morning execution:
- execute-staged: Run the full Monday morning workflow
- show-staged: Display currently staged trades
- cancel-staged: Cancel all staged trades
- execution-report: View last execution results

These commands coordinate the Phase 4 execution workflow.
"""

from datetime import datetime

from rich import box
from rich.console import Console

from src.utils.calc import fmt_pct

from src.utils.timezone import us_trading_date
from rich.panel import Panel
from rich.table import Table

from src.services.execution_scheduler import (
    ExecutionReport,
    ExecutionScheduler,
    ExecutionStatus,
    TradeExecutionResult,
)
from src.services.premarket_validator import StagedOpportunity


class ExecutionDisplay:
    """Display utilities for execution results.

    Handles all Rich console output for execution-related commands.
    """

    def __init__(self, console: Console | None = None):
        """Initialize with optional console.

        Args:
            console: Rich Console instance. Creates new if None.
        """
        self.console = console or Console()

    def display_staged_trades(
        self,
        opportunities: list[StagedOpportunity],
        session: str | None = None,
    ) -> None:
        """Display currently staged trades.

        Args:
            opportunities: List of staged opportunities
            session: Optional execution session identifier
        """
        if not opportunities:
            self.console.print("[yellow]No staged trades found.[/yellow]")
            return

        header = "STAGED TRADES"
        if session:
            header += f" — {session}"

        self.console.print()
        self.console.print(
            Panel(f"[bold]{header}[/bold]", style="blue", padding=(0, 1))
        )

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("#", justify="center", style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Strike", justify="right")
        table.add_column("Exp", justify="center")
        table.add_column("OTM%", justify="right")
        table.add_column("Limit", justify="right")
        table.add_column("Contracts", justify="center")
        table.add_column("Margin", justify="right")
        table.add_column("Premium", justify="right")

        for i, opp in enumerate(opportunities, 1):
            effective_strike = opp.adjusted_strike or opp.strike
            effective_limit = opp.adjusted_limit_price or opp.staged_limit_price
            expected_premium = effective_limit * 100 * opp.staged_contracts

            table.add_row(
                str(i),
                opp.symbol,
                f"${effective_strike:.2f}",
                opp.expiration,
                fmt_pct(opp.otm_pct),
                f"${effective_limit:.2f}",
                str(opp.staged_contracts),
                f"${opp.staged_margin:,.0f}",
                f"${expected_premium:.0f}",
            )

        self.console.print(table)

        # Summary
        total_margin = sum(o.staged_margin for o in opportunities)
        total_premium = sum(
            (o.adjusted_limit_price or o.staged_limit_price) * 100 * o.staged_contracts
            for o in opportunities
        )

        self.console.print()
        self.console.print(
            f"  Total: {len(opportunities)} trades | "
            f"Margin: ${total_margin:,.0f} | "
            f"Expected Premium: ${total_premium:,.0f}"
        )

    def display_execution_progress(
        self,
        result: TradeExecutionResult,
        trade_num: int,
        total_trades: int,
    ) -> None:
        """Display progress for a single trade execution.

        Args:
            result: The trade execution result
            trade_num: Current trade number
            total_trades: Total number of trades
        """
        status_str = self._format_execution_status(result.status)
        symbol = result.opportunity.symbol

        if result.status == ExecutionStatus.FILLED:
            self.console.print(
                f"  [{trade_num}/{total_trades}] {symbol}: {status_str} "
                f"@ ${result.fill_price:.2f} x {result.contracts_filled}"
            )
        elif result.status == ExecutionStatus.WORKING:
            self.console.print(
                f"  [{trade_num}/{total_trades}] {symbol}: {status_str} "
                f"@ ${result.final_limit:.2f} (adj: {result.adjustments_made})"
            )
        else:
            self.console.print(
                f"  [{trade_num}/{total_trades}] {symbol}: {status_str} "
                f"- {result.error_message or 'Unknown error'}"
            )

    def display_execution_report(self, report: ExecutionReport) -> None:
        """Display the complete execution report.

        Args:
            report: The execution report to display
        """
        mode_str = "[DRY-RUN]" if report.dry_run else "[LIVE]"

        self.console.print()
        self.console.print(
            Panel(
                f"[bold]EXECUTION REPORT {mode_str}[/bold]",
                style="green" if report.filled_count > 0 else "yellow",
                padding=(0, 1),
            )
        )

        # Summary stats
        self.console.print()
        self.console.print(f"  Date:      {report.execution_date.strftime('%Y-%m-%d')}")
        self.console.print(f"  Duration:  {report.duration_seconds:.1f}s")
        self.console.print()

        # Pipeline summary
        pipeline_table = Table(box=box.SIMPLE, show_header=False)
        pipeline_table.add_column("Stage", style="dim")
        pipeline_table.add_column("Count", justify="right")
        pipeline_table.add_column("Note")

        pipeline_table.add_row("Staged", str(report.staged_count), "")
        pipeline_table.add_row(
            "→ Validated",
            str(report.validated_count),
            "(passed Stage 1)"
        )
        pipeline_table.add_row(
            "→ Confirmed",
            str(report.confirmed_count),
            "(passed Stage 2)"
        )
        pipeline_table.add_row("→ Executed", str(report.executed_count), "")

        self.console.print(pipeline_table)
        self.console.print()

        # Results breakdown
        if report.execution_results:
            results_table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
            results_table.add_column("Symbol", style="cyan")
            results_table.add_column("Strike", justify="right")
            results_table.add_column("Limit", justify="right")
            results_table.add_column("Fill", justify="right")
            results_table.add_column("Qty", justify="center")
            results_table.add_column("Adj", justify="center")
            results_table.add_column("Status", justify="center")

            for result in report.execution_results:
                status_str = self._format_execution_status(result.status)
                strike = result.opportunity.adjusted_strike or result.opportunity.strike
                fill_str = f"${result.fill_price:.2f}" if result.fill_price else "—"
                qty_str = (
                    f"{result.contracts_filled}/{result.contracts_requested}"
                    if result.contracts_filled > 0
                    else str(result.contracts_requested)
                )

                results_table.add_row(
                    result.opportunity.symbol,
                    f"${strike:.0f}",
                    f"${result.limit_price:.2f}",
                    fill_str,
                    qty_str,
                    str(result.adjustments_made),
                    status_str,
                )

            self.console.print(results_table)
            self.console.print()

        # Final summary
        self.console.print(
            f"  [green]Filled:  {report.filled_count}[/green]  |  "
            f"[yellow]Working: {report.working_count}[/yellow]  |  "
            f"[red]Failed:  {report.failed_count}[/red]"
        )
        self.console.print()
        self.console.print(
            f"  Total Premium: [bold]${report.total_premium:,.2f}[/bold]"
        )

        # Warnings
        if report.warnings:
            self.console.print()
            for warning in report.warnings:
                self.console.print(f"  [yellow]⚠ {warning}[/yellow]")

    def _format_execution_status(self, status: ExecutionStatus) -> str:
        """Format execution status with color.

        Args:
            status: The ExecutionStatus to format

        Returns:
            Formatted string with Rich markup
        """
        status_colors = {
            ExecutionStatus.FILLED: "green",
            ExecutionStatus.WORKING: "yellow",
            ExecutionStatus.PARTIALLY_FILLED: "yellow",
            ExecutionStatus.PENDING: "dim",
            ExecutionStatus.EXECUTING: "cyan",
            ExecutionStatus.CANCELLED: "red",
            ExecutionStatus.REJECTED: "red",
            ExecutionStatus.SKIPPED: "dim",
            ExecutionStatus.ERROR: "red",
        }

        color = status_colors.get(status, "white")
        return f"[{color}]{status.value}[/{color}]"

    def prompt_execution_confirmation(
        self,
        opportunities: list[StagedOpportunity],
        dry_run: bool,
    ) -> bool:
        """Prompt user to confirm execution.

        Args:
            opportunities: Opportunities to execute
            dry_run: Whether this is a dry run

        Returns:
            True if user confirms, False otherwise
        """
        mode_str = "DRY-RUN" if dry_run else "LIVE PAPER TRADING"

        self.console.print()
        self.console.print(
            f"[bold]Ready to execute {len(opportunities)} trades ({mode_str}).[/bold]"
        )
        self.console.print()

        if not dry_run:
            self.console.print(
                "[yellow]⚠ This will place REAL orders in paper trading.[/yellow]"
            )
            self.console.print()

        try:
            response = self.console.input(
                "Proceed? [y/N]: "
            ).strip().lower()
            return response in ("y", "yes")
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Cancelled.[/yellow]")
            return False


def run_execute_staged(
    opportunities: list[StagedOpportunity],
    scheduler: ExecutionScheduler | None = None,
    dry_run: bool = True,
    skip_confirmation: bool = False,
    console: Console | None = None,
) -> ExecutionReport | None:
    """Run the execute-staged command.

    Full Monday morning workflow:
    1. Display staged trades
    2. Confirm execution
    3. Run two-stage validation
    4. Execute confirmed trades
    5. Display report

    Args:
        opportunities: Staged opportunities to execute
        scheduler: Optional ExecutionScheduler
        dry_run: If True, simulate orders
        skip_confirmation: If True, skip user confirmation
        console: Optional Rich console

    Returns:
        ExecutionReport if executed, None if cancelled
    """
    display = ExecutionDisplay(console)
    scheduler = scheduler or ExecutionScheduler()

    if not opportunities:
        display.console.print("[yellow]No staged trades to execute.[/yellow]")
        return None

    # Display staged trades
    display.display_staged_trades(opportunities)

    # Confirm execution
    if not skip_confirmation:
        if not display.prompt_execution_confirmation(opportunities, dry_run):
            return None

    # Run execution
    display.console.print()
    display.console.print(
        f"[bold]{'[DRY-RUN] ' if dry_run else ''}Starting execution...[/bold]"
    )

    report = scheduler.run_monday_morning(opportunities, dry_run=dry_run)

    # Display report
    display.display_execution_report(report)

    return report


def run_show_staged(
    opportunities: list[StagedOpportunity],
    session: str | None = None,
    console: Console | None = None,
) -> None:
    """Run the show-staged command.

    Args:
        opportunities: Staged opportunities to display
        session: Optional execution session identifier
        console: Optional Rich console
    """
    display = ExecutionDisplay(console)
    display.display_staged_trades(opportunities, session=session)


def format_execution_session() -> str:
    """Generate execution session identifier.

    Returns:
        Session string like 'week_of_2026-02-02'
    """
    today = us_trading_date()
    # Find the Monday of this week
    days_since_monday = today.weekday()
    monday = today.replace(day=today.day - days_since_monday)
    return f"week_of_{monday.isoformat()}"
