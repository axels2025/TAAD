"""Interactive candidate selection for Sunday workflow.

This module provides interactive symbol selection and chart review
functionality for the Sunday-to-Monday trading workflow.

The selection flow:
1. Display symbol summary table (aggregated from individual options)
2. First removal prompt: Remove unwanted symbols before chart review
3. Display trend signals from IBKR/Barchart data
4. Second removal prompt: Remove symbols after manual chart review
5. Return final selection of symbols to proceed with strike finding
"""

from dataclasses import dataclass, field

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from src.scoring.scorer import ScoredCandidate
from src.utils.calc import fmt_pct


@dataclass
class SymbolSummary:
    """Aggregated summary of a symbol's opportunities.

    Aggregates multiple option strikes/expirations for a single symbol
    to provide a symbol-level view for selection.
    """

    symbol: str
    stock_price: float
    option_count: int
    best_otm_pct: float  # Best (highest) OTM% among all options
    best_score: float  # Best composite score
    best_grade: str
    iv_rank: float
    sector: str = "Unknown"
    trend: str = "unknown"

    # Details of the best candidate
    best_strike: float = 0.0
    best_premium: float = 0.0
    best_dte: int = 0

    # All scored candidates for this symbol
    candidates: list[ScoredCandidate] = field(default_factory=list)

    @property
    def is_high_iv(self) -> bool:
        """Check if IV rank is elevated (>60%)."""
        return self.iv_rank > 0.60


@dataclass
class SelectionResult:
    """Result of the interactive selection process."""

    selected_symbols: list[str]
    removed_symbols: list[str]
    symbol_summaries: dict[str, SymbolSummary]
    candidates_by_symbol: dict[str, list[ScoredCandidate]]

    @property
    def total_opportunities(self) -> int:
        """Total number of opportunities across selected symbols."""
        return sum(
            len(self.candidates_by_symbol.get(s, []))
            for s in self.selected_symbols
        )


class InteractiveSelector:
    """Interactive symbol selection and chart review workflow.

    Guides the user through:
    1. Reviewing symbol-level summaries
    2. Removing unwanted symbols (before chart review)
    3. Viewing trend signals
    4. Removing more symbols (after chart review)
    """

    def __init__(self, console: Console | None = None):
        """Initialize the selector.

        Args:
            console: Rich console for output (creates new one if None)
        """
        self.console = console or Console()

    def aggregate_by_symbol(
        self, scored_candidates: list[ScoredCandidate]
    ) -> dict[str, SymbolSummary]:
        """Aggregate scored candidates by symbol.

        Args:
            scored_candidates: List of scored candidates (pre-sorted by score)

        Returns:
            Dictionary mapping symbol to SymbolSummary
        """
        summaries: dict[str, SymbolSummary] = {}

        for sc in scored_candidates:
            symbol = sc.symbol
            candidate = sc.candidate

            if symbol not in summaries:
                # First candidate for this symbol is the best (pre-sorted by score)
                summaries[symbol] = SymbolSummary(
                    symbol=symbol,
                    stock_price=candidate.underlying_price,
                    option_count=0,
                    best_otm_pct=abs(candidate.moneyness_pct),
                    best_score=sc.composite_score,
                    best_grade=sc.grade,
                    iv_rank=candidate.iv_rank,
                    sector="Unknown",  # Will be enriched later if available
                    trend="unknown",
                    best_strike=candidate.strike,
                    best_premium=candidate.bid,
                    best_dte=candidate.dte,
                    candidates=[],
                )

            summary = summaries[symbol]
            summary.option_count += 1
            summary.candidates.append(sc)

            # Update best values
            current_otm = abs(candidate.moneyness_pct)
            if current_otm > summary.best_otm_pct:
                summary.best_otm_pct = current_otm

            if sc.composite_score > summary.best_score:
                summary.best_score = sc.composite_score
                summary.best_grade = sc.grade
                summary.best_strike = candidate.strike
                summary.best_premium = candidate.bid
                summary.best_dte = candidate.dte

        return summaries

    def run_selection(
        self,
        scored_candidates: list[ScoredCandidate],
        trend_data: dict[str, str] | None = None,
        sector_data: dict[str, str] | None = None,
    ) -> SelectionResult:
        """Run the full interactive selection workflow.

        Args:
            scored_candidates: List of scored candidates (pre-sorted by score)
            trend_data: Optional dictionary of symbol -> trend string
            sector_data: Optional dictionary of symbol -> sector/industry string

        Returns:
            SelectionResult with selected and removed symbols
        """
        # Aggregate by symbol
        summaries = self.aggregate_by_symbol(scored_candidates)

        # Apply trend data if provided
        if trend_data:
            for symbol, trend in trend_data.items():
                if symbol in summaries:
                    summaries[symbol].trend = trend

        # Apply sector data if provided
        if sector_data:
            for symbol, sector in sector_data.items():
                if symbol in summaries:
                    summaries[symbol].sector = sector

        # Sort symbols by best score (descending)
        sorted_symbols = sorted(
            summaries.keys(),
            key=lambda s: summaries[s].best_score,
            reverse=True,
        )

        total_options = sum(s.option_count for s in summaries.values())

        # Display header
        self._display_header(len(summaries), total_options)

        # Display symbol summary table
        self._display_symbol_table(sorted_symbols, summaries)

        # Show high IV warnings
        self._display_high_iv_warning(summaries)

        # First removal prompt
        removed_before = self._prompt_symbol_removal(
            sorted_symbols,
            prompt_message="Remove any symbols? (e.g., \"CRWV,SLV\" or Enter to keep all):",
        )

        remaining_symbols = [s for s in sorted_symbols if s not in removed_before]

        if not remaining_symbols:
            self.console.print("[yellow]All symbols removed. No selection to proceed with.[/yellow]")
            return SelectionResult(
                selected_symbols=[],
                removed_symbols=sorted_symbols,
                symbol_summaries=summaries,
                candidates_by_symbol=self._get_candidates_by_symbol(scored_candidates),
            )

        self.console.print(
            f"\n[green]Remaining: {len(remaining_symbols)} symbols "
            f"({self._count_opportunities(remaining_symbols, summaries)} opportunities)[/green]\n"
        )

        # Display trend signals for remaining symbols
        self._display_trend_signals(remaining_symbols, summaries)

        # Manual review pause
        self._display_chart_review_notice()

        # Second removal prompt (after chart review)
        removed_after = self._prompt_symbol_removal(
            remaining_symbols,
            prompt_message="Remove more symbols after chart review? (Enter to proceed):",
        )

        final_symbols = [s for s in remaining_symbols if s not in removed_after]
        all_removed = list(set(removed_before) | set(removed_after))

        if not final_symbols:
            self.console.print("[yellow]All symbols removed. No selection to proceed with.[/yellow]")
            return SelectionResult(
                selected_symbols=[],
                removed_symbols=sorted_symbols,
                symbol_summaries=summaries,
                candidates_by_symbol=self._get_candidates_by_symbol(scored_candidates),
            )

        # Final confirmation
        self._display_final_selection(final_symbols, summaries)

        proceed = self._confirm_proceed()

        if not proceed:
            return SelectionResult(
                selected_symbols=[],
                removed_symbols=sorted_symbols,
                symbol_summaries=summaries,
                candidates_by_symbol=self._get_candidates_by_symbol(scored_candidates),
            )

        return SelectionResult(
            selected_symbols=final_symbols,
            removed_symbols=all_removed,
            symbol_summaries=summaries,
            candidates_by_symbol=self._get_candidates_by_symbol(scored_candidates),
        )

    def _display_header(self, symbol_count: int, opportunity_count: int) -> None:
        """Display the selection header."""
        header = Panel(
            f"[bold]INTERACTIVE SELECTION[/bold] — {opportunity_count} opportunities across {symbol_count} symbols",
            box=box.DOUBLE,
            style="cyan",
        )
        self.console.print()
        self.console.print(header)
        self.console.print()

    def _display_symbol_table(
        self, symbols: list[str], summaries: dict[str, SymbolSummary]
    ) -> None:
        """Display the symbol summary table."""
        table = Table(
            title="SYMBOL SUMMARY (sorted by composite score)",
            box=box.ROUNDED,
            show_header=True,
            header_style="bold cyan",
        )

        table.add_column("#", style="cyan bold", width=4)
        table.add_column("Symbol", style="green bold", width=8)
        table.add_column("Sector", style="dim", width=10)
        table.add_column("Strike", justify="right", width=8)
        table.add_column("Premium", justify="right", width=8)
        table.add_column("DTE", justify="right", width=5)
        table.add_column("OTM%", justify="right", width=8)
        table.add_column("IV Rank", justify="right", width=8)
        table.add_column("Score/Grade", justify="right", width=12)

        for i, symbol in enumerate(symbols, 1):
            summary = summaries[symbol]

            # Format IV rank with warning color if high
            iv_display = f"{summary.iv_rank * 100:.1f}%"
            if summary.is_high_iv:
                iv_display = f"[yellow]{iv_display}[/yellow]"

            # Format score/grade
            score_display = f"{summary.best_score:.1f} / {summary.best_grade}"

            # Format sector (truncate if too long)
            sector_display = summary.sector[:10] if len(summary.sector) > 10 else summary.sector

            table.add_row(
                str(i),
                symbol,
                sector_display,
                f"${summary.best_strike:.2f}",
                f"${summary.best_premium:.2f}",
                str(summary.best_dte),
                fmt_pct(summary.best_otm_pct),
                iv_display,
                score_display,
            )

        self.console.print(table)
        self.console.print()

    def _display_high_iv_warning(self, summaries: dict[str, SymbolSummary]) -> None:
        """Display warning for high IV rank symbols."""
        high_iv_symbols = [
            (s, summary.iv_rank)
            for s, summary in summaries.items()
            if summary.is_high_iv
        ]

        if high_iv_symbols:
            warning_text = ", ".join(
                f"{s} ({iv * 100:.1f}%)" for s, iv in high_iv_symbols
            )
            self.console.print(
                f"[yellow]ℹ  High IV Rank (>60%):[/yellow] {warning_text}"
            )
            self.console.print(
                "[dim]   Consider if IV is elevated due to earnings or events.[/dim]"
            )
            self.console.print()

    def _prompt_symbol_removal(
        self, available_symbols: list[str], prompt_message: str
    ) -> list[str]:
        """Prompt user to remove symbols.

        Args:
            available_symbols: Currently available symbols
            prompt_message: Message to display

        Returns:
            List of symbols to remove
        """
        try:
            self.console.print(f"[bold]{prompt_message}[/bold]")
            user_input = self.console.input("> ").strip()

            if not user_input:
                return []

            # Parse comma-separated symbols
            requested = [s.strip().upper() for s in user_input.split(",")]

            # Validate symbols exist
            valid_removals = []
            invalid = []
            for symbol in requested:
                if symbol in available_symbols:
                    valid_removals.append(symbol)
                else:
                    invalid.append(symbol)

            if invalid:
                self.console.print(
                    f"[yellow]Warning: Symbols not found (ignored): {', '.join(invalid)}[/yellow]"
                )

            if valid_removals:
                self.console.print(
                    f"[green]Removed {len(valid_removals)} symbol(s): {', '.join(valid_removals)}[/green]"
                )

            return valid_removals

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Selection cancelled[/yellow]")
            return available_symbols  # Remove all to cancel

    def _display_trend_signals(
        self, symbols: list[str], summaries: dict[str, SymbolSummary]
    ) -> None:
        """Display trend signals for symbols."""
        self.console.print("[bold]App trend signals:[/bold]")

        for symbol in symbols:
            summary = summaries[symbol]
            trend = summary.trend.lower()

            # Format trend with appropriate styling
            if trend == "uptrend":
                trend_display = "[green]Uptrend  (price > SMA20 > SMA50) ✓[/green]"
            elif trend == "sideways":
                trend_display = "[yellow]Sideways (price near SMA20, above SMA50) ✓[/yellow]"
            elif trend == "downtrend":
                trend_display = "[red]Downtrend (price < SMA20 < SMA50) ⚠[/red]"
            else:
                trend_display = "[dim]Unknown (no trend data)[/dim]"

            self.console.print(f"  {symbol:8s} → {trend_display}")

        self.console.print()

    def _display_chart_review_notice(self) -> None:
        """Display the manual chart review notice."""
        notice = Panel(
            "[bold yellow]⚠ MANUAL REVIEW REQUIRED[/bold yellow]\n\n"
            "Open your charting tool (ThinkorSwim/Tastyworks) and review each symbol for:\n"
            "  • Bearish patterns (H&S, double top, breakdown)\n"
            "  • Support/resistance levels relative to target strikes\n"
            "  • Recent trend direction (app signals shown above)",
            box=box.ROUNDED,
            border_style="yellow",
        )
        self.console.print(notice)
        self.console.print()

    def _display_final_selection(
        self, symbols: list[str], summaries: dict[str, SymbolSummary]
    ) -> None:
        """Display the final selection summary."""
        total_opportunities = self._count_opportunities(symbols, summaries)

        self.console.print(
            f"\n[bold green]Final selection: {len(symbols)} symbols "
            f"({total_opportunities} opportunities)[/bold green]\n"
        )

        # Show selected symbols in a compact format
        symbol_list = ", ".join(symbols[:10])
        if len(symbols) > 10:
            symbol_list += f", ... (+{len(symbols) - 10} more)"
        self.console.print(f"  Symbols: {symbol_list}\n")

    def _confirm_proceed(self) -> bool:
        """Confirm proceeding to strike finding."""
        try:
            choice = self.console.input(
                "[bold]Proceed to strike finding? [Y/n]:[/bold] "
            ).strip().lower()

            if choice in ("", "y", "yes"):
                self.console.print("[green]Proceeding to strike finding...[/green]")
                return True
            else:
                self.console.print("[yellow]Selection cancelled[/yellow]")
                return False

        except KeyboardInterrupt:
            self.console.print("\n[yellow]Selection cancelled[/yellow]")
            return False

    def _count_opportunities(
        self, symbols: list[str], summaries: dict[str, SymbolSummary]
    ) -> int:
        """Count total opportunities for given symbols."""
        return sum(summaries[s].option_count for s in symbols if s in summaries)

    def _get_candidates_by_symbol(
        self, scored_candidates: list[ScoredCandidate]
    ) -> dict[str, list[ScoredCandidate]]:
        """Group candidates by symbol."""
        by_symbol: dict[str, list[ScoredCandidate]] = {}
        for sc in scored_candidates:
            symbol = sc.symbol
            if symbol not in by_symbol:
                by_symbol[symbol] = []
            by_symbol[symbol].append(sc)
        return by_symbol


def run_interactive_selection(
    scored_candidates: list[ScoredCandidate],
    trend_data: dict[str, str] | None = None,
    sector_data: dict[str, str] | None = None,
    console: Console | None = None,
) -> SelectionResult:
    """Convenience function to run interactive selection.

    Args:
        scored_candidates: List of scored candidates
        trend_data: Optional trend data by symbol
        sector_data: Optional sector/industry data by symbol
        console: Optional console for output

    Returns:
        SelectionResult with selection outcome
    """
    selector = InteractiveSelector(console=console)
    return selector.run_selection(scored_candidates, trend_data, sector_data)
