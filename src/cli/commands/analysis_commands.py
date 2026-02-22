"""CLI display logic for AI-powered performance analysis.

Provides the display functions called from the main CLI when
--ai or --ask flags are used with the analyse command.
"""

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from sqlalchemy.orm import Session

from src.agents.data_aggregator import DataAggregator
from src.agents.models import AnalysisDepth, AnalysisInsight, AnalysisReport
from src.agents.performance_analyzer import PerformanceAnalyzer

console = Console()

# Map confidence to color
_CONFIDENCE_COLORS = {
    "high": "green",
    "medium": "yellow",
    "low": "dim",
}

# Map category to emoji-free label
_CATEGORY_LABELS = {
    "recommendation": "RECOMMEND",
    "risk": "RISK",
    "hypothesis": "HYPOTHESIS",
    "observation": "NOTE",
}


def run_ai_analysis(
    session: Session,
    days: int = 90,
    depth: str = "standard",
    question: str | None = None,
    account_id: str | None = None,
) -> None:
    """Run AI-powered performance analysis and display results.

    Args:
        session: Database session
        days: Number of days of history to analyse
        depth: Analysis depth ("quick", "standard", "deep")
        question: Optional specific question to ask
        account_id: Optional IBKR account ID to filter trades
    """
    depth_enum = AnalysisDepth(depth)

    # Build context
    account_label = f" (account: {account_id})" if account_id else ""
    console.print(f"[dim]Aggregating {days} days of trading data{account_label}...[/dim]")
    aggregator = DataAggregator(session)
    context = aggregator.build_context(
        days=days,
        depth=depth_enum,
        user_question=question,
        account_id=account_id,
    )

    if context.performance.total_trades == 0:
        console.print(
            f"[yellow]No closed trades in the last {days} days. "
            "Nothing to analyse.[/yellow]"
        )
        return

    console.print(
        f"[dim]Analysing {context.performance.total_trades} trades, "
        f"{len(context.patterns)} patterns, "
        f"{len(context.breakdowns)} dimensions...[/dim]"
    )

    # Run analysis
    analyzer = PerformanceAnalyzer(depth=depth_enum)
    report = analyzer.analyze(context)

    # Display results
    _display_report(report, days)


def _display_report(report: AnalysisReport, days: int) -> None:
    """Display the analysis report with Rich formatting.

    Args:
        report: The analysis report from Claude
        days: Analysis period in days (for display)
    """
    console.print()

    # Narrative
    console.print(Panel(
        report.narrative,
        title=f"AI Performance Analysis ({days}d, {report.depth.value})",
        border_style="blue",
        padding=(1, 2),
    ))

    # Insights table
    if report.insights:
        console.print()
        for insight in report.insights:
            _display_insight(insight)

    # Cost footer
    console.print()
    console.print(
        f"[dim]Model: {report.model_used} | "
        f"Tokens: {report.input_tokens:,} in / {report.output_tokens:,} out | "
        f"Cost: ${report.cost_estimate:.4f}[/dim]"
    )


def _display_insight(insight: AnalysisInsight) -> None:
    """Display a single insight as a Rich panel.

    Args:
        insight: The insight to display
    """
    category_label = _CATEGORY_LABELS.get(insight.category, insight.category.upper())
    conf_color = _CONFIDENCE_COLORS.get(insight.confidence, "dim")

    title = (
        f"[{conf_color}]#{insight.priority}[/{conf_color}] "
        f"[bold]{category_label}[/bold]: {insight.title}"
    )

    # Build body with metadata
    body_parts = [insight.body]

    if insight.related_patterns:
        patterns_str = ", ".join(insight.related_patterns)
        body_parts.append(f"\n[dim]Related patterns: {patterns_str}[/dim]")

    body = "\n".join(body_parts)

    border = "green" if insight.category == "recommendation" else (
        "red" if insight.category == "risk" else "cyan"
    )

    console.print(Panel(
        body,
        title=title,
        border_style=border,
        padding=(0, 1),
    ))
