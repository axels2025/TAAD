"""Enhanced trade presentation and batch approval interface.

This module provides rich presentation of trade opportunities and risk-blocked
opportunities, with batch approval capabilities for improved user experience.
"""

from rich import box
from rich.console import Console
from rich.table import Table

from src.strategies.base import TradeOpportunity


class TradePresenter:
    """Enhanced presentation and approval of trade opportunities."""

    def __init__(self, console: Console | None = None):
        """Initialize trade presenter.

        Args:
            console: Rich console for output (creates new one if None)
        """
        self.console = console or Console()

    def present_opportunities(
        self,
        qualified: list[TradeOpportunity],
        risk_blocked: list[tuple[TradeOpportunity, str]] | None = None,
    ) -> list[int]:
        """Present opportunities with batch approval options.

        Shows:
        1. Qualified opportunities (numbered, with details)
        2. Risk-blocked opportunities (with reasons)
        3. Approval options

        Args:
            qualified: List of qualified opportunities
            risk_blocked: List of (opportunity, rejection_reason) tuples

        Returns:
            list[int]: Indices (0-based) of approved opportunities
        """
        if risk_blocked is None:
            risk_blocked = []

        # Show qualified opportunities
        if qualified:
            self._show_qualified_table(qualified)
        else:
            self.console.print(
                "\n[yellow]No qualified opportunities to display[/yellow]\n"
            )

        # Show risk-blocked opportunities
        if risk_blocked:
            self._show_risk_blocked_table(risk_blocked)

        # Get batch approval
        if not qualified:
            return []

        return self._get_batch_approval(len(qualified))

    def _show_qualified_table(self, opportunities: list[TradeOpportunity]) -> None:
        """Display qualified opportunities in rich table.

        Args:
            opportunities: List of qualified opportunities
        """
        table = Table(
            title=f"Qualified Opportunities ({len(opportunities)})",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("#", style="cyan bold", width=3)
        table.add_column("Symbol", style="green bold")
        table.add_column("Strike", justify="right")
        table.add_column("Expiry")
        table.add_column("Premium", justify="right", style="yellow")
        table.add_column("OTM %", justify="right")
        table.add_column("DTE", justify="right")
        table.add_column("Margin Eff.", justify="right", style="magenta")
        table.add_column("Confidence", justify="right")

        for i, opp in enumerate(opportunities, 1):
            # Format margin efficiency for display
            margin_eff_display = (
                f"{opp.margin_efficiency_pct:.1f}%\n{opp.margin_efficiency_ratio}"
                if opp.margin_efficiency_ratio
                else "N/A"
            )

            table.add_row(
                str(i),
                opp.symbol,
                f"${opp.strike:.2f}",
                opp.expiration.strftime("%Y-%m-%d"),
                f"${opp.premium:.2f}",
                f"{opp.otm_pct*100:.1f}%",
                str(opp.dte),
                margin_eff_display,
                f"{opp.confidence*100:.0f}%",
            )

        self.console.print()
        self.console.print(table)
        self.console.print()

    def _show_risk_blocked_table(
        self, blocked: list[tuple[TradeOpportunity, str]]
    ) -> None:
        """Display risk-blocked opportunities with reasons.

        Critical: Users NEED to see what was filtered and why for trust.

        Args:
            blocked: List of (opportunity, rejection_reason) tuples
        """
        self.console.print(
            f"\n[bold yellow]⚠️  Risk-Blocked Opportunities ({len(blocked)})[/bold yellow]"
        )
        self.console.print(
            "[dim]These opportunities were filtered out by risk checks:[/dim]\n"
        )

        table = Table(
            show_header=True,
            box=box.SIMPLE,
            show_edge=False,
        )
        table.add_column("Symbol", style="yellow")
        table.add_column("Strike", justify="right")
        table.add_column("Expiry")
        table.add_column("Blocked Reason", style="red")

        for opp, reason in blocked:
            table.add_row(
                opp.symbol,
                f"${opp.strike:.2f}",
                opp.expiration.strftime("%Y-%m-%d"),
                reason,
            )

        self.console.print(table)
        self.console.print()

    def _get_batch_approval(self, count: int) -> list[int]:
        """Get user approval with batch options.

        Options:
        - 'a' or 'all': Approve all
        - 'n' or 'none': Reject all
        - '1,3,5': Approve specific numbers (comma-separated)
        - '1-5': Approve range
        - 'q': Quit

        Args:
            count: Number of opportunities

        Returns:
            list[int]: Approved indices (0-based)
        """
        self.console.print("[bold]Approval Options:[/bold]")
        self.console.print(
            "  [cyan]a[/cyan] or [cyan]all[/cyan]   - Approve all qualified"
        )
        self.console.print("  [cyan]n[/cyan] or [cyan]none[/cyan]  - Reject all")
        self.console.print(
            "  [cyan]1,3,5[/cyan]     - Approve specific (comma-separated)"
        )
        self.console.print("  [cyan]1-5[/cyan]       - Approve range")
        self.console.print("  [cyan]q[/cyan]         - Quit without executing")

        while True:
            try:
                choice = (
                    self.console.input("\n[bold]Your choice:[/bold] ").strip().lower()
                )

                # Parse choice
                if choice in ("q", "quit"):
                    self.console.print("[yellow]Cancelled by user[/yellow]")
                    return []

                elif choice in ("a", "all"):
                    self.console.print(
                        f"[green]✓ Approved all {count} opportunities[/green]"
                    )
                    return list(range(count))

                elif choice in ("n", "none"):
                    self.console.print("[yellow]Rejected all opportunities[/yellow]")
                    return []

                else:
                    # Parse numbers or ranges
                    indices = self._parse_selection(choice, count)
                    if indices is None:
                        self.console.print(
                            "[red]Invalid input. Please try again.[/red]"
                        )
                        continue

                    if not indices:
                        self.console.print("[yellow]No opportunities selected[/yellow]")
                        return []

                    self.console.print(
                        f"[green]✓ Approved {len(indices)} opportunities: {', '.join(str(i+1) for i in indices)}[/green]"
                    )
                    return indices

            except KeyboardInterrupt:
                self.console.print("\n[yellow]Cancelled by user[/yellow]")
                return []
            except Exception as e:
                self.console.print(f"[red]Error: {e}[/red]")
                continue

    def _parse_selection(self, choice: str, max_count: int) -> list[int] | None:
        """Parse user selection into list of indices.

        Supports:
        - "1,3,5" → [0, 2, 4]
        - "1-5" → [0, 1, 2, 3, 4]
        - "1,3-5,7" → [0, 2, 3, 4, 6]

        Args:
            choice: User input string
            max_count: Maximum valid number

        Returns:
            list[int]: 0-based indices, or None if invalid
        """
        indices = set()

        # Split by comma for individual selections
        parts = choice.split(",")

        for part in parts:
            part = part.strip()

            if not part:
                continue

            # Check for range (e.g., "1-5")
            if "-" in part:
                try:
                    start_str, end_str = part.split("-", 1)
                    start = int(start_str.strip())
                    end = int(end_str.strip())

                    if start < 1 or end > max_count or start > end:
                        return None

                    # Add all numbers in range (convert to 0-based)
                    indices.update(range(start - 1, end))

                except ValueError:
                    return None
            else:
                # Single number
                try:
                    num = int(part)
                    if num < 1 or num > max_count:
                        return None

                    # Convert to 0-based index
                    indices.add(num - 1)

                except ValueError:
                    return None

        return sorted(indices)

    def show_approval_summary(
        self, approved: list[TradeOpportunity], total: int
    ) -> None:
        """Show summary of approved opportunities.

        Args:
            approved: List of approved opportunities
            total: Total number of qualified opportunities
        """
        if not approved:
            self.console.print(
                "[yellow]No opportunities approved for execution[/yellow]"
            )
            return

        self.console.print(
            f"\n[bold green]Approved {len(approved)} of {total} opportunities:[/bold green]\n"
        )

        for i, opp in enumerate(approved, 1):
            self.console.print(
                f"  {i}. {opp.symbol} ${opp.strike:.2f} @ ${opp.premium:.2f}"
            )

        self.console.print()
