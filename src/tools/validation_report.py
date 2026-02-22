"""Validation report for IBKR option validation.

Provides detailed diagnostics when validation fails, including:
- Per-candidate rejection reasons with actual values
- Summary statistics of your data
- Recommended configuration values
"""

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table


@dataclass
class RejectedCandidate:
    """A candidate that failed IBKR validation."""

    symbol: str
    strike: float
    expiration: str
    dte: int
    rejection_reason: str
    spread_pct: Optional[float] = None
    margin_efficiency: Optional[float] = None
    trend: Optional[str] = None
    stock_price: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None


@dataclass
class ValidationReport:
    """Report of IBKR validation results with diagnostics."""

    timestamp: datetime = field(default_factory=datetime.now)
    total_candidates: int = 0
    passed_count: int = 0
    rejected_candidates: list[RejectedCandidate] = field(default_factory=list)

    # Rejection counts by reason
    rejected_no_data: int = 0
    rejected_no_stock_price: int = 0
    rejected_no_option_quotes: int = 0
    rejected_spread: int = 0
    rejected_margin: int = 0
    rejected_trend: int = 0
    rejected_iv_rank: int = 0

    # Current config thresholds
    max_spread_pct: float = 0.20
    min_margin_efficiency: float = 0.02
    require_uptrend: bool = True

    def add_rejection(
        self,
        symbol: str,
        strike: float,
        expiration: str,
        dte: int,
        reason: str,
        spread_pct: Optional[float] = None,
        margin_efficiency: Optional[float] = None,
        trend: Optional[str] = None,
        stock_price: Optional[float] = None,
        bid: Optional[float] = None,
        ask: Optional[float] = None,
    ) -> None:
        """Add a rejected candidate to the report.

        Args:
            symbol: Stock symbol
            strike: Strike price
            expiration: Expiration date
            dte: Days to expiration
            reason: Rejection reason
            spread_pct: Bid-ask spread percentage
            margin_efficiency: Margin efficiency percentage
            trend: Trend classification
            stock_price: Current stock price
            bid: Option bid price
            ask: Option ask price
        """
        candidate = RejectedCandidate(
            symbol=symbol,
            strike=strike,
            expiration=expiration,
            dte=dte,
            rejection_reason=reason,
            spread_pct=spread_pct,
            margin_efficiency=margin_efficiency,
            trend=trend,
            stock_price=stock_price,
            bid=bid,
            ask=ask,
        )
        self.rejected_candidates.append(candidate)

        # Update counts
        if reason == "no_data":
            self.rejected_no_data += 1
        elif reason == "no_stock_price":
            self.rejected_no_stock_price += 1
        elif reason == "no_option_quotes":
            self.rejected_no_option_quotes += 1
        elif reason == "spread":
            self.rejected_spread += 1
        elif reason == "margin":
            self.rejected_margin += 1
        elif reason == "trend":
            self.rejected_trend += 1
        elif reason == "iv_rank":
            self.rejected_iv_rank += 1

    def get_statistics(self) -> dict:
        """Calculate statistics from rejected candidates.

        Returns:
            dict: Statistics including min, max, median, percentiles
        """
        # Extract valid values
        spreads = [
            c.spread_pct for c in self.rejected_candidates if c.spread_pct is not None
        ]
        margins = [
            c.margin_efficiency
            for c in self.rejected_candidates
            if c.margin_efficiency is not None
        ]

        stats = {
            "spreads": {
                "count": len(spreads),
                "min": min(spreads) if spreads else None,
                "max": max(spreads) if spreads else None,
                "median": sorted(spreads)[len(spreads) // 2] if spreads else None,
                "p80": sorted(spreads)[int(len(spreads) * 0.8)] if spreads else None,
            },
            "margins": {
                "count": len(margins),
                "min": min(margins) if margins else None,
                "max": max(margins) if margins else None,
                "median": sorted(margins)[len(margins) // 2] if margins else None,
                "p20": sorted(margins)[int(len(margins) * 0.2)] if margins else None,
            },
        }

        return stats

    def get_recommendations(self) -> dict:
        """Generate recommended configuration values based on data.

        Returns:
            dict: Recommended config values with explanations
        """
        stats = self.get_statistics()
        recommendations = {}

        # Spread recommendation
        if stats["spreads"]["p80"] is not None:
            recommended_spread = stats["spreads"]["p80"] * 1.1  # 10% buffer
            if recommended_spread > self.max_spread_pct:
                recommendations["MAX_SPREAD_PCT"] = {
                    "current": self.max_spread_pct,
                    "recommended": round(recommended_spread, 2),
                    "reason": f"80% of your data has spreads ≤ {stats['spreads']['p80']:.1%}",
                }

        # Margin efficiency recommendation
        if stats["margins"]["p20"] is not None:
            recommended_margin = stats["margins"]["p20"] * 0.9  # 10% safety margin
            if recommended_margin < self.min_margin_efficiency:
                recommendations["MIN_MARGIN_EFFICIENCY"] = {
                    "current": self.min_margin_efficiency,
                    "recommended": round(recommended_margin, 3),
                    "reason": f"20% of your data has margin efficiency ≥ {stats['margins']['p20']:.1%}",
                }

        # Check if current thresholds are way too strict
        if self.rejected_margin > self.passed_count * 3:
            # More than 3x rejections vs passes
            if stats["margins"]["median"] is not None:
                recommendations["MIN_MARGIN_EFFICIENCY"] = {
                    "current": self.min_margin_efficiency,
                    "recommended": round(stats["margins"]["median"] * 0.8, 3),
                    "reason": "Your threshold is rejecting most candidates. Try median - 20%",
                    "urgent": True,
                }

        return recommendations

    def display_summary(self, console: Console) -> None:
        """Display validation report summary in console.

        Args:
            console: Rich console for output
        """
        console.print("\n[bold cyan]═══ Validation Report ═══[/bold cyan]\n")

        # Summary counts
        total_rejected = len(self.rejected_candidates)
        console.print(f"[bold]Validation Results:[/bold]")
        console.print(f"  ✓ Passed:  [green]{self.passed_count}[/green]")
        console.print(f"  ✗ Rejected: [red]{total_rejected}[/red]")
        console.print()

        if total_rejected == 0:
            console.print("[green]All candidates passed validation! 🎉[/green]")
            return

        # Rejection breakdown
        console.print(f"[bold]Rejection Reasons:[/bold]")
        if self.rejected_margin > 0:
            console.print(
                f"  ✗ [red]{self.rejected_margin}[/red] failed: Margin efficiency too low "
                f"(< {self.min_margin_efficiency:.1%})"
            )
        if self.rejected_spread > 0:
            console.print(
                f"  ✗ [red]{self.rejected_spread}[/red] failed: Spread too wide "
                f"(> {self.max_spread_pct:.0%})"
            )
        if self.rejected_trend > 0:
            console.print(
                f"  ✗ [red]{self.rejected_trend}[/red] failed: Not in uptrend "
                f"(require_uptrend={self.require_uptrend})"
            )
        if self.rejected_no_stock_price > 0:
            console.print(
                f"  ✗ [red]{self.rejected_no_stock_price}[/red] failed: Stock price unavailable from IBKR"
            )
            console.print(
                f"     [dim]→ Stock may be halted, delisted, or invalid symbol[/dim]"
            )
        if self.rejected_no_option_quotes > 0:
            console.print(
                f"  ✗ [red]{self.rejected_no_option_quotes}[/red] failed: Option quotes unavailable from IBKR"
            )
            console.print(
                f"     [dim]→ Common causes:[/dim]"
            )
            console.print(
                f"     [dim]  • Options market closed (check trading hours)[/dim]"
            )
            console.print(
                f"     [dim]  • Contract doesn't exist (invalid strike/expiration)[/dim]"
            )
            console.print(
                f"     [dim]  • Extreme illiquidity (no market makers)[/dim]"
            )
        if self.rejected_iv_rank > 0:
            console.print(
                f"  ✗ [red]{self.rejected_iv_rank}[/red] failed: IV Rank too low "
                f"(premiums cheap relative to stock's history)"
            )
        if self.rejected_no_data > 0:
            console.print(
                f"  ✗ [red]{self.rejected_no_data}[/red] failed: No IBKR data available (generic)"
            )
        console.print()

        # Statistics
        stats = self.get_statistics()
        if stats["spreads"]["count"] > 0 or stats["margins"]["count"] > 0:
            console.print(f"[bold]Your Data Ranges:[/bold]")

            if stats["spreads"]["count"] > 0:
                console.print(
                    f"  Spread %:           "
                    f"[cyan]{stats['spreads']['min']:.1%}[/cyan] to "
                    f"[cyan]{stats['spreads']['max']:.1%}[/cyan] "
                    f"(median: {stats['spreads']['median']:.1%})"
                )

            if stats["margins"]["count"] > 0:
                console.print(
                    f"  Margin Efficiency:  "
                    f"[cyan]{stats['margins']['min']:.1%}[/cyan] to "
                    f"[cyan]{stats['margins']['max']:.1%}[/cyan] "
                    f"(median: {stats['margins']['median']:.1%})"
                )
            console.print()

        # Current config
        console.print(f"[bold]Current Thresholds:[/bold]")
        console.print(
            f"  MAX_SPREAD_PCT={self.max_spread_pct:.2f}  "
            f"({self.max_spread_pct:.0%})"
        )
        console.print(
            f"  MIN_MARGIN_EFFICIENCY={self.min_margin_efficiency:.3f}  "
            f"({self.min_margin_efficiency:.1%})"
        )
        console.print(f"  REQUIRE_UPTREND={self.require_uptrend}")
        console.print()

        # Recommendations
        recommendations = self.get_recommendations()
        if recommendations:
            console.print("[bold yellow]💡 Recommendations:[/bold yellow]")

            for key, rec in recommendations.items():
                if rec.get("urgent"):
                    console.print(
                        f"  [bold red]⚠ URGENT:[/bold red] Change [cyan]{key}={rec['recommended']}[/cyan]  "
                        f"(currently {rec['current']:.3f})"
                    )
                else:
                    console.print(
                        f"  💡 Consider [cyan]{key}={rec['recommended']}[/cyan]  "
                        f"(currently {rec['current']:.3f})"
                    )
                console.print(f"     [dim]{rec['reason']}[/dim]")

            console.print()
            console.print(
                "[dim]Edit these values in your .env file, then re-run the scan.[/dim]"
            )
        else:
            console.print(
                "[dim]Your thresholds look reasonable based on the data.[/dim]"
            )

    def display_rejected_table(self, console: Console, limit: int = 20) -> None:
        """Display table of rejected candidates.

        Args:
            console: Rich console for output
            limit: Maximum number of rows to display
        """
        if not self.rejected_candidates:
            return

        console.print(
            f"\n[bold cyan]Top {min(limit, len(self.rejected_candidates))} Rejected Candidates[/bold cyan]\n"
        )

        table = Table()
        table.add_column("Symbol", style="cyan")
        table.add_column("Strike", justify="right")
        table.add_column("Exp", style="dim")
        table.add_column("DTE", justify="right")
        table.add_column("Reason", style="yellow")
        table.add_column("Spread %", justify="right")
        table.add_column("Margin Eff %", justify="right")
        table.add_column("Trend", style="dim")

        for candidate in self.rejected_candidates[:limit]:
            spread_str = f"{candidate.spread_pct:.1%}" if candidate.spread_pct else "—"
            margin_str = (
                f"{candidate.margin_efficiency:.2%}"
                if candidate.margin_efficiency
                else "—"
            )

            # Color code the problematic value
            reason_display = candidate.rejection_reason
            if candidate.rejection_reason == "spread":
                reason_display = "Spread"
                spread_str = f"[red]{spread_str}[/red]"
            elif candidate.rejection_reason == "margin":
                reason_display = "Margin"
                margin_str = f"[red]{margin_str}[/red]"
            elif candidate.rejection_reason == "trend":
                reason_display = "Trend"
            elif candidate.rejection_reason == "no_data":
                reason_display = "No Data"
            elif candidate.rejection_reason == "no_stock_price":
                reason_display = "No Stock Price"
            elif candidate.rejection_reason == "no_option_quotes":
                reason_display = "No Option Quotes"
            elif candidate.rejection_reason == "iv_rank":
                reason_display = "IV Rank Low"

            table.add_row(
                candidate.symbol,
                f"${candidate.strike:.0f}",
                candidate.expiration[5:],  # Show MM-DD only
                str(candidate.dte),
                reason_display,
                spread_str,
                margin_str,
                candidate.trend or "—",
            )

        console.print(table)

    def save_to_csv(self, filepath: Path) -> None:
        """Save detailed rejection report to CSV file.

        Args:
            filepath: Path to save CSV file
        """
        import csv

        filepath.parent.mkdir(parents=True, exist_ok=True)

        with open(filepath, "w", newline="") as f:
            writer = csv.writer(f)

            # Header
            writer.writerow(
                [
                    "Symbol",
                    "Strike",
                    "Expiration",
                    "DTE",
                    "Rejection Reason",
                    "Spread %",
                    "Margin Efficiency %",
                    "Trend",
                    "Stock Price",
                    "Option Bid",
                    "Option Ask",
                ]
            )

            # Data rows
            for c in self.rejected_candidates:
                writer.writerow(
                    [
                        c.symbol,
                        c.strike,
                        c.expiration,
                        c.dte,
                        c.rejection_reason,
                        f"{c.spread_pct:.4f}" if c.spread_pct else "",
                        f"{c.margin_efficiency:.4f}" if c.margin_efficiency else "",
                        c.trend or "",
                        f"{c.stock_price:.2f}" if c.stock_price else "",
                        f"{c.bid:.2f}" if c.bid else "",
                        f"{c.ask:.2f}" if c.ask else "",
                    ]
                )
