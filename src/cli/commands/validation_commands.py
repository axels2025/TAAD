"""CLI commands for staged trade validation.

This module provides the CLI interface for two-stage validation:
- validate-staged: Pre-market validation (Stage 1 - 9:15 AM)
- validate-staged --at-open: Market-open validation (Stage 2 - 9:30 AM)

These commands display validation results and update opportunity states.
"""

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.services.premarket_validator import (
    OpenCheckResult,
    PremarketCheckResult,
    PremarketValidator,
    StagedOpportunity,
    ValidationStatus,
)


class ValidationDisplay:
    """Display utilities for validation results.

    Handles all Rich console output for validation-related commands,
    including the Stage 1 and Stage 2 validation tables.
    """

    def __init__(self, console: Console | None = None):
        """Initialize with optional console.

        Args:
            console: Rich Console instance. Creates new if None.
        """
        self.console = console or Console()

    def display_premarket_results(
        self,
        results: list[PremarketCheckResult],
    ) -> None:
        """Display Stage 1 pre-market validation results.

        Shows the stock price check results in a formatted table.

        Args:
            results: List of PremarketCheckResult from validation
        """
        self.console.print()
        self.console.print(
            Panel(
                "[bold]STAGE 1: PRE-MARKET VALIDATION (9:15 AM ET)[/bold]",
                style="cyan",
                padding=(0, 1),
            )
        )

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("Symbol", style="cyan")
        table.add_column("Staged $", justify="right")
        table.add_column("Pre-Mkt $", justify="right")
        table.add_column("Change", justify="right")
        table.add_column("OTM%", justify="right")
        table.add_column("Status", justify="center")

        for result in results:
            # Format status with color and icon
            status_str = self._format_status(result.status, is_stage1=True)

            # Format change with color
            change_str = self._format_change(result.deviation_pct)

            # Highlight OTM if adjusted
            otm_str = f"{result.new_otm_pct:.1%}"
            if result.adjusted_strike:
                otm_str = f"[yellow]{otm_str}[/yellow]"

            table.add_row(
                result.opportunity.symbol,
                f"${result.staged_price:.2f}",
                f"${result.premarket_price:.2f}",
                change_str,
                otm_str,
                status_str,
            )

        self.console.print(table)

        # Summary line
        ready = sum(1 for r in results if r.status == ValidationStatus.READY)
        adjusted = sum(1 for r in results if r.status == ValidationStatus.ADJUSTED)
        stale = sum(1 for r in results if r.status == ValidationStatus.STALE)

        # Calculate total margin requirement
        total_margin = sum(
            r.opportunity.staged_margin
            for r in results
            if r.opportunity.staged_margin
        )

        self.console.print()
        self.console.print(
            f"  {ready} READY, {adjusted} ADJUSTED, {stale} STALE"
        )
        self.console.print(
            f"  [bold]Total Margin Required: ${total_margin:,.2f}[/bold]"
        )

        # Show adjustment details
        for result in results:
            if result.adjustment_reason:
                symbol = result.opportunity.symbol
                self.console.print(f"  [dim]{symbol}: {result.adjustment_reason}[/dim]")

    def display_open_results(
        self,
        results: list[OpenCheckResult],
    ) -> None:
        """Display Stage 2 market-open validation results.

        Shows the premium check results in a formatted table.

        Args:
            results: List of OpenCheckResult from validation
        """
        self.console.print()
        self.console.print(
            Panel(
                "[bold]STAGE 2: MARKET-OPEN VALIDATION (9:30 AM ET)[/bold]",
                style="green",
                padding=(0, 1),
            )
        )

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("Symbol", style="cyan")
        table.add_column("Staged", justify="right")
        table.add_column("Live Bid", justify="right")
        table.add_column("Live Ask", justify="right")
        table.add_column("Premium \u0394", justify="right")
        table.add_column("Status", justify="center")

        for result in results:
            # Format status with color and icon
            status_str = self._format_status(result.status, is_stage1=False)

            # Format premium change with color
            change_str = self._format_change(result.premium_deviation_pct)

            # Format staged limit
            staged_str = f"Lmt ${result.staged_limit:.2f}"

            table.add_row(
                result.opportunity.symbol,
                staged_str,
                f"${result.live_bid:.2f}",
                f"${result.live_ask:.2f}",
                change_str,
                status_str,
            )

        self.console.print(table)

        # Summary line
        confirmed = sum(1 for r in results if r.status == ValidationStatus.READY)
        adjusted = sum(1 for r in results if r.status == ValidationStatus.ADJUSTED)
        stale = sum(1 for r in results if r.status == ValidationStatus.STALE)

        # Calculate total margin requirement for proceeding trades
        total_margin = sum(
            r.opportunity.staged_margin
            for r in results
            if r.opportunity.staged_margin and r.status != ValidationStatus.STALE
        )

        self.console.print()
        proceeding = confirmed + adjusted
        self.console.print(
            f"  {proceeding} trades proceeding to execution. "
            f"{stale} trade(s) marked STALE."
        )
        if proceeding > 0:
            self.console.print(
                f"  [bold]Total Margin Required: ${total_margin:,.2f}[/bold]"
            )

        # Show stale reasons
        for result in results:
            if result.status == ValidationStatus.STALE and result.adjustment_reason:
                symbol = result.opportunity.symbol
                self.console.print(
                    f"  [yellow]{symbol}: {result.adjustment_reason}[/yellow]"
                )

    def display_waiting_message(self) -> None:
        """Display waiting message between Stage 1 and Stage 2."""
        self.console.print()
        self.console.print("[dim]Waiting for market open...[/dim]")
        self.console.print()

    def _format_status(
        self, status: ValidationStatus, is_stage1: bool = True
    ) -> str:
        """Format validation status with color and icon.

        Args:
            status: The ValidationStatus to format
            is_stage1: If True, use Stage 1 labels (READY); else Stage 2 (CONFIRMED)

        Returns:
            Formatted status string with Rich markup
        """
        if status == ValidationStatus.READY:
            label = "READY" if is_stage1 else "CONFIRMED"
            return f"[green]\u2713 {label}[/green]"
        elif status == ValidationStatus.ADJUSTED:
            return "[yellow]\u26a0 ADJUSTED[/yellow]"
        elif status == ValidationStatus.STALE:
            return "[red]\u2717 STALE[/red]"
        else:
            return f"[dim]{status.value}[/dim]"

    def _format_change(self, change_pct: float) -> str:
        """Format percentage change with color.

        Args:
            change_pct: Change as decimal (e.g., -0.02 = -2%)

        Returns:
            Formatted string with Rich markup
        """
        if abs(change_pct) < 0.03:
            color = "green"
        elif abs(change_pct) < 0.10:
            color = "yellow"
        else:
            color = "red"

        return f"[{color}]{change_pct:+.1%}[/{color}]"


def run_premarket_validation(
    opportunities: list[StagedOpportunity],
    validator: PremarketValidator | None = None,
    console: Console | None = None,
) -> list[PremarketCheckResult]:
    """Run Stage 1 pre-market validation with display.

    This is the main entry point for the validate-staged CLI command.

    Args:
        opportunities: List of staged opportunities to validate
        validator: Optional PremarketValidator. Creates one if None.
        console: Optional Rich console

    Returns:
        List of PremarketCheckResult
    """
    display = ValidationDisplay(console)
    validator = validator or PremarketValidator()

    if not opportunities:
        display.console.print(
            "[yellow]No staged opportunities to validate.[/yellow]"
        )
        return []

    results = validator.validate_premarket(opportunities)
    display.display_premarket_results(results)

    return results


def run_open_validation(
    opportunities: list[StagedOpportunity],
    validator: PremarketValidator | None = None,
    console: Console | None = None,
) -> list[OpenCheckResult]:
    """Run Stage 2 market-open validation with display.

    This is the main entry point for the validate-staged --at-open command.

    Args:
        opportunities: List of READY opportunities (from Stage 1)
        validator: Optional PremarketValidator. Creates one if None.
        console: Optional Rich console

    Returns:
        List of OpenCheckResult
    """
    display = ValidationDisplay(console)
    validator = validator or PremarketValidator()

    if not opportunities:
        display.console.print(
            "[yellow]No ready opportunities to validate.[/yellow]"
        )
        return []

    results = validator.validate_at_open(opportunities)
    display.display_open_results(results)

    return results


def run_full_validation(
    opportunities: list[StagedOpportunity],
    validator: PremarketValidator | None = None,
    console: Console | None = None,
    wait_for_open: bool = False,
) -> tuple[list[PremarketCheckResult], list[OpenCheckResult]]:
    """Run full two-stage validation workflow.

    Runs Stage 1, optionally waits, then runs Stage 2.

    Args:
        opportunities: List of staged opportunities
        validator: Optional PremarketValidator
        console: Optional Rich console
        wait_for_open: If True, displays waiting message (actual wait is external)

    Returns:
        Tuple of (Stage 1 results, Stage 2 results)
    """
    display = ValidationDisplay(console)
    validator = validator or PremarketValidator()

    if not opportunities:
        display.console.print(
            "[yellow]No staged opportunities to validate.[/yellow]"
        )
        return [], []

    # Stage 1
    stage1_results = validator.validate_premarket(opportunities)
    display.display_premarket_results(stage1_results)

    # Filter for Stage 2
    ready_opps = [r.opportunity for r in stage1_results if r.passed]

    if not ready_opps:
        display.console.print()
        display.console.print(
            "[yellow]No opportunities passed Stage 1. Nothing to execute.[/yellow]"
        )
        return stage1_results, []

    if wait_for_open:
        display.display_waiting_message()

    # Stage 2
    stage2_results = validator.validate_at_open(ready_opps)
    display.display_open_results(stage2_results)

    return stage1_results, stage2_results
