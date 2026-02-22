"""Sunday session combined CLI command.

This module provides the sunday-session command that chains together
all Sunday workflow steps into a single interactive session:

1. Screen for candidates (scan command)
2. Score and rank opportunities (score command)
3. Interactive selection (select command)
4. Build portfolio with margin check (build-portfolio command)
5. Stage trades for Monday execution

The session maintains state throughout and provides a cohesive workflow.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any

from loguru import logger
from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.table import Table

from src.data.opportunity_state import OpportunityState
from src.scoring.scorer import NakedPutScorer, ScoredCandidate
from src.cli.commands.interactive_selection import InteractiveSelector, SelectionResult
from src.services.strike_finder import StrikeFinder, StrikePreferences
from src.services.portfolio_builder import PortfolioBuilder, PortfolioConfig
from src.services.limit_price_calculator import LimitPriceCalculator


@dataclass
class SundaySessionConfig:
    """Configuration for Sunday session workflow.

    Attributes:
        margin_budget: Maximum margin to use for new positions (fallback if dynamic calc fails)
        account_usage_pct: Percentage of NetLiquidation to use (e.g., 0.20 = 20%)
        max_positions: Maximum number of new positions
        max_sector_concentration: Maximum percentage in any sector (0.0-1.0)
        min_otm_pct: Minimum OTM percentage filter
        min_premium: Minimum premium filter
        use_live_margin: Whether to fetch actual margin from IBKR
        use_dynamic_budget: Whether to calculate budget from account value
        auto_stage: Whether to auto-stage selected trades
    """

    margin_budget: float = 50000.0
    account_usage_pct: float = 0.20
    max_positions: int = 10
    max_sector_concentration: float = 0.40
    min_otm_pct: float = 0.12
    min_premium: float = 0.30
    use_live_margin: bool = True
    use_dynamic_budget: bool = True
    auto_stage: bool = False

    @classmethod
    def from_env(cls) -> "SundaySessionConfig":
        """Load configuration from the central Config singleton.

        Shared values (margin budget, max positions, premium) come from
        ``get_config()`` so there is one source of truth.  Sunday-specific
        booleans stay as ``os.getenv`` since they are only used here.
        """
        import os

        from src.config.base import get_config

        cfg = get_config()
        return cls(
            margin_budget=cfg.margin_budget_default,
            account_usage_pct=cfg.margin_budget_pct,
            max_positions=cfg.max_positions,
            max_sector_concentration=float(cfg.max_sector_count),
            min_otm_pct=float(os.getenv("OTM_MIN_PCT", "0.15")),
            min_premium=cfg.premium_min,
            use_live_margin=os.getenv("SUNDAY_USE_LIVE_MARGIN", "true").lower() == "true",
            use_dynamic_budget=os.getenv("USE_DYNAMIC_BUDGET", "true").lower() == "true",
            auto_stage=os.getenv("SUNDAY_AUTO_STAGE", "false").lower() == "true",
        )


@dataclass
class SundayStagedTrade:
    """A trade staged from Sunday session.

    Simplified trade representation for the Sunday session workflow.
    """

    id: int
    symbol: str
    strike: float
    expiration: str
    contracts: int
    limit_price: float
    margin_required: float
    expected_premium: float
    sector: str = "Unknown"
    state: OpportunityState = OpportunityState.STAGED


@dataclass
class SundayPortfolioPlan:
    """Portfolio plan from Sunday session.

    Contains the list of trades and summary metrics.
    """

    trades: list[SundayStagedTrade] = field(default_factory=list)
    total_margin: float = 0.0
    expected_premium: float = 0.0
    margin_budget: float = 50000.0

    @property
    def margin_utilization(self) -> float:
        """Calculate margin utilization as a fraction."""
        if self.margin_budget <= 0:
            return 0.0
        return self.total_margin / self.margin_budget


@dataclass
class SundaySessionResult:
    """Result of a Sunday session workflow.

    Attributes:
        session_id: Unique session identifier (week_of_YYYY-MM-DD)
        started_at: When the session started
        completed_at: When the session completed
        candidates_screened: Number of candidates from screening
        opportunities_scored: Number of opportunities scored
        opportunities_selected: Number selected by user
        trades_staged: Number of trades staged for Monday
        portfolio_plan: The final portfolio plan
        total_margin_required: Total margin for staged trades
        total_expected_premium: Total expected premium
        warnings: Any warnings generated
    """

    session_id: str
    started_at: datetime
    completed_at: datetime | None = None
    candidates_screened: int = 0
    opportunities_scored: int = 0
    opportunities_selected: int = 0
    trades_staged: int = 0
    portfolio_plan: SundayPortfolioPlan | None = None
    total_margin_required: float = 0.0
    total_expected_premium: float = 0.0
    existing_margin: float = 0.0
    warnings: list[str] | None = None

    def __post_init__(self):
        """Initialize mutable defaults."""
        if self.warnings is None:
            self.warnings = []


class SundaySessionDisplay:
    """Display utilities for Sunday session workflow.

    Provides Rich console output for all stages of the Sunday workflow.
    """

    def __init__(self, console: Console | None = None):
        """Initialize with optional console.

        Args:
            console: Rich Console instance. Creates new if None.
        """
        self.console = console or Console()

    def display_session_header(self, session_id: str) -> None:
        """Display session start header.

        Args:
            session_id: The session identifier
        """
        self.console.print()
        self.console.print(
            Panel(
                f"[bold cyan]SUNDAY SESSION: {session_id}[/bold cyan]\n\n"
                "[dim]Automated workflow for Sunday trade preparation[/dim]",
                box=box.DOUBLE,
                padding=(1, 2),
            )
        )
        self.console.print()

    def display_stage_header(self, stage_num: int, title: str) -> None:
        """Display stage header.

        Args:
            stage_num: Stage number (1-5)
            title: Stage title
        """
        self.console.print()
        self.console.print(
            f"[bold blue]{'─' * 50}[/bold blue]"
        )
        self.console.print(
            f"[bold white]STAGE {stage_num}: {title}[/bold white]"
        )
        self.console.print(
            f"[bold blue]{'─' * 50}[/bold blue]"
        )
        self.console.print()

    def display_progress_spinner(self, message: str) -> Progress:
        """Create a progress spinner.

        Args:
            message: Message to display

        Returns:
            Progress context manager
        """
        return Progress(
            SpinnerColumn(),
            TextColumn(f"[cyan]{message}[/cyan]"),
            console=self.console,
        )

    def display_candidates_summary(self, count: int, symbols: list[str]) -> None:
        """Display screening results summary.

        Args:
            count: Number of candidates found
            symbols: List of candidate symbols
        """
        if count == 0:
            self.console.print("[yellow]No candidates found matching criteria.[/yellow]")
            return

        # Show first 10 symbols
        display_symbols = symbols[:10]
        more = count - 10 if count > 10 else 0

        self.console.print(f"[green]Found {count} candidates:[/green]")
        self.console.print(f"  {', '.join(display_symbols)}", end="")
        if more > 0:
            self.console.print(f" [dim]... and {more} more[/dim]")
        else:
            self.console.print()

    def display_scoring_summary(self, count: int, top_scores: list[tuple[str, float]]) -> None:
        """Display scoring results summary.

        Args:
            count: Total opportunities scored
            top_scores: List of (symbol, score) tuples for top opportunities
        """
        self.console.print(f"[green]Scored {count} opportunities[/green]")

        if top_scores:
            self.console.print()
            self.console.print("[dim]Top 5 by score:[/dim]")
            for symbol, score in top_scores[:5]:
                self.console.print(f"  {symbol}: [cyan]{score:.1f}[/cyan]")

    def display_enhanced_scoring_summary(self, count: int, top_scores: list[dict]) -> None:
        """Display enhanced scoring results with detailed opportunity information.

        Args:
            count: Total opportunities scored
            top_scores: List of dicts with symbol, score, strike, price, premium, iv data
        """
        self.console.print(f"[green]Scored {count} opportunities[/green]")
        self.console.print()

        if top_scores:
            from rich.table import Table

            table = Table(
                title=f"[bold cyan]Top 15 Opportunities by Composite Score[/bold cyan]",
                show_header=True,
                header_style="bold magenta",
                border_style="dim",
            )

            table.add_column("#", justify="right", style="dim", width=3)
            table.add_column("Symbol", justify="left", style="cyan", width=8)
            table.add_column("Score", justify="right", style="green", width=6)
            table.add_column("Grade", justify="center", style="yellow", width=6)
            table.add_column("Strike", justify="right", style="white", width=8)
            table.add_column("Stock", justify="right", style="white", width=8)
            table.add_column("Premium", justify="right", style="green", width=9)
            table.add_column("IV Rank", justify="right", style="magenta", width=9)
            table.add_column("OTM%", justify="right", style="cyan", width=7)
            table.add_column("DTE", justify="right", style="dim", width=5)

            for idx, opp in enumerate(top_scores, 1):
                # Format grade with color
                grade_color = {
                    "A": "green",
                    "B": "yellow",
                    "C": "orange1",
                    "D": "red",
                    "F": "red bold",
                }.get(opp["grade"], "white")

                # Format IV rank - highlight high IV (convert from decimal to percentage)
                if opp['iv_rank'] is not None:
                    iv_value = opp['iv_rank'] * 100  # Convert 0-1 range to 0-100%
                    iv_display = f"{iv_value:.1f}%"
                    iv_style = "bold magenta" if iv_value > 60 else "magenta"
                else:
                    iv_display = "N/A"
                    iv_style = "magenta"

                # Format OTM% (convert from decimal to percentage)
                otm_value = opp['otm_pct'] * 100  # Convert 0.2 to 20%
                otm_display = f"{otm_value:.1f}%"

                table.add_row(
                    str(idx),
                    opp["symbol"],
                    f"{opp['score']:.1f}",
                    f"[{grade_color}]{opp['grade']}[/{grade_color}]",
                    f"${opp['strike']:.2f}",
                    f"${opp['stock_price']:.2f}",
                    f"${opp['premium']:.2f}",
                    f"[{iv_style}]{iv_display}[/{iv_style}]",
                    otm_display,
                    str(opp['dte']),
                )

            self.console.print(table)
            self.console.print()

    def display_selection_summary(
        self,
        selected_count: int,
        total_count: int,
        selected_symbols: list[str],
    ) -> None:
        """Display selection summary.

        Args:
            selected_count: Number selected
            total_count: Total available
            selected_symbols: List of selected symbols
        """
        self.console.print(
            f"[green]Selected {selected_count} of {total_count} opportunities[/green]"
        )

        if selected_symbols:
            self.console.print(f"  Selected: {', '.join(selected_symbols)}")

    def display_portfolio_summary(self, plan: SundayPortfolioPlan) -> None:
        """Display portfolio build summary.

        Args:
            plan: The portfolio plan
        """
        self.console.print("[green]Portfolio built successfully[/green]")
        self.console.print()

        # Summary table
        table = Table(box=box.SIMPLE, show_header=False)
        table.add_column("Metric", style="dim")
        table.add_column("Value", justify="right")

        table.add_row("Trades", f"{len(plan.trades)}")
        table.add_row("Total Margin", f"${plan.total_margin:,.2f}")
        table.add_row("Expected Premium", f"${plan.expected_premium:,.2f}")
        table.add_row("Budget Used", f"{plan.margin_utilization:.1%}")

        self.console.print(table)

    def display_trade_details(self, plan: SundayPortfolioPlan) -> None:
        """Display detailed list of trades before staging approval.

        Shows all trade details so user can review before committing.

        Args:
            plan: The portfolio plan with trades
        """
        from datetime import datetime

        self.console.print()
        self.console.print(
            Panel(
                f"[bold cyan]PORTFOLIO PLAN - {len(plan.trades)} TRADES[/bold cyan]",
                box=box.ROUNDED,
                padding=(0, 2),
            )
        )

        # Create detailed trade table
        table = Table(box=box.ROUNDED, show_header=True, header_style="bold cyan")
        table.add_column("#", justify="right", style="dim")
        table.add_column("Symbol", style="bold")
        table.add_column("Strike", justify="right")
        table.add_column("DTE", justify="center")
        table.add_column("Limit", justify="right", style="green")
        table.add_column("Margin", justify="right")
        table.add_column("Contracts", justify="center")
        table.add_column("Premium", justify="right", style="green bold")
        table.add_column("Sector", style="dim")

        # Add each trade
        for i, trade in enumerate(plan.trades, 1):
            # Calculate DTE
            try:
                exp_date = datetime.fromisoformat(trade.expiration)
                dte = (exp_date - datetime.now()).days
                dte_str = f"{dte}d"
            except:
                dte_str = "—"

            # Format values
            strike_str = f"${trade.strike:.2f}"
            limit_str = f"${trade.limit_price:.2f}"
            margin_str = f"${trade.margin_required:,.0f}"
            premium_str = f"${trade.expected_premium:.0f}"

            table.add_row(
                str(i),
                trade.symbol,
                strike_str,
                dte_str,
                limit_str,
                margin_str,
                str(trade.contracts),
                premium_str,
                trade.sector,
            )

        self.console.print(table)

        # Summary footer
        self.console.print()
        self.console.print(
            f"[dim]Total Margin: ${plan.total_margin:,.0f} "
            f"({plan.margin_utilization:.1%} of budget) • "
            f"Expected Premium: ${plan.expected_premium:,.0f}[/dim]"
        )

    def display_staging_summary(self, staged_count: int, session_id: str) -> None:
        """Display staging summary.

        Args:
            staged_count: Number of trades staged
            session_id: Session identifier
        """
        self.console.print()
        self.console.print(
            Panel(
                f"[bold green]✓ {staged_count} trades staged for Monday[/bold green]\n\n"
                f"Session: {session_id}\n"
                "[dim]Run 'validate-staged' Monday pre-market to check prices[/dim]",
                box=box.ROUNDED,
                padding=(1, 2),
            )
        )

    def display_session_complete(self, result: SundaySessionResult) -> None:
        """Display session completion summary.

        Args:
            result: The session result
        """
        duration = (
            (result.completed_at - result.started_at).total_seconds()
            if result.completed_at
            else 0
        )

        total_margin = result.existing_margin + result.total_margin_required
        total_premium = result.total_expected_premium  # Only new trades have premium

        self.console.print()
        self.console.print(
            Panel(
                "[bold cyan]SESSION COMPLETE[/bold cyan]\n\n"
                f"Session ID:     {result.session_id}\n"
                f"Duration:       {duration:.0f} seconds\n"
                f"Candidates:     {result.candidates_screened}\n"
                f"Scored:         {result.opportunities_scored}\n"
                f"Selected:       {result.opportunities_selected}\n"
                f"Staged:         {result.trades_staged}\n"
                f"  Margin:       ${result.total_margin_required:,.2f}\n"
                f"  Est. Premium: ${result.total_expected_premium:,.2f}\n"
                f"{'─' * 40}\n"
                f"[bold]Portfolio After Execution[/bold]\n"
                f"  Existing Margin:  ${result.existing_margin:,.2f}\n"
                f"  + New Staged:     ${result.total_margin_required:,.2f}\n"
                f"  Total Margin:     ${total_margin:,.2f}\n"
                f"  New Premium:      ${result.total_expected_premium:,.2f}",
                box=box.DOUBLE,
                padding=(1, 2),
            )
        )

        if result.warnings:
            self.console.print()
            self.console.print("[yellow]Warnings:[/yellow]")
            for warning in result.warnings:
                self.console.print(f"  • {warning}")

    def prompt_continue(self, message: str = "Continue?") -> bool:
        """Prompt user to continue.

        Args:
            message: Prompt message

        Returns:
            True if user wants to continue
        """
        try:
            response = self.console.input(f"\n[cyan]{message}[/cyan] [dim](y/n)[/dim] ")
            return response.lower().strip() in ("y", "yes")
        except KeyboardInterrupt:
            return False


def format_session_id() -> str:
    """Generate a session ID based on current date.

    Returns:
        Session ID in format 'week_of_YYYY-MM-DD'
    """
    from datetime import timedelta
    from src.utils.timezone import us_trading_date

    today = us_trading_date()
    # Find the Monday of this week
    monday = today - timedelta(days=today.weekday())
    return f"week_of_{monday.isoformat()}"


def calculate_trends_for_symbols(
    symbols: list[str], ibkr_client: Any
) -> dict[str, str]:
    """Calculate trend signals for a list of symbols.

    Uses EMA-based trend detection:
    - Uptrend: Price > EMA20 > EMA50
    - Downtrend: Price < EMA20 < EMA50
    - Sideways: Otherwise

    Args:
        symbols: List of stock ticker symbols
        ibkr_client: IBKR client for fetching historical data

    Returns:
        Dictionary mapping symbol -> trend string ("uptrend", "downtrend", "sideways", "unknown")
    """
    import pandas as pd

    trend_data = {}

    logger.info(f"Calculating trend signals for {len(symbols)} symbols...")

    for symbol in symbols:
        try:
            # Get historical bars (need 50+ days for EMA50)
            stock_contract = ibkr_client.get_stock_contract(symbol)
            bars = ibkr_client.ib.reqHistoricalData(
                stock_contract,
                endDateTime="",
                durationStr="60 D",
                barSizeSetting="1 day",
                whatToShow="TRADES",
                useRTH=True,
                formatDate=1,
            )
            # Allow ib_insync event loop to process between requests
            # to prevent semaphore leaks and segfaults
            ibkr_client.ib.sleep(0.1)

            if not bars or len(bars) < 50:
                logger.debug(f"{symbol}: Insufficient historical data for trend calculation")
                trend_data[symbol] = "unknown"
                continue

            # Convert to DataFrame
            df = pd.DataFrame(
                {
                    "close": [bar.close for bar in bars],
                }
            )

            # Calculate EMAs
            ema_20 = df["close"].ewm(span=20, adjust=False).mean().iloc[-1]
            ema_50 = df["close"].ewm(span=50, adjust=False).mean().iloc[-1]
            current_price = df["close"].iloc[-1]

            # Determine trend
            if current_price > ema_20 > ema_50:
                trend = "uptrend"
            elif current_price < ema_20 < ema_50:
                trend = "downtrend"
            else:
                trend = "sideways"

            trend_data[symbol] = trend
            logger.debug(
                f"{symbol}: {trend} (price=${current_price:.2f}, "
                f"EMA20=${ema_20:.2f}, EMA50=${ema_50:.2f})"
            )

        except Exception as e:
            logger.warning(f"{symbol}: Error calculating trend - {e}")
            trend_data[symbol] = "unknown"

    ready_count = sum(1 for t in trend_data.values() if t != "unknown")
    logger.info(
        f"Trend calculation complete: {ready_count}/{len(symbols)} symbols analyzed"
    )

    return trend_data


def calculate_dynamic_budget(
    ibkr_client: Any, account_usage_pct: float, fallback_budget: float
) -> float:
    """Calculate available margin budget for new trades.

    Computes the target margin ceiling (NetLiquidation * account_usage_pct)
    and subtracts existing IBKR margin (InitMarginReq) so we only stage
    trades that fit within the target. Returns 0 if existing margin already
    exceeds the ceiling.

    Formula: available = max(0, NLV * account_usage_pct - existing_margin)

    Args:
        ibkr_client: IBKR client for fetching account data
        account_usage_pct: Percentage of NetLiquidation to use (e.g., 0.20 = 20%)
        fallback_budget: Fallback value if account fetch fails

    Returns:
        Available margin budget for new trades in dollars
    """
    if not ibkr_client:
        logger.warning("No IBKR client available, using fallback budget")
        return fallback_budget

    try:
        # Get account summary
        account = ibkr_client.get_account_summary()

        if "NetLiquidation" not in account:
            logger.warning("NetLiquidation not found in account summary, using fallback")
            return fallback_budget

        net_liquidation = account["NetLiquidation"]
        calculated_budget = net_liquidation * account_usage_pct

        # Subtract existing IBKR margin so we only stage what fits
        existing_margin = 0.0
        if "InitMarginReq" in account:
            existing_margin = account["InitMarginReq"]

        available_for_new = max(0.0, calculated_budget - existing_margin)

        logger.info(
            f"Dynamic budget: ${calculated_budget:,.2f} "
            f"({account_usage_pct:.1%} of ${net_liquidation:,.2f} NLV), "
            f"existing margin: ${existing_margin:,.2f}, "
            f"available for new trades: ${available_for_new:,.2f}"
        )

        return available_for_new

    except Exception as e:
        logger.error(f"Error calculating dynamic budget: {e}")
        logger.warning(f"Using fallback budget: ${fallback_budget:,.2f}")
        return fallback_budget


def run_sunday_session(
    config: SundaySessionConfig | None = None,
    ibkr_client: Any | None = None,
    console: Console | None = None,
    skip_confirmations: bool = False,
    csv_file: str | None = None,
) -> SundaySessionResult:
    """Run the full Sunday session workflow.

    This chains together:
    1. Screen for candidates
    2. Score opportunities
    3. Interactive selection
    4. Build portfolio
    5. Stage trades

    Args:
        config: Session configuration. Uses defaults if None.
        ibkr_client: Optional IBKR client for live margin
        console: Optional Rich console
        skip_confirmations: If True, skip confirmation prompts
        csv_file: Optional path to Barchart CSV file

    Returns:
        SundaySessionResult with workflow outcome
    """
    config = config or SundaySessionConfig()
    display = SundaySessionDisplay(console)
    session_id = format_session_id()

    result = SundaySessionResult(
        session_id=session_id,
        started_at=datetime.now(),
    )

    # Display header
    display.display_session_header(session_id)

    # =========================================================================
    # STAGE 1: Screen for candidates
    # =========================================================================
    display.display_stage_header(1, "SCREEN CANDIDATES")

    # Import and parse CSV if provided, otherwise use mock data
    barchart_candidates = []
    if csv_file:
        from src.tools.barchart_csv_parser import parse_barchart_csv

        barchart_candidates = parse_barchart_csv(csv_file)
        logger.info(f"Parsed {len(barchart_candidates)} candidates from CSV")
    else:
        logger.warning("No CSV file provided - using mock data")
        # TODO: Implement actual screening when no CSV provided
        from src.data.candidates import BarchartCandidate

        # For now, return empty to avoid mock data confusion
        barchart_candidates = []

    result.candidates_screened = len(barchart_candidates)

    if not barchart_candidates:
        result.warnings.append("No candidates found")
        result.completed_at = datetime.now()
        display.display_session_complete(result)
        return result

    # Get unique symbols for display
    unique_symbols = list(dict.fromkeys(c.symbol for c in barchart_candidates))
    display.display_candidates_summary(
        len(barchart_candidates),
        unique_symbols,
    )

    # Calculate trend signals for symbols (if IBKR connected)
    trend_data = {}
    if ibkr_client:
        try:
            trend_data = calculate_trends_for_symbols(unique_symbols, ibkr_client)
        except Exception as e:
            logger.warning(f"Error calculating trends: {e}")
            # Continue without trend data
            trend_data = {}
    else:
        logger.debug("No IBKR client available, skipping trend calculation")

    # Stage 1 complete - proceed directly to Stage 2 (no confirmation needed)
    logger.debug("Stage 1 complete, proceeding to scoring")

    # =========================================================================
    # STAGE 2: Score opportunities
    # =========================================================================
    display.display_stage_header(2, "SCORE OPPORTUNITIES")

    # Use real NakedPutScorer
    logger.info("Scoring candidates with NakedPutScorer...")
    scorer = NakedPutScorer()
    scored_candidates = scorer.score_all(barchart_candidates)
    result.opportunities_scored = len(scored_candidates)

    if not scored_candidates:
        result.warnings.append("No opportunities passed scoring")
        result.completed_at = datetime.now()
        display.display_session_complete(result)
        return result

    # Display top 15 scores with enhanced details
    top_scored = sorted(scored_candidates, key=lambda x: x.composite_score, reverse=True)[:15]

    # Format enhanced scoring data: (symbol, score, strike, stock_price, premium, iv_rank)
    enhanced_top_scores = []
    for sc in top_scored:
        candidate = sc.candidate
        enhanced_top_scores.append({
            "symbol": sc.symbol,
            "score": sc.composite_score,
            "grade": sc.grade,
            "strike": candidate.strike,
            "stock_price": candidate.underlying_price,
            "premium": candidate.bid,
            "iv_rank": candidate.iv_rank,
            "otm_pct": abs(candidate.moneyness_pct),
            "dte": candidate.dte,
        })

    display.display_enhanced_scoring_summary(len(scored_candidates), enhanced_top_scores)

    if not skip_confirmations and not display.prompt_continue("Proceed to selection?"):
        result.warnings.append("Session cancelled by user at Stage 2")
        result.completed_at = datetime.now()
        return result

    # =========================================================================
    # STAGE 3: Interactive selection
    # =========================================================================
    display.display_stage_header(3, "SELECT OPPORTUNITIES")

    # Use InteractiveSelector for symbol filtering
    selector = InteractiveSelector(display.console)

    # Fetch sector information for all unique symbols
    unique_symbols = list({sc.symbol for sc in scored_candidates})
    sector_data = {}
    if ibkr_client:
        display.console.print(f"[dim]Fetching sector information for {len(unique_symbols)} symbols...[/dim]")
        for symbol in unique_symbols:
            details = ibkr_client.get_contract_details(symbol)
            if details:
                sector_data[symbol] = details["industry"]
            else:
                sector_data[symbol] = "Unknown"
        logger.info(f"Fetched sector data for {len(sector_data)} symbols")

    # For automated mode (skip_confirmations), just take top symbols
    if skip_confirmations:
        # Group by symbol and take top N symbols by best score
        symbol_best_score = {}
        for sc in scored_candidates:
            if sc.symbol not in symbol_best_score or sc.composite_score > symbol_best_score[sc.symbol]:
                symbol_best_score[sc.symbol] = sc.composite_score

        top_symbols = sorted(symbol_best_score.items(), key=lambda x: x[1], reverse=True)[:config.max_positions]
        selected_symbols = [s for s, _ in top_symbols]

        # Filter scored_candidates to only selected symbols
        selected_candidates = [sc for sc in scored_candidates if sc.symbol in selected_symbols]
    else:
        # Interactive selection with user prompts
        selection_result = selector.run_selection(
            scored_candidates=scored_candidates,
            trend_data=trend_data,  # Pass calculated trend signals
            sector_data=sector_data,  # Pass sector information
        )
        selected_symbols = selection_result.selected_symbols
        selected_candidates = []
        for symbol in selected_symbols:
            selected_candidates.extend(selection_result.candidates_by_symbol[symbol])

    result.opportunities_selected = len(selected_candidates)

    display.display_selection_summary(
        len(selected_candidates),
        len(scored_candidates),
        selected_symbols,
    )

    if not skip_confirmations and not display.prompt_continue("Proceed to portfolio build?"):
        result.warnings.append("Session cancelled by user at Stage 3")
        result.completed_at = datetime.now()
        return result

    # =========================================================================
    # STAGE 4: Build portfolio
    # =========================================================================
    display.display_stage_header(4, "BUILD PORTFOLIO")

    # Calculate dynamic margin budget from account (if enabled)
    account_equity = None
    if config.use_dynamic_budget and ibkr_client:
        margin_budget = calculate_dynamic_budget(
            ibkr_client=ibkr_client,
            account_usage_pct=config.account_usage_pct,
            fallback_budget=config.margin_budget,
        )
        # Also fetch NLV for risk-based position sizing
        try:
            summary = ibkr_client.get_account_summary()
            if summary and "NetLiquidation" in summary:
                account_equity = float(summary["NetLiquidation"])
        except Exception as e:
            logger.warning(f"Could not fetch NLV for position sizing: {e}")
    else:
        margin_budget = config.margin_budget
        logger.info(f"Using static margin budget: ${margin_budget:,.2f}")

    # Step 4a: Find best strikes for each symbol using StrikeFinder
    logger.info(f"Finding best strikes for {len(selected_symbols)} symbols...")

    # Group selected_candidates by symbol for strike finding
    candidates_by_symbol = {}
    for sc in selected_candidates:
        if sc.symbol not in candidates_by_symbol:
            candidates_by_symbol[sc.symbol] = []
        candidates_by_symbol[sc.symbol].append(sc)

    # Use StrikeFinder to select optimal strikes
    strike_finder = StrikeFinder(
        ibkr_client=ibkr_client,
        preferences=StrikePreferences.from_env(),
        limit_calculator=LimitPriceCalculator(),
        account_equity=account_equity,
    )

    strike_candidates = strike_finder.find_best_strikes(
        symbols=selected_symbols,
        barchart_data=candidates_by_symbol,
        sector_data=sector_data,  # Pass sector information through
    )

    if not strike_candidates:
        result.warnings.append("No viable strikes found")
        result.completed_at = datetime.now()
        display.display_session_complete(result)
        return result

    logger.info(f"Found {len(strike_candidates)} strike candidates")

    # Step 4b: Build portfolio with PortfolioBuilder
    logger.info("Building portfolio with margin optimization...")
    portfolio_builder = PortfolioBuilder(
        ibkr_client=ibkr_client,
        config=PortfolioConfig.from_env(),
    )

    portfolio_plan = portfolio_builder.build_portfolio(
        candidates=strike_candidates,
        margin_budget=margin_budget,
    )

    # Convert PortfolioPlan to SundayPortfolioPlan for display
    simple_trades = []
    for staged_trade in portfolio_plan.trades:
        if staged_trade.within_budget:
            simple_trades.append(
                SundayStagedTrade(
                    id=0,  # Will be set when saved to DB
                    symbol=staged_trade.symbol,
                    strike=staged_trade.strike,
                    expiration=str(staged_trade.expiration),
                    contracts=staged_trade.contracts,
                    limit_price=staged_trade.candidate.suggested_limit,
                    margin_required=staged_trade.total_margin,
                    expected_premium=staged_trade.total_premium,
                    sector=staged_trade.candidate.sector,
                    state=OpportunityState.STAGED,
                )
            )

    # Calculate totals from actual staged trades (not all evaluated candidates)
    staged_margin = sum(t.margin_required for t in simple_trades)
    staged_premium = sum(t.expected_premium for t in simple_trades)

    result.total_margin_required = staged_margin
    result.total_expected_premium = staged_premium

    simple_plan = SundayPortfolioPlan(
        trades=simple_trades,
        total_margin=staged_margin,
        expected_premium=staged_premium,
        margin_budget=portfolio_plan.margin_budget,
    )
    result.portfolio_plan = simple_plan

    display.display_portfolio_summary(simple_plan)
    display.display_trade_details(simple_plan)

    if not skip_confirmations and not display.prompt_continue("Stage these trades for Monday?"):
        result.warnings.append("Session cancelled by user at Stage 4")
        result.completed_at = datetime.now()
        return result

    # =========================================================================
    # STAGE 5: Stage trades
    # =========================================================================
    display.display_stage_header(5, "STAGE TRADES")

    # Persist to database using OpportunityLifecycleManager
    logger.info(f"Staging {len(portfolio_plan.trades)} trades to database...")

    staged_count = 0
    actual_staged_margin = 0.0
    actual_staged_premium = 0.0
    if ibkr_client:  # Only persist if we have actual trade data
        from src.data.database import get_db_session
        from src.execution.opportunity_lifecycle import OpportunityLifecycleManager
        from src.data.models import ScanOpportunity, ScanResult
        from src.data.repositories import ScanRepository

        with get_db_session() as db:
            scan_repo = ScanRepository(db)
            lifecycle = OpportunityLifecycleManager(db)

            # Create a scan result for this Sunday session
            scan_result = ScanResult(
                source="sunday_session",
                scan_timestamp=datetime.now(),
                total_candidates=len(portfolio_plan.trades),
                config_used={"session_id": session_id, "csv_file": csv_file or "none"},
            )
            scan_result = scan_repo.create_scan(scan_result)

            # Create and stage each opportunity
            for staged_trade in portfolio_plan.trades:
                if not staged_trade.within_budget:
                    continue

                # Check for duplicate - skip if already staged
                existing = scan_repo.find_duplicate(
                    symbol=staged_trade.symbol,
                    strike=staged_trade.strike,
                    expiration=staged_trade.expiration,
                    option_type="PUT",
                    days_lookback=7,
                )

                if existing:
                    logger.info(
                        f"Skipping duplicate: {staged_trade.symbol} ${staged_trade.strike}P "
                        f"{staged_trade.expiration} (already exists as opportunity #{existing.id}, state: {existing.state})"
                    )
                    console.print(
                        f"  [dim]⊘ Skipped duplicate: {staged_trade.symbol} "
                        f"${staged_trade.strike}P {staged_trade.expiration} "
                        f"(already {existing.state})[/dim]"
                    )
                    continue

                # Check for existing open position in portfolio
                from src.data.repositories import TradeRepository
                trade_repo = TradeRepository(db)
                exp_date = (
                    staged_trade.expiration
                    if isinstance(staged_trade.expiration, date)
                    else datetime.fromisoformat(str(staged_trade.expiration)).date()
                )
                open_position = trade_repo.find_open_position(
                    symbol=staged_trade.symbol,
                    strike=staged_trade.strike,
                    expiration=exp_date,
                )
                if open_position:
                    logger.warning(
                        f"Skipping {staged_trade.symbol} ${staged_trade.strike}P "
                        f"{staged_trade.expiration}: already open in portfolio "
                        f"(trade {open_position.trade_id}, entered {open_position.entry_date})"
                    )
                    console.print(
                        f"  [yellow]⊘ Already open: {staged_trade.symbol} "
                        f"${staged_trade.strike}P {staged_trade.expiration} "
                        f"(trade {open_position.trade_id})[/yellow]"
                    )
                    continue

                # Create ScanOpportunity with essential fields
                opportunity = ScanOpportunity(
                    scan_id=scan_result.id,
                    symbol=staged_trade.symbol,
                    strike=staged_trade.strike,
                    expiration=staged_trade.expiration,
                    bid=staged_trade.candidate.bid,
                    ask=staged_trade.candidate.ask,
                    delta=staged_trade.candidate.delta if hasattr(staged_trade.candidate, 'delta') else None,
                    iv=staged_trade.candidate.iv if hasattr(staged_trade.candidate, 'iv') else None,
                    volume=staged_trade.candidate.volume if hasattr(staged_trade.candidate, 'volume') else None,
                    open_interest=staged_trade.candidate.open_interest if hasattr(staged_trade.candidate, 'open_interest') else None,
                    stock_price=staged_trade.candidate.stock_price,
                    dte=staged_trade.candidate.dte,
                    otm_pct=staged_trade.candidate.otm_pct,
                    validation_status="validated",
                    state=OpportunityState.VALIDATED.name,  # Use .name instead of .value
                    source="sunday_session",
                    # Staging fields
                    staged_at=datetime.now(),
                    staged_contracts=staged_trade.contracts,
                    staged_limit_price=staged_trade.candidate.suggested_limit,
                    staged_margin=staged_trade.total_margin,
                    staged_margin_source=staged_trade.margin_source,
                    portfolio_rank=staged_trade.portfolio_rank,
                    execution_session=session_id,
                )

                opportunity = scan_repo.add_opportunity(opportunity)

                # Follow proper state machine transitions: VALIDATED → OFFERED → APPROVED → STAGED
                success = lifecycle.transition(
                    opportunity.id,
                    OpportunityState.OFFERED,
                    reason=f"Sunday session portfolio selection",
                    actor="sunday_session",
                )

                if success:
                    success = lifecycle.transition(
                        opportunity.id,
                        OpportunityState.APPROVED,
                        reason=f"Approved in Sunday session portfolio",
                        actor="sunday_session",
                    )

                if success:
                    success = lifecycle.transition(
                        opportunity.id,
                        OpportunityState.STAGED,
                        reason=f"Staged for Monday execution: {session_id}",
                        actor="sunday_session",
                    )

                if success:
                    staged_count += 1
                    actual_staged_margin += staged_trade.total_margin
                    actual_staged_premium += staged_trade.total_premium
                else:
                    logger.warning(f"Failed to stage opportunity {opportunity.id} for {staged_trade.symbol}")

            # Update scan_result with final counts and timing
            scan_result.validated_count = staged_count
            elapsed = (datetime.now() - result.started_at).total_seconds()
            scan_result.execution_time_seconds = elapsed

            db.commit()
            logger.info(f"Successfully staged {staged_count} trades to database")
    else:
        logger.warning("No IBKR client - skipping database persistence")
        staged_count = len(simple_trades)
        actual_staged_margin = staged_margin
        actual_staged_premium = staged_premium

    result.trades_staged = staged_count
    result.total_margin_required = actual_staged_margin
    result.total_expected_premium = actual_staged_premium

    # Capture existing margin for portfolio totals
    if ibkr_client:
        try:
            account = ibkr_client.get_account_summary()
            if account and "InitMarginReq" in account:
                result.existing_margin = account["InitMarginReq"]
        except Exception as e:
            logger.warning(f"Could not fetch existing margin: {e}")

    display.display_staging_summary(staged_count, session_id)

    # Complete session
    result.completed_at = datetime.now()
    display.display_session_complete(result)

    return result


def _mock_screen_candidates(config: SundaySessionConfig) -> list[dict]:
    """Mock screening - returns fake candidates for testing.

    In production, this would call the actual scanner.
    """
    return [
        {"symbol": "AAPL", "price": 180.0, "sector": "Technology"},
        {"symbol": "MSFT", "price": 380.0, "sector": "Technology"},
        {"symbol": "GOOGL", "price": 170.0, "sector": "Technology"},
        {"symbol": "JPM", "price": 195.0, "sector": "Financial"},
        {"symbol": "V", "price": 280.0, "sector": "Financial"},
        {"symbol": "UNH", "price": 520.0, "sector": "Healthcare"},
        {"symbol": "JNJ", "price": 160.0, "sector": "Healthcare"},
        {"symbol": "PG", "price": 165.0, "sector": "Consumer"},
    ]


def _mock_score_opportunities(
    candidates: list[dict],
    config: SundaySessionConfig,
) -> list[dict]:
    """Mock scoring - returns fake scores for testing.

    In production, this would call the actual scorer.
    """
    import random

    scored = []
    for candidate in candidates:
        score = random.uniform(60, 95)
        scored.append({
            **candidate,
            "score": score,
            "strike": candidate["price"] * (1 - config.min_otm_pct),
            "premium": config.min_premium + random.uniform(0, 0.30),
            "expiration": "2026-02-07",
            "contracts": 5,
            "margin": candidate["price"] * 100 * 5 * 0.2,  # ~20% margin
        })
    return scored


def _build_simple_portfolio(
    selected: list[dict],
    config: SundaySessionConfig,
) -> SundayPortfolioPlan:
    """Build a simple portfolio from selected opportunities.

    This is a simplified version that doesn't require IBKR integration.
    In production, this would use the full PortfolioBuilder.
    """
    trades = []
    total_margin = 0.0
    total_premium = 0.0

    for i, opp in enumerate(selected):
        margin = opp["margin"]

        # Check if within budget
        if total_margin + margin > config.margin_budget:
            continue

        trade = SundayStagedTrade(
            id=i + 1,
            symbol=opp["symbol"],
            strike=opp["strike"],
            expiration=opp["expiration"],
            contracts=opp.get("contracts", 5),
            limit_price=opp["premium"],
            margin_required=margin,
            expected_premium=opp["premium"] * 100 * opp.get("contracts", 5),
            sector=opp.get("sector", "Unknown"),
        )

        trades.append(trade)
        total_margin += margin
        total_premium += trade.expected_premium

    return SundayPortfolioPlan(
        trades=trades,
        total_margin=total_margin,
        expected_premium=total_premium,
        margin_budget=config.margin_budget,
    )
