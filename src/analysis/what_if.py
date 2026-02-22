"""What-if analysis for trade opportunity combinations.

This module analyzes the impact of approving combinations of opportunities
to help users understand risk exposure before execution.
"""

from collections import Counter
from dataclasses import dataclass, field

from loguru import logger

from src.strategies.base import TradeOpportunity


@dataclass
class WhatIfResult:
    """Result of what-if analysis.

    Attributes:
        approved_count: Number of opportunities being analyzed
        total_premium: Total premium that would be collected
        total_margin: Total margin that would be required
        current_positions: Current number of open positions
        new_total_positions: Total positions after approval
        position_limit: Maximum allowed positions
        exceeds_position_limit: True if would exceed position limit
        sector_concentration: Dict of sector to count
        exceeds_sector_limit: True if any sector exceeds limit
        margin_utilization_pct: Percentage of margin that would be used
        exceeds_margin_limit: True if would exceed margin limit
        warnings: List of warning messages
        details: Additional analysis details
    """

    approved_count: int
    total_premium: float
    total_margin: float
    current_positions: int
    new_total_positions: int
    position_limit: int
    exceeds_position_limit: bool
    sector_concentration: dict[str, int]
    exceeds_sector_limit: bool
    margin_utilization_pct: float
    exceeds_margin_limit: bool
    warnings: list[str] = field(default_factory=list)
    details: dict[str, any] = field(default_factory=dict)


class WhatIfAnalyzer:
    """Analyze impact of approving combinations of opportunities.

    Use case: "If I approve #1 and #3, will I hit max positions?"
    """

    def __init__(
        self,
        max_positions: int = 10,
        max_sector_concentration: int = 3,
        max_margin_pct: float = 0.80,  # 80% of available margin
        total_available_margin: float = 50000.0,  # $50k default
    ):
        """Initialize what-if analyzer.

        Args:
            max_positions: Maximum allowed positions
            max_sector_concentration: Maximum positions per sector
            max_margin_pct: Maximum margin utilization percentage
            total_available_margin: Total available margin in dollars
        """
        self.max_positions = max_positions
        self.max_sector_concentration = max_sector_concentration
        self.max_margin_pct = max_margin_pct
        self.total_available_margin = total_available_margin

    def analyze_selections(
        self,
        opportunities: list[TradeOpportunity],
        selected_indices: list[int],
        current_positions: int = 0,
        current_margin_used: float = 0.0,
    ) -> WhatIfResult:
        """Analyze what happens if user approves selected opportunities.

        Args:
            opportunities: List of all opportunities
            selected_indices: List of indices (0-based) user wants to approve
            current_positions: Current number of open positions
            current_margin_used: Current margin already in use

        Returns:
            WhatIfResult with analysis details
        """
        # Extract selected opportunities
        selected = [
            opportunities[i] for i in selected_indices if i < len(opportunities)
        ]

        if not selected:
            return self._empty_result(current_positions, current_margin_used)

        # Calculate totals
        total_premium = sum(opp.premium * 100 * opp.contracts for opp in selected)
        total_margin = sum(opp.margin_required * opp.contracts for opp in selected)

        new_total_positions = current_positions + len(selected)
        new_margin_used = current_margin_used + total_margin

        # Analyze position limit
        exceeds_position_limit = new_total_positions > self.max_positions

        # Analyze sector concentration
        sector_counts = Counter()
        for opp in selected:
            sector = opp.sector or "Unknown"
            sector_counts[sector] += 1

        exceeds_sector_limit = any(
            count > self.max_sector_concentration for count in sector_counts.values()
        )

        # Analyze margin utilization
        margin_utilization_pct = (
            (new_margin_used / self.total_available_margin) * 100
            if self.total_available_margin > 0
            else 0.0
        )
        exceeds_margin_limit = margin_utilization_pct > (self.max_margin_pct * 100)

        # Generate warnings
        warnings = []

        if exceeds_position_limit:
            warnings.append(
                f"⚠️  Would exceed position limit: {new_total_positions} > {self.max_positions}"
            )

        if exceeds_sector_limit:
            for sector, count in sector_counts.items():
                if count > self.max_sector_concentration:
                    warnings.append(
                        f"⚠️  Would exceed sector limit for {sector}: {count} > {self.max_sector_concentration}"
                    )

        if exceeds_margin_limit:
            warnings.append(
                f"⚠️  Would exceed margin limit: {margin_utilization_pct:.1f}% > {self.max_margin_pct*100:.0f}%"
            )

        # Additional safety warnings
        if margin_utilization_pct > 50 and not exceeds_margin_limit:
            warnings.append(
                f"ℹ️  High margin utilization: {margin_utilization_pct:.1f}% of available margin"
            )

        if new_total_positions > self.max_positions * 0.8:
            warnings.append(
                f"ℹ️  Approaching position limit: {new_total_positions}/{self.max_positions}"
            )

        # Build result
        result = WhatIfResult(
            approved_count=len(selected),
            total_premium=total_premium,
            total_margin=total_margin,
            current_positions=current_positions,
            new_total_positions=new_total_positions,
            position_limit=self.max_positions,
            exceeds_position_limit=exceeds_position_limit,
            sector_concentration=dict(sector_counts),
            exceeds_sector_limit=exceeds_sector_limit,
            margin_utilization_pct=margin_utilization_pct,
            exceeds_margin_limit=exceeds_margin_limit,
            warnings=warnings,
            details={
                "selected_symbols": [opp.symbol for opp in selected],
                "avg_premium": total_premium / len(selected) if selected else 0.0,
                "avg_margin": total_margin / len(selected) if selected else 0.0,
                "current_margin_used": current_margin_used,
                "new_margin_used": new_margin_used,
                "available_margin_remaining": self.total_available_margin
                - new_margin_used,
            },
        )

        logger.info(
            "What-if analysis completed",
            extra={
                "approved_count": len(selected),
                "total_premium": total_premium,
                "total_margin": total_margin,
                "warnings": len(warnings),
            },
        )

        return result

    def _empty_result(
        self, current_positions: int, current_margin_used: float
    ) -> WhatIfResult:
        """Create empty result for no selections.

        Args:
            current_positions: Current number of open positions
            current_margin_used: Current margin in use

        Returns:
            WhatIfResult with zero values
        """
        margin_utilization_pct = (
            (current_margin_used / self.total_available_margin) * 100
            if self.total_available_margin > 0
            else 0.0
        )

        return WhatIfResult(
            approved_count=0,
            total_premium=0.0,
            total_margin=0.0,
            current_positions=current_positions,
            new_total_positions=current_positions,
            position_limit=self.max_positions,
            exceeds_position_limit=False,
            sector_concentration={},
            exceeds_sector_limit=False,
            margin_utilization_pct=margin_utilization_pct,
            exceeds_margin_limit=False,
            warnings=[],
            details={
                "current_margin_used": current_margin_used,
                "available_margin_remaining": self.total_available_margin
                - current_margin_used,
            },
        )

    def format_result(self, result: WhatIfResult) -> str:
        """Format what-if result for display.

        Args:
            result: WhatIfResult to format

        Returns:
            Formatted string for console output
        """
        lines = []
        lines.append("\n[bold cyan]What-If Analysis:[/bold cyan]")

        if result.approved_count == 0:
            lines.append("[yellow]No opportunities selected[/yellow]")
            return "\n".join(lines)

        lines.append(
            f"  • Opportunities: {result.approved_count} (symbols: {', '.join(result.details['selected_symbols'])})"
        )
        lines.append(f"  • Total premium: ${result.total_premium:.2f}")
        lines.append(f"  • Total margin required: ${result.total_margin:.2f}")
        lines.append(
            f"  • Positions: {result.current_positions} → {result.new_total_positions} (limit: {result.position_limit})"
        )
        lines.append(
            f"  • Margin utilization: {result.margin_utilization_pct:.1f}% (limit: {self.max_margin_pct*100:.0f}%)"
        )

        if result.sector_concentration:
            lines.append(
                f"  • Sector concentration: {', '.join(f'{s}={c}' for s, c in result.sector_concentration.items())}"
            )

        if result.warnings:
            lines.append("\n[bold yellow]Warnings:[/bold yellow]")
            for warning in result.warnings:
                lines.append(f"  {warning}")

        return "\n".join(lines)
