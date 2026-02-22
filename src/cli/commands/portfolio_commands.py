"""CLI commands for portfolio building and trade staging.

This module provides the CLI interface for Phase 4 Sunday-to-Monday workflow:
- build-portfolio: Build portfolio from strike candidates
- stage-trades: Stage approved trades for Monday execution
- show-staged: Display currently staged trades
- cancel-staged: Cancel staged trades

These commands are designed to be called from the main CLI.
"""

from datetime import datetime

from rich import box
from rich.console import Console

from src.utils.calc import fmt_pct

from src.utils.timezone import us_trading_date
from rich.panel import Panel
from rich.table import Table

from src.services.portfolio_builder import (
    PortfolioBuilder,
    PortfolioPlan,
    StagedTrade,
)
from src.services.strike_finder import StrikeCandidate


class PortfolioDisplay:
    """Display utilities for portfolio plans and staged trades.

    Handles all Rich console output for portfolio-related commands,
    including the margin re-ranking display and portfolio summary tables.
    """

    def __init__(self, console: Console | None = None):
        """Initialize with optional console.

        Args:
            console: Rich Console instance. Creates new if None.
        """
        self.console = console or Console()

    def display_margin_reranking(self, plan: PortfolioPlan) -> None:
        """Display the before/after margin re-ranking table.

        Shows how candidate rankings shift when actual margins
        replace estimates.

        Args:
            plan: The portfolio plan with margin comparisons
        """
        if not plan.margin_comparisons:
            return

        self.console.print()
        self.console.print(
            Panel(
                "[bold]MARGIN RE-RANKING[/bold] (estimated → actual)",
                style="cyan",
                padding=(0, 1),
            )
        )

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("Symbol", style="cyan")
        table.add_column("Estimated Margin (eff%)", justify="right")
        table.add_column("Est Rank", justify="center")
        table.add_column("Actual Margin (eff%)", justify="right")
        table.add_column("Act Rank", justify="center")
        table.add_column("Shift", justify="center")

        for comp in plan.margin_comparisons:
            # Format shift indicator
            if comp.rank_shift > 0:
                shift_str = f"[green]↑{comp.rank_shift}[/green]"
            elif comp.rank_shift < 0:
                shift_str = f"[red]↓{abs(comp.rank_shift)}[/red]"
            else:
                shift_str = "—"

            # Format margin source indicator
            source_indicator = "" if comp.margin_source == "ibkr_whatif" else "*"

            table.add_row(
                f"{comp.candidate.symbol} ${comp.candidate.strike:.0f}P",
                f"${comp.estimated_margin:,.0f} ({comp.estimated_efficiency:.2%})",
                f"#{comp.estimated_rank}",
                f"${comp.actual_margin:,.0f}{source_indicator} ({comp.actual_efficiency:.2%})",
                f"#{comp.actual_rank}",
                shift_str,
            )

        self.console.print(table)
        self.console.print(
            "[dim]* = estimated margin (IBKR not available)[/dim]"
        )

    def display_portfolio_plan(self, plan: PortfolioPlan) -> None:
        """Display the final portfolio plan.

        Shows selected trades ranked by margin efficiency,
        plus skipped trades and summary statistics.

        Args:
            plan: The portfolio plan to display
        """
        self.console.print()
        self.console.print(
            Panel(
                "[bold]FINAL PORTFOLIO[/bold] (ranked by actual margin efficiency)",
                style="green",
                padding=(0, 1),
            )
        )

        table = Table(box=box.ROUNDED, show_header=True, header_style="bold")
        table.add_column("Rk", justify="center", style="dim")
        table.add_column("Symbol", style="cyan")
        table.add_column("Strike", justify="right")
        table.add_column("Exp", justify="center")
        table.add_column("OTM%", justify="right")
        table.add_column("Limit", justify="right")
        table.add_column("Contracts", justify="center")
        table.add_column("Margin", justify="right")
        table.add_column("Premium", justify="right")
        table.add_column("Efficiency", justify="right")

        # Add selected trades
        for trade in plan.trades:
            eff_style = "green" if trade.margin_efficiency >= 0.05 else ""
            margin_indicator = "" if trade.margin_source == "ibkr_whatif" else "*"

            table.add_row(
                str(trade.portfolio_rank),
                trade.symbol,
                f"${trade.strike:.2f}",
                trade.expiration.strftime("%b %d"),
                fmt_pct(trade.candidate.otm_pct),
                f"${trade.candidate.suggested_limit:.2f}",
                str(trade.contracts),
                f"${trade.total_margin:,.0f}{margin_indicator}",
                f"${trade.total_premium:.0f}",
                f"[{eff_style}]{trade.margin_efficiency:.2%}[/{eff_style}]",
            )

        # Add skipped trades
        for trade in plan.skipped_trades:
            table.add_row(
                "—",
                f"[dim]{trade.symbol}[/dim]",
                f"[dim]${trade.strike:.2f}[/dim]",
                f"[dim]{trade.expiration.strftime('%b %d')}[/dim]",
                f"[dim]{fmt_pct(trade.candidate.otm_pct)}[/dim]",
                f"[dim]${trade.candidate.suggested_limit:.2f}[/dim]",
                f"[dim]{trade.contracts}[/dim]",
                f"[dim]${trade.total_margin:,.0f}[/dim]",
                f"[dim]${trade.total_premium:.0f}[/dim]",
                f"[dim]{trade.margin_efficiency:.2%} SKIP[/dim]",
            )

        # Add totals row
        table.add_row(
            "",
            "",
            "",
            "",
            "",
            "",
            "[bold]Total:[/bold]",
            f"[bold]${plan.total_margin_used:,.0f}[/bold]",
            f"[bold]${plan.total_premium_expected:,.0f}[/bold]",
            "",
        )

        self.console.print(table)

        # Budget summary
        self.console.print()
        self.console.print(
            f"  Budget: ${plan.margin_budget:,.0f} | "
            f"Used: ${plan.total_margin_used:,.0f} ({plan.budget_utilization:.1%}) | "
            f"Remaining: ${plan.margin_remaining:,.0f}"
        )

        # Skipped trade reasons
        for trade in plan.skipped_trades:
            if trade.skip_reason:
                self.console.print(
                    f"  [yellow]{trade.symbol} skipped: {trade.skip_reason}[/yellow]"
                )

        # Sector distribution
        if plan.sector_distribution:
            sectors_str = ", ".join(
                f"{sector} ({count})"
                for sector, count in plan.sector_distribution.items()
            )
            self.console.print(f"  Sectors: {sectors_str}")

        # Warnings
        for warning in plan.warnings:
            self.console.print(f"  [yellow]⚠ {warning}[/yellow]")

    def display_staged_trades(
        self,
        trades: list[StagedTrade],
        session: str | None = None,
    ) -> None:
        """Display currently staged trades.

        Args:
            trades: List of staged trades
            session: Optional session identifier
        """
        if not trades:
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
        table.add_column("Source", justify="center")

        for i, trade in enumerate(trades, 1):
            source_style = "green" if trade.margin_source == "ibkr_whatif" else "yellow"

            table.add_row(
                str(i),
                trade.symbol,
                f"${trade.strike:.2f}",
                trade.expiration.strftime("%b %d"),
                fmt_pct(trade.candidate.otm_pct),
                f"${trade.candidate.suggested_limit:.2f}",
                str(trade.contracts),
                f"${trade.total_margin:,.0f}",
                f"${trade.total_premium:.0f}",
                f"[{source_style}]{trade.margin_source}[/{source_style}]",
            )

        self.console.print(table)

        total_margin = sum(t.total_margin for t in trades)
        total_premium = sum(t.total_premium for t in trades)
        self.console.print()
        self.console.print(
            f"  Total: {len(trades)} trades | "
            f"Margin: ${total_margin:,.0f} | "
            f"Expected Premium: ${total_premium:,.0f}"
        )

    def prompt_approval(self, plan: PortfolioPlan) -> str | None:
        """Prompt user for portfolio approval.

        Args:
            plan: The portfolio plan to approve

        Returns:
            'y' for approve, 'n' for cancel, 'edit' for modify, None for interrupt
        """
        self.console.print()
        self.console.print(
            "[bold]Approve this portfolio?[/bold] [Y/n/edit]:"
        )
        self.console.print(f"  Y     → Stage all {plan.trade_count} trades")
        self.console.print("  n     → Cancel")
        self.console.print(
            "  edit  → Modify (remove trades, adjust contracts, change strikes)"
        )
        self.console.print()

        try:
            response = self.console.input("[bold cyan]> [/bold cyan]").strip().lower()
            if response in ("", "y", "yes"):
                return "y"
            elif response in ("n", "no"):
                return "n"
            elif response == "edit":
                return "edit"
            else:
                return "n"
        except KeyboardInterrupt:
            self.console.print("\n[yellow]Cancelled.[/yellow]")
            return None


def build_portfolio_interactive(
    candidates: list[StrikeCandidate],
    ibkr_client=None,
    margin_budget: float | None = None,
    console: Console | None = None,
) -> tuple[PortfolioPlan | None, bool]:
    """Build portfolio with interactive display and approval.

    This is the main entry point for the build-portfolio CLI command.
    It builds a portfolio plan, displays it, and prompts for approval.

    Args:
        candidates: Strike candidates to build portfolio from
        ibkr_client: Optional IBKR client for actual margins
        margin_budget: Optional margin budget override
        console: Optional Rich console

    Returns:
        Tuple of (PortfolioPlan or None, approved boolean)
    """
    display = PortfolioDisplay(console)

    if not candidates:
        display.console.print(
            "[red]No candidates provided for portfolio building.[/red]"
        )
        return None, False

    # Build the portfolio
    builder = PortfolioBuilder(ibkr_client=ibkr_client)
    plan = builder.build_portfolio(candidates, margin_budget=margin_budget)

    if plan.trade_count == 0:
        display.console.print(
            "[yellow]No trades selected for portfolio.[/yellow]"
        )
        if plan.warnings:
            for warning in plan.warnings:
                display.console.print(f"  [dim]{warning}[/dim]")
        return plan, False

    # Display margin re-ranking
    display.display_margin_reranking(plan)

    # Display final portfolio
    display.display_portfolio_plan(plan)

    # Prompt for approval
    response = display.prompt_approval(plan)

    if response == "y":
        return plan, True
    elif response == "edit":
        display.console.print(
            "[yellow]Edit mode not yet implemented. "
            "Please re-run with modified candidates.[/yellow]"
        )
        return plan, False
    else:
        return plan, False


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
