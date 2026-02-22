"""Portfolio builder for naked put trading.

This module builds an optimal portfolio of naked put trades within margin
constraints. It uses ACTUAL margin from IBKR's whatIfOrder API when available,
with an estimate fallback for offline mode.

Key features:
- Gets actual margin via whatIfOrder (not just estimates)
- Shows before/after re-ranking when actual margin differs from estimates
- Greedy algorithm to maximize margin efficiency within budget
- Enforces sector concentration limits
- Flags trades using estimated margin for later verification
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from loguru import logger

from src.services.strike_finder import StrikeCandidate


@dataclass
class PortfolioConfig:
    """Configuration for portfolio building.

    All values loaded from environment variables with sensible defaults.

    Attributes:
        margin_budget_pct: Fraction of NLV to use as margin budget (default 0.50)
        margin_budget_default: Fallback budget if IBKR offline (default $50,000)
        max_positions: Maximum number of positions (default 10)
        max_sector_concentration: Max trades per sector (default 3)
        max_budget_utilization: Warn if budget > this % used (default 0.80)
        high_iv_threshold: IV rank above this triggers warning (default 0.60)
    """

    margin_budget_pct: float = 0.50
    margin_budget_default: float = 50000.0
    max_positions: int = 10
    max_sector_concentration: int = 3
    max_budget_utilization: float = 0.80
    high_iv_threshold: float = 0.60

    @classmethod
    def from_env(cls) -> "PortfolioConfig":
        """Load configuration from the central Config singleton.

        Values come from .env via ``get_config()`` so there is a single
        source of truth for MAX_POSITIONS, MARGIN_BUDGET_PCT, etc.

        Returns:
            PortfolioConfig instance with values from the central config
        """
        from src.config.base import get_config

        cfg = get_config()
        return cls(
            margin_budget_pct=cfg.margin_budget_pct,
            margin_budget_default=cfg.margin_budget_default,
            max_positions=cfg.max_positions,
            max_sector_concentration=cfg.max_sector_count,
        )


@dataclass
class StagedTrade:
    """A trade ready for staging, with margin confirmed.

    Represents a trade that has passed all checks and is ready to be
    staged for Monday execution.

    Attributes:
        candidate: The underlying StrikeCandidate
        margin_per_contract: Margin required per contract
        margin_source: Source of margin data ('ibkr_whatif' or 'estimated')
        contracts: Number of contracts to trade
        total_margin: Total margin (margin_per_contract * contracts)
        total_premium: Expected premium income
        portfolio_rank: Rank within the portfolio (1 = best)
        cumulative_margin: Running total of margin up to this trade
        within_budget: Whether this trade fits within the budget
        skip_reason: If not within_budget, why it was skipped
    """

    candidate: StrikeCandidate
    margin_per_contract: float
    margin_source: str
    contracts: int
    total_margin: float
    total_premium: float
    portfolio_rank: int
    cumulative_margin: float
    within_budget: bool
    skip_reason: str | None = None

    @property
    def symbol(self) -> str:
        """Get the symbol from the candidate."""
        return self.candidate.symbol

    @property
    def strike(self) -> float:
        """Get the strike from the candidate."""
        return self.candidate.strike

    @property
    def expiration(self):
        """Get the expiration from the candidate."""
        return self.candidate.expiration

    @property
    def margin_efficiency(self) -> float:
        """Calculate margin efficiency (premium / margin)."""
        if self.total_margin <= 0:
            return 0.0
        return self.total_premium / self.total_margin


@dataclass
class MarginComparison:
    """Comparison of estimated vs actual margin for a candidate.

    Used for the before/after re-ranking display.

    Attributes:
        candidate: The StrikeCandidate
        estimated_margin: Original estimated margin
        estimated_efficiency: Efficiency based on estimate
        estimated_rank: Rank based on estimate
        actual_margin: Actual margin from IBKR (or estimate if unavailable)
        actual_efficiency: Efficiency based on actual
        actual_rank: Rank based on actual
        rank_shift: How much rank changed (+1 = moved up, -1 = down)
        margin_source: 'ibkr_whatif' or 'estimated'
    """

    candidate: StrikeCandidate
    estimated_margin: float
    estimated_efficiency: float
    estimated_rank: int
    actual_margin: float
    actual_efficiency: float
    actual_rank: int
    rank_shift: int
    margin_source: str


@dataclass
class PortfolioPlan:
    """The complete set of staged trades for the week.

    Represents the final portfolio plan after all selection and
    margin validation.

    Attributes:
        trades: List of selected trades (within budget)
        skipped_trades: List of trades that didn't fit
        margin_comparisons: Before/after margin comparison for display
        total_margin_used: Total margin of selected trades
        margin_budget: Available margin budget
        margin_remaining: Remaining budget after selections
        total_premium_expected: Total expected premium income
        sector_distribution: Count of trades per sector
        warnings: List of warning messages
        created_at: When the plan was created
        ibkr_connected: Whether IBKR was connected during planning
    """

    trades: list[StagedTrade]
    skipped_trades: list[StagedTrade]
    margin_comparisons: list[MarginComparison]
    total_margin_used: float
    margin_budget: float
    margin_remaining: float
    total_premium_expected: float
    sector_distribution: dict[str, int]
    warnings: list[str]
    created_at: datetime = field(default_factory=datetime.now)
    ibkr_connected: bool = True

    @property
    def budget_utilization(self) -> float:
        """Calculate budget utilization percentage."""
        if self.margin_budget <= 0:
            return 0.0
        return self.total_margin_used / self.margin_budget

    @property
    def trade_count(self) -> int:
        """Number of trades in the portfolio."""
        return len(self.trades)

    @property
    def has_estimated_margins(self) -> bool:
        """Check if any trades use estimated margins."""
        return any(t.margin_source == "estimated" for t in self.trades)


class IBKRClientProtocol(Protocol):
    """Protocol for IBKR client dependency injection."""

    def get_account_summary(self) -> dict | None:
        """Get account summary including NLV."""
        ...

    def get_actual_margin(self, contract, quantity: int = 1) -> float | None:
        """Get actual margin via whatIfOrder."""
        ...

    def get_option_contract(
        self, symbol: str, strike: float, expiration: str, right: str
    ):
        """Create an option contract."""
        ...

    def qualify_contract(self, contract) -> list:
        """Qualify a contract with IBKR."""
        ...


class PortfolioBuilder:
    """Build an optimal portfolio of naked put trades within margin constraints.

    This class handles the critical task of converting strike candidates into
    a portfolio plan, using ACTUAL margin requirements from IBKR when available.

    The algorithm:
    1. Get actual margin for each candidate via whatIfOrder
    2. Calculate margin efficiency (premium / margin)
    3. Rank by efficiency (highest first)
    4. Greedily select trades that fit within budget and constraints

    Example:
        >>> builder = PortfolioBuilder(ibkr_client)
        >>> candidates = strike_finder.find_best_strikes(symbols, data, prefs)
        >>> plan = builder.build_portfolio(candidates)
        >>> for trade in plan.trades:
        ...     print(f"{trade.symbol}: ${trade.total_premium} premium")
    """

    def __init__(
        self,
        ibkr_client: IBKRClientProtocol | None = None,
        config: PortfolioConfig | None = None,
        db_session = None,
    ):
        """Initialize the portfolio builder.

        Args:
            ibkr_client: Optional IBKR client for actual margin lookups.
                        If None, uses estimated margins only.
            config: Optional configuration. If None, loads from environment.
            db_session: Optional SQLAlchemy session for checking staged trades.
                       If None, cumulative margin check is skipped.
        """
        self.ibkr_client = ibkr_client
        self.config = config or PortfolioConfig.from_env()
        self.db_session = db_session
        self._ibkr_connected = ibkr_client is not None

        logger.debug(
            f"PortfolioBuilder initialized: "
            f"budget_pct={self.config.margin_budget_pct}, "
            f"max_positions={self.config.max_positions}, "
            f"max_sector={self.config.max_sector_concentration}, "
            f"ibkr_connected={self._ibkr_connected}, "
            f"db_connected={db_session is not None}"
        )

    def build_portfolio(
        self,
        candidates: list[StrikeCandidate],
        margin_budget: float | None = None,
    ) -> PortfolioPlan:
        """Build optimal portfolio from ranked candidates.

        Algorithm:
        1. If margin_budget not provided, query IBKR for account NLV x budget_pct
        2. Get ACTUAL margin for each candidate (via whatIfOrder if connected)
        3. Calculate margin efficiency for each: premium_income / total_margin
        4. Sort by margin efficiency (highest first = best use of capital)
        5. Greedily select trades within constraints

        Args:
            candidates: List of StrikeCandidate objects to consider
            margin_budget: Optional override for margin budget.
                          If None, calculates from IBKR NLV or uses default.

        Returns:
            PortfolioPlan with selected trades and analysis
        """
        if not candidates:
            logger.warning("No candidates provided to portfolio builder")
            return self._empty_plan(margin_budget or self.config.margin_budget_default)

        # Step 1: Check already-staged trades
        already_staged_margin, already_staged_count = self._get_already_staged_margin()

        # Step 2: Determine margin budget and apply MAX_TOTAL_MARGIN ceiling
        budget = margin_budget or self._get_margin_budget()

        # Get MAX_TOTAL_MARGIN from central config (absolute ceiling)
        from src.config.base import get_config

        max_total_margin = get_config().max_total_margin

        # Check if already-staged trades exceed the absolute ceiling
        if already_staged_margin >= max_total_margin:
            logger.error(
                f"Already-staged trades (${already_staged_margin:,.0f}) "
                f"exceed MAX_TOTAL_MARGIN (${max_total_margin:,.0f})"
            )
            empty_plan = self._empty_plan(budget)
            empty_plan.warnings = [
                f"CRITICAL: {already_staged_count} trades already staged with "
                f"${already_staged_margin:,.0f} margin exceeds absolute limit "
                f"of ${max_total_margin:,.0f}. Cancel some staged trades before adding more."
            ]
            return empty_plan

        # Calculate available budget for NEW trades
        available_budget = min(budget, max_total_margin - already_staged_margin)

        if already_staged_count > 0:
            logger.warning(
                f"Already staged: {already_staged_count} trades, "
                f"${already_staged_margin:,.0f} margin. "
                f"Available for new trades: ${available_budget:,.0f}"
            )

        logger.info(f"Building portfolio with budget: ${available_budget:,.2f}")

        # Step 3: Get actual margins for all candidates
        candidates_with_margin = self._get_actual_margins(candidates)

        # Step 4: Build margin comparison for display
        margin_comparisons = self._build_margin_comparisons(candidates_with_margin)

        # Step 5: Sort by margin efficiency (actual margin)
        sorted_candidates = sorted(
            candidates_with_margin,
            key=lambda c: self._calculate_efficiency(c),
            reverse=True,  # Highest efficiency first
        )

        # Step 6: Greedy selection (using available_budget after accounting for staged trades)
        selected_trades: list[StagedTrade] = []
        skipped_trades: list[StagedTrade] = []
        warnings: list[str] = []
        cumulative_margin = 0.0
        sector_counts: dict[str, int] = {}
        selected_symbols: set[str] = set()

        # Add warning if many trades already staged
        if already_staged_count > 0:
            warnings.append(
                f"Note: {already_staged_count} trades already staged "
                f"(${already_staged_margin:,.0f} margin) - "
                f"budget reduced to ${available_budget:,.0f} for new trades"
            )

        for rank, candidate in enumerate(sorted_candidates, start=1):
            # Calculate trade metrics
            margin_per_contract = candidate.effective_margin
            contracts = candidate.contracts
            total_margin = margin_per_contract * contracts
            total_premium = candidate.suggested_limit * 100 * contracts

            logger.debug(
                f"Rank {rank} {candidate.symbol} ${candidate.strike}P: "
                f"eff_margin={margin_per_contract:.2f}, actual={candidate.margin_actual}, "
                f"est={candidate.margin_estimate:.0f}, total={total_margin:.0f}"
            )

            if total_margin <= 0:
                # Safeguard: recalculate from Reg-T if effective_margin returned 0
                fallback = max(
                    candidate.margin_estimate or 0,
                    0.10 * candidate.stock_price * 100,
                )
                logger.warning(
                    f"Zero margin for {candidate.symbol} ${candidate.strike}P: "
                    f"effective_margin={margin_per_contract}, "
                    f"margin_actual={candidate.margin_actual}, "
                    f"margin_estimate={candidate.margin_estimate}, "
                    f"stock_price={candidate.stock_price}, "
                    f"contracts={contracts} → fallback ${fallback:,.0f}"
                )
                if fallback > 0:
                    margin_per_contract = fallback
                    total_margin = fallback * contracts

            # Check constraints (using available_budget, not original budget)
            skip_reason = self._check_constraints(
                candidate=candidate,
                total_margin=total_margin,
                cumulative_margin=cumulative_margin,
                budget=available_budget,
                sector_counts=sector_counts,
                selected_symbols=selected_symbols,
                current_position_count=len(selected_trades),
            )

            staged_trade = StagedTrade(
                candidate=candidate,
                margin_per_contract=margin_per_contract,
                margin_source=candidate.margin_source,
                contracts=contracts,
                total_margin=total_margin,
                total_premium=total_premium,
                portfolio_rank=rank,
                cumulative_margin=cumulative_margin + total_margin,
                within_budget=skip_reason is None,
                skip_reason=skip_reason,
            )

            if skip_reason is None:
                # Trade accepted
                selected_trades.append(staged_trade)
                cumulative_margin += total_margin
                selected_symbols.add(candidate.symbol)

                # Update sector count
                sector = candidate.sector or "Unknown"
                sector_counts[sector] = sector_counts.get(sector, 0) + 1

                # Check for high IV warning
                if candidate.iv_rank > self.config.high_iv_threshold:
                    warnings.append(
                        f"{candidate.symbol} IV Rank {candidate.iv_rank:.1%} — "
                        "elevated, check for upcoming events"
                    )

                logger.debug(
                    f"Selected {candidate.symbol} ${candidate.strike}P: "
                    f"margin=${total_margin:,.0f}, premium=${total_premium:.0f}"
                )
            else:
                # Trade skipped
                skipped_trades.append(staged_trade)
                logger.debug(
                    f"Skipped {candidate.symbol} ${candidate.strike}P: {skip_reason}"
                )

        # Generate additional warnings
        warnings.extend(self._generate_warnings(
            selected_trades, budget, cumulative_margin, sector_counts
        ))

        # Build final plan (using available_budget which accounts for staged trades)
        plan = PortfolioPlan(
            trades=selected_trades,
            skipped_trades=skipped_trades,
            margin_comparisons=margin_comparisons,
            total_margin_used=cumulative_margin,
            margin_budget=available_budget,
            margin_remaining=available_budget - cumulative_margin,
            total_premium_expected=sum(t.total_premium for t in selected_trades),
            sector_distribution=sector_counts,
            warnings=warnings,
            ibkr_connected=self._ibkr_connected,
        )

        logger.info(
            f"Portfolio built: {plan.trade_count} trades, "
            f"${plan.total_margin_used:,.0f} margin ({plan.budget_utilization:.1%}), "
            f"${plan.total_premium_expected:,.0f} expected premium"
        )

        return plan

    def _get_already_staged_margin(self) -> tuple[float, int]:
        """Get total margin and count of already-staged trades.

        Returns:
            Tuple of (total_margin, trade_count) for already staged trades
        """
        if not self.db_session:
            logger.debug("No database session - skipping staged trades check")
            return 0.0, 0

        try:
            from src.data.models import ScanOpportunity

            # Query all STAGED opportunities
            staged_opps = (
                self.db_session.query(ScanOpportunity)
                .filter(ScanOpportunity.state == "STAGED")
                .all()
            )

            total_margin = sum(
                opp.staged_margin for opp in staged_opps if opp.staged_margin
            )
            count = len(staged_opps)

            if count > 0:
                logger.info(
                    f"Found {count} already-staged trades with "
                    f"${total_margin:,.0f} margin"
                )

            return total_margin, count

        except Exception as e:
            logger.warning(f"Error querying staged trades: {e}")
            return 0.0, 0

    def _get_margin_budget(self) -> float:
        """Get margin budget from IBKR or use default.

        Calculates: MARGIN_BUDGET_PCT x Net Liquidation Value

        Returns:
            Margin budget in dollars
        """
        if not self.ibkr_client:
            logger.info(
                f"IBKR not connected, using default budget: "
                f"${self.config.margin_budget_default:,.0f}"
            )
            return self.config.margin_budget_default

        try:
            summary = self.ibkr_client.get_account_summary()
            if summary and "NetLiquidation" in summary:
                nlv = float(summary["NetLiquidation"])
                budget = nlv * self.config.margin_budget_pct
                logger.info(
                    f"Calculated budget from IBKR: "
                    f"NLV=${nlv:,.0f} x {self.config.margin_budget_pct:.0%} = ${budget:,.0f}"
                )
                return budget
        except Exception as e:
            logger.warning(f"Error getting NLV from IBKR: {e}")

        logger.info(
            f"Using default budget: ${self.config.margin_budget_default:,.0f}"
        )
        return self.config.margin_budget_default

    def _get_actual_margins(
        self, candidates: list[StrikeCandidate]
    ) -> list[StrikeCandidate]:
        """Get actual margins for all candidates via IBKR whatIfOrder.

        Args:
            candidates: List of candidates to get margins for

        Returns:
            Same list with margin_actual populated where available
        """
        if not self.ibkr_client:
            logger.info("IBKR not connected, using estimated margins")
            return candidates

        logger.info(f"Getting actual margins for {len(candidates)} candidates...")
        updated_candidates = []

        for candidate in candidates:
            actual_margin = self._get_single_margin(candidate)
            if actual_margin:
                # Create new candidate with actual margin
                # (StrikeCandidate is a dataclass, so we need to create a new one)
                updated = StrikeCandidate(
                    symbol=candidate.symbol,
                    stock_price=candidate.stock_price,
                    strike=candidate.strike,
                    expiration=candidate.expiration,
                    dte=candidate.dte,
                    bid=candidate.bid,
                    ask=candidate.ask,
                    mid=candidate.mid,
                    suggested_limit=candidate.suggested_limit,
                    otm_pct=candidate.otm_pct,
                    delta=candidate.delta,
                    iv=candidate.iv,
                    iv_rank=candidate.iv_rank,
                    volume=candidate.volume,
                    open_interest=candidate.open_interest,
                    margin_estimate=candidate.margin_estimate,
                    margin_actual=actual_margin,
                    contracts=candidate.contracts,
                    total_margin=actual_margin * candidate.contracts,
                    premium_income=candidate.premium_income,
                    margin_efficiency=(
                        candidate.premium_income / (actual_margin * candidate.contracts)
                        if actual_margin > 0 else 0.0
                    ),
                    sector=candidate.sector,
                    score=candidate.score,
                    source=candidate.source,
                )
                updated_candidates.append(updated)
            else:
                # Keep original with estimated margin
                updated_candidates.append(candidate)

        # Retry pass: candidates that failed on first attempt
        failed_indices = [
            i for i, c in enumerate(updated_candidates)
            if c.margin_actual is None
        ]

        if failed_indices:
            logger.info(
                f"Retrying margin for {len(failed_indices)} candidates "
                f"that failed first attempt..."
            )
            self.ibkr_client.ib.sleep(1.0)  # Let IBKR settle

            for i in failed_indices:
                actual_margin = self._get_single_margin(updated_candidates[i])
                if actual_margin:
                    old = updated_candidates[i]
                    updated_candidates[i] = StrikeCandidate(
                        symbol=old.symbol,
                        stock_price=old.stock_price,
                        strike=old.strike,
                        expiration=old.expiration,
                        dte=old.dte,
                        bid=old.bid,
                        ask=old.ask,
                        mid=old.mid,
                        suggested_limit=old.suggested_limit,
                        otm_pct=old.otm_pct,
                        delta=old.delta,
                        iv=old.iv,
                        iv_rank=old.iv_rank,
                        volume=old.volume,
                        open_interest=old.open_interest,
                        margin_estimate=old.margin_estimate,
                        margin_actual=actual_margin,
                        contracts=old.contracts,
                        total_margin=actual_margin * old.contracts,
                        premium_income=old.premium_income,
                        margin_efficiency=(
                            old.premium_income / (actual_margin * old.contracts)
                            if actual_margin > 0 else 0.0
                        ),
                        sector=old.sector,
                        score=old.score,
                        source=old.source,
                    )
                self.ibkr_client.ib.sleep(0.2)  # Pace retries

        actual_count = sum(1 for c in updated_candidates if c.margin_actual)
        logger.info(
            f"Got actual margins for {actual_count}/{len(candidates)} candidates"
        )

        # Log margin details for all candidates
        for c in updated_candidates:
            logger.debug(
                f"  {c.symbol} ${c.strike}P: actual={c.margin_actual}, "
                f"estimate={c.margin_estimate:.0f}, effective={c.effective_margin:.0f}"
            )

        return updated_candidates

    def _get_single_margin(self, candidate: StrikeCandidate) -> float | None:
        """Get actual margin for a single candidate from IBKR.

        Args:
            candidate: The StrikeCandidate to check

        Returns:
            Actual margin per contract, or None if unavailable
        """
        if not self.ibkr_client:
            return None

        try:
            # Format expiration as YYYYMMDD
            exp_str = candidate.expiration.strftime("%Y%m%d")

            # Create option contract
            contract = self.ibkr_client.get_option_contract(
                candidate.symbol,
                exp_str,
                candidate.strike,
                "P",  # Put
            )

            if not contract:
                logger.debug(f"Could not create contract for {candidate.symbol}")
                return None

            # Qualify the contract
            qualified = self.ibkr_client.qualify_contract(contract)
            if not qualified:
                logger.debug(f"Could not qualify contract for {candidate.symbol}")
                return None

            # Get actual margin via whatIfOrder using real contract count
            # IBKR returns more accurate margins with actual quantity vs 1
            qty = candidate.contracts or 1
            total_margin = self.ibkr_client.get_actual_margin(
                qualified, quantity=qty
            )

            if total_margin and total_margin > 0:
                margin_per_contract = total_margin / qty
                # Sanity check: naked put margin should be at least 5% of notional
                min_believable = 0.05 * candidate.strike * 100
                if margin_per_contract < min_believable:
                    logger.warning(
                        f"IBKR margin ${margin_per_contract:,.2f}/contract "
                        f"(${total_margin:,.2f} total for x{qty}) for "
                        f"{candidate.symbol} ${candidate.strike}P is below "
                        f"sanity floor ${min_believable:,.0f} — will use Reg-T estimate"
                    )
                    return None
                logger.debug(
                    f"Got actual margin for {candidate.symbol} ${candidate.strike}P: "
                    f"${margin_per_contract:,.2f}/contract (${total_margin:,.2f} total for x{qty})"
                )
                return margin_per_contract

        except Exception as e:
            logger.debug(f"Error getting margin for {candidate.symbol}: {e}")

        logger.warning(
            f"Could not get actual margin for {candidate.symbol} "
            f"${candidate.strike}P — using Reg-T estimate "
            f"${candidate.margin_estimate:,.0f}"
        )
        return None

    def _build_margin_comparisons(
        self, candidates: list[StrikeCandidate]
    ) -> list[MarginComparison]:
        """Build margin comparison data for before/after display.

        Args:
            candidates: Candidates with actual margins populated

        Returns:
            List of MarginComparison objects
        """
        # Rank by estimated margin efficiency
        by_estimated = sorted(
            candidates,
            key=lambda c: (
                c.premium_income / (c.margin_estimate * c.contracts)
                if c.margin_estimate > 0 else 0
            ),
            reverse=True,
        )
        estimated_ranks = {c.symbol: rank for rank, c in enumerate(by_estimated, 1)}

        # Rank by actual margin efficiency
        by_actual = sorted(
            candidates,
            key=lambda c: self._calculate_efficiency(c),
            reverse=True,
        )
        actual_ranks = {c.symbol: rank for rank, c in enumerate(by_actual, 1)}

        # Build comparisons
        comparisons = []
        for candidate in candidates:
            est_margin = candidate.margin_estimate * candidate.contracts
            act_margin = candidate.effective_margin * candidate.contracts
            est_eff = (
                candidate.premium_income / est_margin if est_margin > 0 else 0
            )
            act_eff = (
                candidate.premium_income / act_margin if act_margin > 0 else 0
            )
            est_rank = estimated_ranks[candidate.symbol]
            act_rank = actual_ranks[candidate.symbol]

            comparisons.append(MarginComparison(
                candidate=candidate,
                estimated_margin=est_margin,
                estimated_efficiency=est_eff,
                estimated_rank=est_rank,
                actual_margin=act_margin,
                actual_efficiency=act_eff,
                actual_rank=act_rank,
                rank_shift=est_rank - act_rank,  # Positive = moved up
                margin_source=candidate.margin_source,
            ))

        # Sort by actual rank for display
        return sorted(comparisons, key=lambda c: c.actual_rank)

    def _calculate_efficiency(self, candidate: StrikeCandidate) -> float:
        """Calculate margin efficiency for a candidate.

        Efficiency = premium_income / total_margin (using actual if available)

        Args:
            candidate: The StrikeCandidate

        Returns:
            Efficiency as a decimal (e.g., 0.05 = 5%)
        """
        margin = candidate.effective_margin * candidate.contracts
        if margin <= 0:
            return 0.0
        return candidate.premium_income / margin

    def _check_constraints(
        self,
        candidate: StrikeCandidate,
        total_margin: float,
        cumulative_margin: float,
        budget: float,
        sector_counts: dict[str, int],
        selected_symbols: set[str],
        current_position_count: int,
    ) -> str | None:
        """Check if a trade violates any constraints.

        Args:
            candidate: The candidate to check
            total_margin: Margin required for this trade
            cumulative_margin: Total margin already committed
            budget: Available margin budget
            sector_counts: Current sector distribution
            selected_symbols: Symbols already selected
            current_position_count: Number of trades already selected

        Returns:
            Skip reason string if constraint violated, None if OK
        """
        # Check max positions
        if current_position_count >= self.config.max_positions:
            return f"Max positions ({self.config.max_positions}) reached"

        # Check duplicate symbol
        if candidate.symbol in selected_symbols:
            return f"Symbol {candidate.symbol} already selected"

        # Check sector concentration
        sector = candidate.sector or "Unknown"
        if sector_counts.get(sector, 0) >= self.config.max_sector_concentration:
            return (
                f"Sector {sector} at max concentration "
                f"({self.config.max_sector_concentration})"
            )

        # Check margin budget
        new_total = cumulative_margin + total_margin
        if new_total > budget:
            return (
                f"Would exceed budget "
                f"(${new_total:,.0f} > ${budget:,.0f})"
            )

        # Check 80% utilization warning threshold (still allow but flag)
        utilization = new_total / budget if budget > 0 else 0
        if utilization > self.config.max_budget_utilization:
            # This is a soft limit - we allow it but generate a warning
            pass

        return None

    def _generate_warnings(
        self,
        trades: list[StagedTrade],
        budget: float,
        cumulative_margin: float,
        sector_counts: dict[str, int],
    ) -> list[str]:
        """Generate warnings for the portfolio plan.

        Args:
            trades: Selected trades
            budget: Margin budget
            cumulative_margin: Total margin used
            sector_counts: Sector distribution

        Returns:
            List of warning strings
        """
        warnings = []

        # Check for estimated margins
        estimated_count = sum(1 for t in trades if t.margin_source == "estimated")
        if estimated_count > 0:
            warnings.append(
                f"{estimated_count} trade(s) using estimated margin — "
                "will verify at execution time"
            )

        # Check budget utilization
        utilization = cumulative_margin / budget if budget > 0 else 0
        if utilization > self.config.max_budget_utilization:
            warnings.append(
                f"Budget utilization {utilization:.1%} exceeds "
                f"{self.config.max_budget_utilization:.0%} threshold"
            )

        # Check sector concentration
        for sector, count in sector_counts.items():
            if count >= self.config.max_sector_concentration:
                warnings.append(
                    f"Sector {sector} at max concentration ({count} trades)"
                )

        return warnings

    def _empty_plan(self, budget: float) -> PortfolioPlan:
        """Create an empty portfolio plan.

        Args:
            budget: The margin budget

        Returns:
            Empty PortfolioPlan
        """
        return PortfolioPlan(
            trades=[],
            skipped_trades=[],
            margin_comparisons=[],
            total_margin_used=0.0,
            margin_budget=budget,
            margin_remaining=budget,
            total_premium_expected=0.0,
            sector_distribution={},
            warnings=["No candidates provided"],
            ibkr_connected=self._ibkr_connected,
        )

    def estimate_margin(
        self,
        stock_price: float,
        strike: float,
        premium: float,
    ) -> float:
        """Estimate margin using Reg-T formula.

        This is the fallback when IBKR whatIfOrder is unavailable.
        Note: Actual IBKR margin may be 50-100% higher for volatile stocks.

        Formula: max(
            20% of stock price - OTM amount + premium,
            10% of stock price
        ) * 100 (per contract)

        Args:
            stock_price: Current stock price
            strike: Strike price
            premium: Option premium

        Returns:
            Estimated margin requirement per contract
        """
        otm_amount = max(0, stock_price - strike)
        margin = (0.20 * stock_price - otm_amount + premium) * 100
        min_margin = 0.10 * stock_price * 100

        return max(margin, min_margin)
